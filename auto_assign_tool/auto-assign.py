#!/usr/bin/env python3
# auto-assign.py
# GUI 版 - 按歌曲元数据 album 创建目录，移动文件（分配完所有歌曲后统一更新歌曲数）

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import json
import importlib.util
import threading
import traceback
import unicodedata
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import requests
from mutagen import File
try:
    from opencc import OpenCC
except ImportError:
    OpenCC = None
try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from service_manager import MusicApiServiceManager
from single_instance import SingleInstance

os.environ["NO_PROXY"] = "127.0.0.1,localhost"

LOCAL_API = "http://127.0.0.1:3001"
# 专辑元数据会同时读取 QQ 音乐与 music-api（网易云），优先使用专辑数量更多的平台匹配。
# 本地 music-api 见 _default_timeout_for_url
REQUEST_TIMEOUT = int(os.environ.get("AUTO_ASSIGN_HTTP_TIMEOUT", "30"))
REPORT_FILE_SUFFIX = "专辑缺失报告.txt"
SETTINGS_FILE_NAME = "auto_assign_settings.json"
QQ_AUTO_ASSIGN_MODULE = None
SINGLE_INSTANCE_APP_ID = "music-api-auto-assign"
SINGLE_INSTANCE_PORT = 49231
AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".wma",
    ".ape",
    ".aiff",
    ".alac",
}
HTTP_SESSION = requests.Session()
OPENCC_T2S = OpenCC("t2s") if OpenCC else None
TRADITIONAL_TO_SIMPLIFIED_FALLBACK = str.maketrans(
    {
        "專": "专",
        "樂": "乐",
        "亞": "亚",
        "樓": "楼",
        "風": "风",
        "飛": "飞",
        "國": "国",
        "夢": "梦",
        "愛": "爱",
        "會": "会",
        "聽": "听",
        "見": "见",
        "說": "说",
        "錄": "录",
        "體": "体",
        "與": "与",
        "後": "后",
        "這": "这",
        "個": "个",
        "點": "点",
        "線": "线",
        "門": "门",
        "開": "开",
        "關": "关",
        "實": "实",
        "現": "现",
        "為": "为",
        "無": "无",
        "聲": "声",
        "時": "时",
        "經": "经",
        "萬": "万",
        "東": "东",
        "裡": "里",
        "貓": "猫",
        "龍": "龙",
        "車": "车",
        "頭": "头",
        "臺": "台",
        "灣": "湾",
        "迴": "回",
        "場": "场",
        "館": "馆",
        "廳": "厅",
        "員": "员",
        "眾": "众",
        "選": "选",
        "舉": "举",
        "區": "区",
        "際": "际",
        "級": "级",
    }
)

class AlbumMetadataDict(dict):
    def __init__(self, *args, source_summary=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.source_summary = source_summary or {}

# -----------------------
# 工具函数
# -----------------------
def sanitize_folder_name(name: str) -> str:
    """去除或替换 Windows 不允许的文件夹字符"""
    invalid_chars = r'<>:"/\|?*'
    for c in invalid_chars:
        name = name.replace(c, "_")
    return name.strip()

def normalize_cjk_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    if not text:
        return ""
    if OPENCC_T2S is not None:
        try:
            return OPENCC_T2S.convert(text)
        except Exception:
            pass
    return text.translate(TRADITIONAL_TO_SIMPLIFIED_FALLBACK)

def is_audio_file(file_path: str) -> bool:
    return os.path.splitext(file_path)[1].lower() in AUDIO_EXTENSIONS

def normalize_album_name(name: str) -> str:
    """专辑名比对键：简繁统一后去掉常见标点，避免「：」经 sanitize 变「_」与空格版不一致。"""
    t = normalize_cjk_text((name or "").strip())
    for ch in "：:·、，。．!！?？《》「」『』\"'（）—－–／\\":
        t = t.replace(ch, "")
    for c in r'<>:"/\|?*':
        t = t.replace(c, "")
    t = re.sub(r"[\s_]+", "", t)
    return t.lower()

def clean_song_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "")).strip()

def normalize_track_duration_seconds(value) -> int:
    try:
        seconds = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if seconds <= 0:
        return 0
    if seconds > 10000:
        seconds = seconds / 1000
    return int(round(seconds))

def format_track_duration(value) -> str:
    seconds = normalize_track_duration_seconds(value)
    if seconds <= 0:
        return ""
    return f"{seconds // 60:02d}:{seconds % 60:02d}"

def normalize_song_name(name: str) -> str:
    """宽松曲名：去掉括号内容，用于与「无括号」标题对齐；多首同名加括号后缀时不可用。"""
    name = normalize_cjk_text(clean_song_name(name))
    name = re.sub(r"\(.*?\)|（.*?）", "", name)
    return re.sub(r"\s+", " ", name).strip().lower()

VERSION_SUFFIX_PATTERN = re.compile(
    r"remix|mix|ver(?:sion)?|live|acoustic|demo|instrumental|inst|"
    r"伴奏|纯音乐|演奏版|钢琴版|弦乐版|合唱版|口白|对白|旁白|remaster|edit|radio|club",
    re.I,
)

def normalize_track_variant_key(name: str) -> str:
    """曲目版本比对键：只剥离末尾 Remix/口白等版本后缀，保留 Dance With Me 这类标题括号。"""
    text = normalize_cjk_text(clean_song_name(name))
    while text:
        match = re.search(r"\s*[\(（]([^()（）]*)[\)）]\s*$", text)
        if not match:
            break
        content = clean_song_name(match.group(1))
        if not content or not VERSION_SUFFIX_PATTERN.search(content):
            break
        text = text[:match.start()].strip()
    return re.sub(r"\s+", " ", text).strip().lower()

def normalize_track_title_key(name: str) -> str:
    """曲目比对用：保留 Acoustic/Remix 等括号内差异，避免多首 7 Days (…) 被当成同一首。"""
    name = normalize_cjk_text(clean_song_name(name))
    return re.sub(r"\s+", " ", name).strip().lower()

def strip_windows_duplicate_suffix(title: str) -> str:
    """去掉本地文件重名产生的末尾 (2)/(3)，不把它当作曲目版本。"""
    text = clean_song_name(title)
    return re.sub(r"\s*[\(（][2-9]\d{0,2}[\)）]\s*$", "", text).strip()

def strip_leading_artist_from_title(title: str) -> str:
    """去掉常见「歌手 - 曲名」前缀，用于本地标题/文件名包含歌手名时匹配远端曲目表。"""
    text = clean_song_name(title)
    if not text:
        return ""
    parts = re.split(r"\s+(?:-|–|—|－)\s+", text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[1].strip()
    return text

def iter_track_title_candidates(title: str):
    seen = set()
    base = clean_song_name(title)
    without_duplicate = strip_windows_duplicate_suffix(base)
    for candidate in (
        base,
        without_duplicate,
        strip_leading_artist_from_title(base),
        strip_leading_artist_from_title(without_duplicate),
    ):
        candidate = clean_song_name(candidate)
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield candidate

def iter_track_match_keys(title: str):
    seen = set()
    candidates = list(iter_track_title_candidates(title))
    for normalizer in (
        normalize_track_title_key,
        normalize_track_variant_key,
        normalize_song_name,
    ):
        for candidate in candidates:
            key = normalizer(candidate)
            if key and key not in seen:
                seen.add(key)
                yield key

def resolve_track_match_key(title: str, track_index_map: dict) -> str | None:
    """先严格键、版本键，再用无歧义宽松键，与 build_track_index_map 一致。"""
    for key in iter_track_match_keys(title):
        if key and key in track_index_map and track_index_map[key]:
            return key
    return None

def _default_timeout_for_url(url: str):
    """本地 music-api 首包/大包常超过 20s；可用环境变量覆盖。"""
    if url.startswith(LOCAL_API):
        return int(os.environ.get("AUTO_ASSIGN_LOCAL_READ_TIMEOUT", "120"))
    return REQUEST_TIMEOUT


def request_json(method: str, url: str, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = _default_timeout_for_url(url)
    local_retries = max(1, int(os.environ.get("AUTO_ASSIGN_LOCAL_RETRIES", "3")))
    attempts = local_retries if url.startswith(LOCAL_API) else 1
    for attempt in range(attempts):
        try:
            response = HTTP_SESSION.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt + 1 >= attempts:
                raise
            time.sleep(1.0 + attempt * 1.5)

def get_app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

def get_report_path(artist_name: str) -> str:
    safe_artist_name = sanitize_folder_name((artist_name or "").strip()) or "未命名歌手"
    return os.path.join(get_app_dir(), f"{safe_artist_name}-{REPORT_FILE_SUFFIX}")

def strip_artist_count_suffix(name: str) -> str:
    return re.sub(r"[（(]\d+首[）)]$", "", (name or "").strip()).strip()

def build_artist_folder_name(artist_name: str, total_songs: int) -> str:
    safe_artist_name = sanitize_folder_name((artist_name or "").strip()) or "未命名歌手"
    return f"{safe_artist_name}（{total_songs}首）"

def find_existing_artist_folder(dest_folder: str, artist_name: str):
    safe_artist_name = sanitize_folder_name((artist_name or "").strip()) or "未命名歌手"
    if not os.path.isdir(dest_folder):
        return None
    for fname in os.listdir(dest_folder):
        folder_path = os.path.join(dest_folder, fname)
        if not os.path.isdir(folder_path):
            continue
        if strip_artist_count_suffix(fname) == safe_artist_name:
            return folder_path
    return None


def _explorer_close_enabled() -> bool:
    v = (os.environ.get("AUTO_ASSIGN_CLOSE_EXPLORER") or "1").strip().lower()
    return v not in ("0", "no", "false", "off")


def try_close_explorer_windows_for_path(target_path: str, log_func=None) -> int:
    """Windows：关闭当前浏览路径恰好等于 target_path 的资源管理器窗口，减轻重命名占用。

    通过 PowerShell 调用 Shell.Application，无需 pywin32。若需禁用：环境变量 AUTO_ASSIGN_CLOSE_EXPLORER=0
    """
    if sys.platform != "win32" or not _explorer_close_enabled():
        return 0
    try:
        target_norm = os.path.normcase(os.path.abspath(target_path))
    except OSError:
        return 0

    ps_body = r"""
param([Parameter(Mandatory=$true, Position=0)][string]$TargetPath)
$ErrorActionPreference = 'SilentlyContinue'
try {
  $target = [System.IO.Path]::GetFullPath($TargetPath)
} catch {
  Write-Output 0
  exit 0
}
$closed = 0
try {
  $shell = New-Object -ComObject Shell.Application
  for ($i = $shell.Windows().Count - 1; $i -ge 0; $i--) {
    try {
      $w = $shell.Windows().Item($i)
      if ($null -eq $w.Document -or $null -eq $w.Document.Folder) { continue }
      $p = $w.Document.Folder.Self.Path
      if ([string]::IsNullOrWhiteSpace($p)) { continue }
      $fp = [System.IO.Path]::GetFullPath($p)
      $targetWithSep = $target.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
      $fpWithSep = $fp.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
      if (
        [string]::Compare($fp, $target, $true) -eq 0 -or
        $fpWithSep.StartsWith($targetWithSep, [System.StringComparison]::OrdinalIgnoreCase)
      ) {
        $w.Quit() | Out-Null
        $closed++
      }
    } catch {}
  }
} catch {}
Write-Output $closed
"""
    fd, ps_path = tempfile.mkstemp(suffix=".ps1", prefix="auto_assign_close_explorer_")
    closed = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write(ps_body)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                ps_path,
                target_norm,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=creationflags,
        )
        if proc.stdout:
            try:
                closed = int(proc.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                closed = 0
        if proc.returncode != 0 and log_func and proc.stderr:
            log_func(f"关闭资源管理器窗口（PowerShell 提示）：{proc.stderr.strip()}")
    except subprocess.TimeoutExpired:
        if log_func:
            log_func("关闭资源管理器窗口超时，将直接尝试重命名。")
    except OSError as e:
        if log_func:
            log_func(f"无法调用 PowerShell 以关闭资源管理器：{e}")
    finally:
        try:
            os.remove(ps_path)
        except OSError:
            pass

    if log_func and closed > 0:
        log_func(f"已关闭 {closed} 个正在浏览该文件夹的资源管理器窗口。")
    return closed


def rename_with_retry(src: str, dst: str, log_func=None, max_attempts: int = 12):
    """重命名文件夹；必要时先尝试关闭占用该路径的资源管理器窗口，并短暂重试。"""
    if sys.platform == "win32":
        try_close_explorer_windows_for_path(src, log_func=log_func)
        time.sleep(0.25)
    last_exc = None
    for attempt in range(max_attempts):
        try:
            os.rename(src, dst)
            return
        except (OSError, PermissionError) as e:
            last_exc = e
            if attempt + 1 >= max_attempts:
                break
            if sys.platform == "win32" and attempt == 1:
                try_close_explorer_windows_for_path(src, log_func=log_func)
                time.sleep(0.35)
            if log_func and attempt == 0:
                log_func("重命名受阻，正在自动重试（若持续失败请关闭占用该路径的程序）…")
            time.sleep(min(1.2, 0.28 * (attempt + 1)))
    if last_exc is not None:
        raise last_exc


def count_artist_audio_summary(folder_path: str):
    total = 0
    format_counts = {}
    for root, _, files in os.walk(folder_path):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in AUDIO_EXTENSIONS:
                continue
            total += 1
            key = ext.lstrip(".")
            format_counts[key] = format_counts.get(key, 0) + 1
    ordered_counts = []
    for ext in [item.lstrip(".") for item in sorted(AUDIO_EXTENSIONS)]:
        if ext in format_counts:
            ordered_counts.append((ext, format_counts.pop(ext)))
    for ext in sorted(format_counts):
        ordered_counts.append((ext, format_counts[ext]))
    return total, ordered_counts

def finalize_artist_folder(dest_folder: str, artist_name: str, artist_folder: str, log_func=None):
    total_songs, format_counts = count_artist_audio_summary(artist_folder)
    target_name = build_artist_folder_name(artist_name, total_songs)
    target_path = os.path.join(dest_folder, target_name)

    if os.path.normcase(os.path.abspath(artist_folder)) != os.path.normcase(os.path.abspath(target_path)):
        try:
            if os.path.exists(target_path):
                raise FileExistsError(f"目标目录已存在：{target_path}")
            rename_with_retry(artist_folder, target_path, log_func=log_func)
            artist_folder = target_path
        except Exception as exc:
            if log_func:
                log_func(
                    f"歌手目录重命名失败：{artist_folder} -> {target_path} ({exc})。"
                    " 请先关闭已打开该路径的资源管理器窗口，或设置 AUTO_ASSIGN_CLOSE_EXPLORER=0 后手动关闭再重试。"
                )

    if log_func:
        if format_counts:
            summary = " ".join(f"{ext} {count}首" for ext, count in format_counts)
            log_func(f"歌手目录统计：共 {total_songs} 首，{summary}")
        else:
            log_func(f"歌手目录统计：共 {total_songs} 首。")

    return artist_folder, total_songs, format_counts

def get_settings_path() -> str:
    return os.path.join(get_app_dir(), SETTINGS_FILE_NAME)

def load_gui_settings() -> dict:
    path = get_settings_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_gui_settings(data: dict):
    with open(get_settings_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def create_tray_image():
    if Image is None or ImageDraw is None:
        return None
    image = Image.new("RGBA", (64, 64), (34, 40, 49, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 12, 54, 52), fill=(66, 133, 244, 255))
    draw.rectangle((16, 18, 48, 46), fill=(255, 255, 255, 255))
    draw.rectangle((22, 24, 42, 28), fill=(66, 133, 244, 255))
    draw.rectangle((22, 32, 42, 36), fill=(66, 133, 244, 255))
    return image

def build_valid_publish_date(year: str, month: str, day: str) -> str:
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""

def format_publish_date(value) -> str:
    if value in (None, "", 0, "0"):
        return "未知日期"

    if isinstance(value, (int, float)):
        timestamp = int(value)
        # 网易云 publishTime 为毫秒。旧逻辑仅在 >1e12 时除以 1000，但 1973～2001 年的
        # 毫秒戳多为 11～12 位（约 1e11～1e12），会被误当作「秒」解析而失败或年份错乱。
        # 音乐发行日在秒级不可能达到 1e11（需到公元三千多年以后），故 >=1e11 一律按毫秒处理。
        if abs(timestamp) >= 10**11:
            timestamp //= 1000
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return str(value)

    text = str(value).strip()
    if not text:
        return "未知日期"
    if re.fullmatch(r"\d{13}", text):
        return format_publish_date(int(text))
    if re.fullmatch(r"\d{10}", text):
        return format_publish_date(int(text))
    if re.fullmatch(r"\d{8}", text):
        formatted = build_valid_publish_date(text[:4], text[4:6], text[6:8])
        return formatted or "未知日期"
    return text

def build_publish_sort_key(publish_date: str) -> str:
    """生成适合按名称排序的日期前缀，未知日期统一放到最后。"""
    text = (publish_date or "").strip()
    if not text or text == "未知日期":
        return "9999-99-99"

    match = re.search(r"(\d{4})(?:[-/.]?(\d{1,2}))?(?:[-/.]?(\d{1,2}))?", text)
    if not match:
        return "9999-99-99"

    year = match.group(1)
    month = match.group(2)
    day = match.group(3)
    if not month:
        return f"{year}-00-00"

    month = month.zfill(2)
    if not 1 <= int(month) <= 12:
        return "9999-99-99"
    if not day:
        return f"{year}-{month}-00"

    day = day.zfill(2)
    formatted = build_valid_publish_date(year, month, day)
    return formatted or "9999-99-99"

def build_album_folder_name(album_name: str, publish_date: str, song_count: int) -> str:
    safe_name = sanitize_folder_name(album_name)
    date_prefix = sanitize_folder_name(build_publish_sort_key(publish_date))
    return f"{date_prefix} {safe_name} ({song_count}首)"

def build_local_album_folder_name(album_name: str, song_count: int) -> str:
    safe_name = sanitize_folder_name(album_name)
    return f"{safe_name} ({song_count}首)"

def parse_album_folder_name(folder_name: str):
    new_match = re.match(
        r"^(?P<publish>\d{4}-\d{2}-\d{2}) (?P<album>.+?) \((?P<count>\d+)首\)$",
        folder_name,
    )
    if new_match:
        publish_date = (new_match.group("publish") or "").strip()
        return {
            "album_name": new_match.group("album").strip(),
            "publish_date": "" if publish_date == "9999-99-99" else publish_date,
            "song_count": int(new_match.group("count")),
        }

    old_match = re.match(r"^(?P<album>.+?)(?: \[(?P<publish>.+?)\])? \((?P<count>\d+)首\)$", folder_name)
    if not old_match:
        return None
    publish_text = (old_match.group("publish") or "").strip()
    # 本地整理阶段的专辑名可能本身包含方括号版本信息，如
    # “永远等待 [超越时代纪念版] (23首)”。只有像日期的方括号内容才当作发布日期。
    if publish_text and build_publish_sort_key(publish_text) == "9999-99-99":
        album_name = f"{old_match.group('album').strip()} [{publish_text}]".strip()
        publish_text = ""
    else:
        album_name = old_match.group("album").strip()
    return {
        "album_name": album_name,
        "publish_date": publish_text,
        "song_count": int(old_match.group("count")),
    }

def count_audio_files(folder_path: str) -> int:
    return len(
        [
            f for f in os.listdir(folder_path)
            if os.path.isfile(os.path.join(folder_path, f))
            and is_audio_file(os.path.join(folder_path, f))
        ]
    )

def collect_audio_files_recursive(src_folder: str):
    audio_files = []
    for root, _, files in os.walk(src_folder):
        for fname in files:
            file_path = os.path.join(root, fname)
            if os.path.isfile(file_path) and is_audio_file(file_path):
                audio_files.append(file_path)
    return audio_files

def get_album_name(file_path: str) -> str:
    """从音乐文件元数据中获取专辑名（album）。不存在返回 None"""
    try:
        if not is_audio_file(file_path):
            return None
        audio = File(file_path, easy=True)
        if audio is None:
            return None
        album = audio.get('album', [None])[0]
        if album:
            return album.strip()
        return None
    except Exception:
        return None

def get_track_title(file_path: str) -> str:
    try:
        if not is_audio_file(file_path):
            return ""
        audio = File(file_path, easy=True)
        if audio is None:
            return ""
        title = audio.get("title", [None])[0]
        if title:
            return title.strip()
    except Exception:
        pass
    return ""

def normalize_release_hint(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    match = re.search(r"(\d{4})(?:[-/.]?(\d{2}))?(?:[-/.]?(\d{2}))?", text)
    if not match:
        return text

    year = match.group(1)
    month = match.group(2)
    day = match.group(3)
    if month and day:
        return f"{year}-{month}-{day}"
    return year

def get_album_release_hint(file_path: str) -> str:
    try:
        if not is_audio_file(file_path):
            return ""
        audio = File(file_path, easy=True)
        if audio is None:
            return ""

        for key in ("date", "year", "originaldate", "originalyear"):
            value = audio.get(key, [None])[0]
            normalized = normalize_release_hint(str(value or ""))
            if normalized:
                return normalized
        return ""
    except Exception:
        return ""

def normalize_artist_name(name: str) -> str:
    name = re.sub(r"\s+", "", (name or "").strip()).lower()
    return re.sub(r"[()（）\[\]【】'\"`·._-]", "", name)

def artist_name_matches(target_name: str, candidate_name: str) -> bool:
    target = normalize_artist_name(target_name)
    candidate = normalize_artist_name(candidate_name)
    if not target or not candidate:
        return False
    return (
        target == candidate
        or candidate.startswith(target)
        or target.startswith(candidate)
    )

def track_artist_matches(target_name: str, artist_names) -> bool:
    """曲目演唱人包含目标歌手时才计入该歌手目录；缺少演唱人字段时保守保留。"""
    if not (target_name or "").strip():
        return True
    names = [str(item or "").strip() for item in (artist_names or [])]
    names = [item for item in names if item]
    if not names:
        return True
    return any(artist_name_matches(target_name, item) for item in names)

def normalize_publish_date_text(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "未知日期"
    if re.fullmatch(r"\d{13}", text) or re.fullmatch(r"\d{10}", text) or re.fullmatch(r"\d{8}", text):
        return format_publish_date(text)
    sort_key = build_publish_sort_key(text)
    return "未知日期" if sort_key == "9999-99-99" else sort_key

def get_publish_date_rank(value: str) -> int:
    sort_key = build_publish_sort_key(value)
    if sort_key == "9999-99-99":
        return 0
    if sort_key.endswith("-00-00"):
        return 1
    if sort_key.endswith("-00"):
        return 2
    return 3

def choose_better_publish_date(current_value: str, candidate_value: str) -> str:
    current = normalize_publish_date_text(current_value)
    candidate = normalize_publish_date_text(candidate_value)
    current_rank = get_publish_date_rank(current)
    candidate_rank = get_publish_date_rank(candidate)
    if current_rank > 0:
        return current
    if candidate_rank > current_rank:
        return candidate
    return current

def preserve_local_publish_date(metadata: dict, release_hint: str) -> dict:
    if get_publish_date_rank(release_hint) <= 0:
        return metadata
    updated = dict(metadata or {})
    updated["publish_date"] = normalize_publish_date_text(release_hint)
    updated["publish_date_source"] = "local_folder"
    return updated

def merge_track_titles(current_titles, candidate_titles):
    current_titles = current_titles or []
    candidate_titles = candidate_titles or []
    if len(candidate_titles) > len(current_titles):
        return candidate_titles
    return current_titles

def merge_album_metadata_entry(existing: dict, candidate: dict):
    existing["publish_date"] = choose_better_publish_date(
        existing.get("publish_date", ""),
        candidate.get("publish_date", ""),
    )
    existing_filtered = bool(existing.get("artist_filtered"))
    candidate_filtered = bool(candidate.get("artist_filtered"))
    if candidate_filtered and not existing_filtered:
        existing["track_count"] = int(candidate.get("track_count") or 0)
        existing["track_titles"] = candidate.get("track_titles") or []
    elif existing_filtered and not candidate_filtered:
        pass
    else:
        existing["track_count"] = max(
            int(existing.get("track_count") or 0),
            int(candidate.get("track_count") or 0),
        )
        existing["track_titles"] = merge_track_titles(
            existing.get("track_titles"),
            candidate.get("track_titles"),
        )
    existing["artist_filtered"] = existing_filtered or candidate_filtered
    existing_sources = list(existing.get("sources") or [])
    for source_name in candidate.get("sources") or []:
        if source_name not in existing_sources:
            existing_sources.append(source_name)
    existing["sources"] = existing_sources
    if not existing.get("name") and candidate.get("name"):
        existing["name"] = candidate.get("name")
    if candidate.get("id") and not existing.get("id"):
        existing["id"] = candidate.get("id")
    return existing

def fetch_netease_album_metadata(artist_name: str, log_func=None):
    search_result = request_json(
        "GET",
        f"{LOCAL_API}/search",
        params={"keywords": artist_name, "type": 100, "limit": 1},
    )
    artists = search_result.get("result", {}).get("artists", [])
    if not artists:
        raise ValueError(f"网易云未找到歌手：{artist_name}")

    artist_id = artists[0]["id"]
    album_result = request_json(
        "GET",
        f"{LOCAL_API}/artist/album",
        params={"id": artist_id, "limit": 1000},
    )

    metadata = {}
    raw_album_count = 0
    for album in album_result.get("hotAlbums", []):
        album_id = album.get("id")
        album_name = (album.get("name") or "").strip()
        if not album_name or not album_id:
            continue
        raw_album_count += 1
        name_key = normalize_album_name(album_name)
        detail = fetch_netease_album_detail(album_id, artist_name=artist_name)
        track_titles = detail["track_titles"]
        track_count = len(track_titles)
        # 发行日期优先用专辑详情 /album 返回的 album.publishTime（与网页专辑页一致）；
        # hotAlbums 列表里的 publishTime 有时与详情不一致，会导致「页面上是 2009、工具却是 2019」。
        publish_date = detail.get("publish_date") or format_publish_date(
            album.get("publishTime", 0)
        )

        # 按专辑 ID 分别保留，不在此处合并同名专辑。合并时取「曲目最多」会把
        # 本地持有的标准版错配成豪华版/其它区版本，导致「应有」虚高。
        key = f"netease-{album_id}"
        metadata[key] = {
            "id": album_id,
            "name": album_name,
            "name_key": name_key,
            "publish_date": publish_date,
            "track_count": track_count,
            "track_titles": track_titles,
            "artist_filtered": detail.get("artist_filtered", False),
            "sources": ["网易云"],
        }

    if log_func:
        log_func(
            f"网易云专辑元数据获取完成：共 {raw_album_count} 张，"
            f"按专辑 ID 保留 {len(metadata)} 条（同名多张互不合并，解析时再按本地曲目数匹配）。"
        )
    return metadata

def fetch_netease_album_detail(album_id: int, artist_name: str = "") -> dict:
    """请求 /album 一次，同时取曲目表与专辑详情中的发行时间。

    只计入曲目演唱人包含目标歌手的歌曲；日期以详情中的 album.publishTime 为准。
    """
    result = request_json("GET", f"{LOCAL_API}/album", params={"id": album_id})
    album_obj = result.get("album") or {}
    raw_pub = album_obj.get("publishTime", 0)
    detail_pub = format_publish_date(raw_pub)
    if detail_pub == "未知日期":
        detail_pub = ""

    songs = result.get("songs", [])
    ordered_titles = []
    artist_metadata_seen = False
    for index, song in enumerate(songs, start=1):
        title = clean_song_name(song.get("name", ""))
        if not title:
            continue
        artist_names = [
            str(item.get("name", "")).strip()
            for item in song.get("ar", [])
            if isinstance(item, dict) and item.get("name")
        ]
        if artist_names:
            artist_metadata_seen = True
        if not track_artist_matches(artist_name, artist_names):
            continue
        ordered_titles.append(
            {
                "title": title,
                "track_no": index,
                "duration": normalize_track_duration_seconds(song.get("dt", 0)),
                "artists": artist_names,
            }
        )

    return {
        "track_titles": ordered_titles,
        "publish_date": detail_pub,
        "album_name": (album_obj.get("name") or "").strip(),
        "artist_filtered": bool((artist_name or "").strip() and artist_metadata_seen),
    }

def find_album_metadata_candidates(album_metadata, album_name: str):
    album_name_key = normalize_album_name(album_name)
    return [
        (key, item)
        for key, item in album_metadata.items()
        if item.get("name_key") == album_name_key
    ]

def release_hint_matches(publish_date: str, release_hint: str) -> bool:
    publish_date = (publish_date or "").strip()
    release_hint = (release_hint or "").strip()
    if not publish_date or not release_hint:
        return False
    if publish_date == release_hint:
        return True
    if len(release_hint) == 4:
        return publish_date.startswith(release_hint)
    return release_hint in publish_date or publish_date in release_hint

def choose_best_album_candidate(candidates, local_track_count=None):
    """在多个同名候选中选一条。若给出本地文件夹内曲目数，优先匹配曲目数一致的版本。"""
    pool = list(candidates)
    if local_track_count is not None and local_track_count > 0 and pool:
        exact = [
            e for e in pool
            if int(e[1].get("track_count") or 0) == local_track_count
        ]
        if exact:
            pool = exact
        else:
            best_dist = min(
                abs(int(e[1].get("track_count") or 0) - local_track_count)
                for e in pool
            )
            pool = [
                e for e in pool
                if abs(int(e[1].get("track_count") or 0) - local_track_count) == best_dist
            ]

    def sort_key(entry):
        _, item = entry
        publish_date = item.get("publish_date", "")
        publish_rank = get_publish_date_rank(publish_date)
        track_count = int(item.get("track_count") or 0)
        track_title_count = len(item.get("track_titles") or [])
        source_count = len(item.get("sources") or [])
        return (
            publish_rank,
            track_count,
            track_title_count,
            source_count,
        )

    return max(pool, key=sort_key)

def get_candidate_sort_key(item: dict):
    return (
        get_publish_date_rank(item.get("publish_date", "")),
        int(item.get("track_count") or 0),
        len(item.get("track_titles") or []),
        len(item.get("sources") or []),
    )

def apply_local_album_name(metadata: dict, album_name: str) -> dict:
    local_album_name = clean_song_name(album_name)
    updated = dict(metadata or {})
    updated["name"] = local_album_name
    updated["name_key"] = normalize_album_name(local_album_name)
    return updated

def merge_album_metadata_candidates(candidates, album_name: str) -> dict:
    merged = None
    ordered_candidates = sorted(candidates, key=lambda entry: get_candidate_sort_key(entry[1]), reverse=True)
    for _, item in ordered_candidates:
        if merged is None:
            merged = dict(item)
            continue
        merged = merge_album_metadata_entry(merged, dict(item))
    return apply_local_album_name(merged or {}, album_name)

def resolve_bundled_file(filename: str):
    app_dir = get_app_dir()
    bundle_dir = getattr(sys, "_MEIPASS", app_dir)
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), filename),
        os.path.join(bundle_dir, filename),
        os.path.join(app_dir, filename),
        os.path.join(app_dir, "_internal", filename),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return ""

def load_qq_auto_assign_module():
    global QQ_AUTO_ASSIGN_MODULE
    if QQ_AUTO_ASSIGN_MODULE is not None:
        return QQ_AUTO_ASSIGN_MODULE

    helper_path = resolve_bundled_file("qq-auto-assign.py")
    if not helper_path:
        raise FileNotFoundError("未找到 qq-auto-assign.py，无法查询 QQ 音乐专辑信息。")

    spec = importlib.util.spec_from_file_location("qq_auto_assign_metadata_provider", helper_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    QQ_AUTO_ASSIGN_MODULE = module
    return QQ_AUTO_ASSIGN_MODULE

def fetch_qq_album_metadata(artist_name: str, log_func=None):
    provider = load_qq_auto_assign_module()
    return provider.fetch_qq_album_metadata(artist_name, log_func=log_func)

def normalize_local_album_names(local_album_names):
    names = []
    seen = set()
    for album_name in local_album_names or []:
        clean_name = clean_song_name(album_name)
        key = normalize_album_name(clean_name)
        if not clean_name or not key or key in seen:
            continue
        names.append(clean_name)
        seen.add(key)
    return names

def find_unmatched_local_album_names(album_metadata, local_album_names):
    unmatched = []
    for album_name in normalize_local_album_names(local_album_names):
        if not find_album_metadata_candidates(album_metadata, album_name):
            unmatched.append(album_name)
    return unmatched

def find_local_album_names_needing_secondary(album_metadata, local_album_names):
    needs = []
    seen = set()
    for album_name in normalize_local_album_names(local_album_names):
        key = normalize_album_name(album_name)
        if key in seen:
            continue
        seen.add(key)
        candidates = find_album_metadata_candidates(album_metadata, album_name)
        if not candidates:
            needs.append(album_name)
            continue
        if not any(get_publish_date_rank(item.get("publish_date", "")) > 0 for _, item in candidates):
            needs.append(album_name)
    return needs

def find_local_album_names_needing_netease(album_metadata, local_album_names):
    return find_local_album_names_needing_secondary(album_metadata, local_album_names)

def filter_album_metadata_by_album_names(album_metadata, album_names):
    wanted_keys = {normalize_album_name(name) for name in normalize_local_album_names(album_names)}
    if not wanted_keys:
        return {}
    return {
        key: item
        for key, item in (album_metadata or {}).items()
        if item.get("name_key") in wanted_keys
    }

def resolve_album_metadata_entry(
    album_metadata,
    artist_name: str,
    album_name: str,
    release_hint: str,
    album_folders,
    log_func=None,
    local_track_count=None,
):
    candidates = find_album_metadata_candidates(album_metadata, album_name)

    if not candidates:
        album_name_key = normalize_album_name(album_name)
        metadata = {
            "name": album_name,
            "name_key": album_name_key,
            "publish_date": "未知日期",
            "track_count": 0,
            "track_titles": [],
            "sources": [],
        }
        return album_name_key, preserve_local_publish_date(metadata, release_hint)

    if len(candidates) == 1:
        album_key, item = candidates[0]
        metadata = merge_album_metadata_candidates([(album_key, item)], album_name)
        return album_key, preserve_local_publish_date(metadata, release_hint)

    hinted_matches = [
        (key, item)
        for key, item in candidates
        if release_hint_matches(item.get("publish_date", ""), release_hint)
    ]
    if hinted_matches:
        album_key, item = choose_best_album_candidate(hinted_matches, local_track_count)
        metadata = merge_album_metadata_candidates([(album_key, item)], album_name)
        return album_key, preserve_local_publish_date(metadata, release_hint)

    existing_matches = [
        (key, item)
        for key, item in candidates
        if key in album_folders
    ]
    if existing_matches:
        album_key, item = choose_best_album_candidate(existing_matches, local_track_count)
        metadata = merge_album_metadata_candidates([(album_key, item)], album_name)
        return album_key, preserve_local_publish_date(metadata, release_hint)

    unused_candidates = [
        (key, item)
        for key, item in candidates
        if key not in album_folders
    ]
    if unused_candidates:
        chosen = choose_best_album_candidate(unused_candidates, local_track_count)
    else:
        chosen = choose_best_album_candidate(candidates, local_track_count)

    if log_func:
        dates = ", ".join(item.get("publish_date", "未知日期") for _, item in candidates)
        if release_hint:
            log_func(
                f"专辑《{album_name}》存在同名版本，未精确匹配到本地日期 {release_hint}，"
                f"已使用 {chosen[1].get('publish_date', '未知日期')}。候选发行时间：{dates}"
            )
        else:
            log_func(
                f"专辑《{album_name}》存在同名版本，缺少本地日期标签，"
                f"已使用 {chosen[1].get('publish_date', '未知日期')}。候选发行时间：{dates}"
            )
    metadata = merge_album_metadata_candidates([(chosen[0], chosen[1])], album_name)
    return chosen[0], preserve_local_publish_date(metadata, release_hint)

def choose_album_metadata_primary_source(qq_album_count: int, netease_album_count: int) -> str:
    if netease_album_count > qq_album_count:
        return "netease"
    return "qq"

def build_album_metadata_source_summary(primary_key: str, qq_count: int, netease_count: int) -> dict:
    if primary_key == "netease":
        return {
            "primary": "网易云",
            "secondary": "QQ 音乐",
            "qq_count": qq_count,
            "netease_count": netease_count,
        }
    return {
        "primary": "QQ 音乐",
        "secondary": "网易云",
        "qq_count": qq_count,
        "netease_count": netease_count,
    }

def fetch_album_metadata(artist_name: str, local_album_names=None, log_func=None):
    qq_metadata = {}
    netease_metadata = {}
    local_album_names = normalize_local_album_names(local_album_names)

    try:
        if log_func:
            log_func("正在拉取 QQ 音乐数据（歌手专辑较多时可能需要一些时间）…")
        qq_metadata = fetch_qq_album_metadata(artist_name, log_func=log_func)
    except Exception as exc:
        if log_func:
            extra = ""
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                extra = " [可设置环境变量 QQ_AUTO_ASSIGN_HTTP_TIMEOUT=60 延长 QQ 音乐接口超时]"
            log_func(f"QQ 音乐专辑元数据获取失败：{exc}{extra}")

    service_manager = MusicApiServiceManager(
        base_dir=os.path.dirname(os.path.abspath(__file__)),
        log_callback=log_func,
    )
    try:
        service_manager.ensure_running(timeout_seconds=60)
        time.sleep(1.5)
        if log_func:
            log_func("正在拉取网易云数据，用于比较两个平台的专辑数量…")
        netease_metadata = fetch_netease_album_metadata(artist_name, log_func=log_func)
    except Exception as exc:
        if log_func:
            extra = ""
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                extra = " [可设置环境变量 AUTO_ASSIGN_LOCAL_READ_TIMEOUT=180 延长本地读超时，或稍后再试]"
            log_func(f"网易云专辑元数据获取失败：{exc}{extra}")
    finally:
        service_manager.stop()

    sources = [
        {"key": "qq", "label": "QQ 音乐", "metadata": qq_metadata},
        {"key": "netease", "label": "网易云", "metadata": netease_metadata},
    ]
    primary_key = choose_album_metadata_primary_source(len(qq_metadata), len(netease_metadata))
    primary_source = next(source for source in sources if source["key"] == primary_key)
    secondary_source = next(source for source in sources if source["key"] != primary_key)
    source_summary = build_album_metadata_source_summary(
        primary_key,
        len(qq_metadata),
        len(netease_metadata),
    )

    primary_metadata = primary_source["metadata"]
    secondary_metadata = secondary_source["metadata"]
    merged_metadata = AlbumMetadataDict(source_summary=source_summary)
    merged_metadata.update(primary_metadata)

    if log_func:
        log_func(
            f"平台专辑数量对比：QQ 音乐 {len(qq_metadata)} 条，"
            f"网易云 {len(netease_metadata)} 条，优先使用 {primary_source['label']}。"
        )

    unmatched_album_names = find_unmatched_local_album_names(primary_metadata, local_album_names)
    secondary_album_names = find_local_album_names_needing_secondary(primary_metadata, local_album_names)

    if log_func and local_album_names:
        matched_count = len(local_album_names) - len(unmatched_album_names)
        log_func(
            f"{primary_source['label']}本地专辑匹配：已匹配 {matched_count} 张，"
            f"未匹配 {len(unmatched_album_names)} 张。"
        )

    if secondary_album_names:
        if log_func:
            preview = "、".join(secondary_album_names[:8])
            if len(secondary_album_names) > 8:
                preview += f" 等 {len(secondary_album_names)} 张"
            log_func(
                f"正在用{secondary_source['label']}补充"
                f"{primary_source['label']}未匹配或日期未知专辑：{preview}"
            )
        secondary_metadata = filter_album_metadata_by_album_names(
            secondary_metadata,
            secondary_album_names,
        )
        # 直接并入补充条目（每专辑唯一 key），避免同名不同 ID 被提前合并。
        merged_metadata.update(secondary_metadata)
    elif log_func and local_album_names:
        log_func(
            f"本地专辑均已在{primary_source['label']}匹配且日期可用，"
            f"无需使用{secondary_source['label']}补充。"
        )

    if log_func:
        log_func(
            f"本次已加载专辑元数据：QQ 音乐 {len(qq_metadata)} 条，"
            f"网易云 {len(netease_metadata)} 条，实际用于匹配 {len(merged_metadata)} 条。"
        )
    return merged_metadata

def strip_track_prefix(name: str) -> str:
    return re.sub(r"^\d{1,3}([.\-_ ]+)", "", name).strip()

def build_track_index_map(track_titles):
    track_index_map = {}
    loose_candidates = {}
    max_track_no = 0
    normalized_tracks = []
    strict_keys = set()

    def add_key(target_map, key, track_no):
        if not key:
            return
        values = target_map.setdefault(key, [])
        if track_no not in values:
            values.append(track_no)

    for index, item in enumerate(track_titles, start=1):
        if isinstance(item, dict):
            title = item.get("title", "")
            track_no = int(item.get("track_no") or index)
        else:
            title = item
            track_no = index
        strict_key = normalize_track_title_key(title)
        if not strict_key:
            continue
        add_key(track_index_map, strict_key, track_no)
        strict_keys.add(strict_key)
        normalized_tracks.append((title, track_no, strict_key))
        if track_no > max_track_no:
            max_track_no = track_no

    for title, track_no, strict_key in normalized_tracks:
        variant_key = normalize_track_variant_key(title)
        if variant_key and (variant_key == strict_key or variant_key not in strict_keys):
            add_key(track_index_map, variant_key, track_no)
        loose_key = normalize_song_name(title)
        if loose_key and loose_key != strict_key and loose_key not in strict_keys:
            add_key(loose_candidates, loose_key, track_no)

    for key, track_numbers in loose_candidates.items():
        if key not in track_index_map and len(track_numbers) == 1:
            track_index_map[key] = track_numbers
    return track_index_map, max_track_no

def reorder_album_files(folder_path: str, metadata, log_func=None):
    track_titles = metadata.get("track_titles") or []
    if not track_titles:
        return

    track_index_map, max_track_no = build_track_index_map(track_titles)
    files = []
    for fname in sorted(os.listdir(folder_path)):
        file_path = os.path.join(folder_path, fname)
        if not os.path.isfile(file_path) or not is_audio_file(file_path):
            continue
        title = get_track_title(file_path) or os.path.splitext(strip_track_prefix(fname))[0]
        match_key = resolve_track_match_key(title, track_index_map)
        if not match_key:
            continue
        files.append((fname, file_path, track_index_map[match_key].pop(0)))

    if not files:
        return

    width = max(2, len(str(max_track_no or len(track_titles))))
    temp_moves = []
    for seq, (fname, file_path, track_no) in enumerate(files, start=1):
        ext = os.path.splitext(fname)[1]
        base_name = strip_track_prefix(os.path.splitext(fname)[0]) or os.path.splitext(fname)[0]
        temp_name = f"__tmp_reorder__{seq:04d}{ext}"
        temp_path = os.path.join(folder_path, temp_name)
        os.rename(file_path, temp_path)
        temp_moves.append((temp_path, track_no, base_name, ext))

    used_names = set()
    for temp_path, track_no, base_name, ext in temp_moves:
        prefix = str(track_no).zfill(width)
        target_name = sanitize_folder_name(f"{prefix}. {base_name}") + ext
        candidate_name = target_name
        duplicate_index = 2
        while candidate_name in used_names or os.path.exists(os.path.join(folder_path, candidate_name)):
            candidate_name = sanitize_folder_name(f"{prefix}. {base_name} ({duplicate_index})") + ext
            duplicate_index += 1
        final_path = os.path.join(folder_path, candidate_name)
        os.rename(temp_path, final_path)
        used_names.add(candidate_name)

    if log_func:
        log_func(f"已按远端专辑顺序重排：{folder_path}")

def reorder_album_folders(album_folders, album_metadata, log_func=None):
    for album_key, folder_path in album_folders.items():
        metadata = album_metadata.get(album_key, {})
        reorder_album_files(folder_path, metadata, log_func=log_func)

def remap_album_folder_paths(album_folders, old_root: str, new_root: str):
    if os.path.normcase(os.path.abspath(old_root)) == os.path.normcase(os.path.abspath(new_root)):
        return album_folders
    remapped = {}
    for album_key, folder_path in album_folders.items():
        relative_path = os.path.relpath(folder_path, old_root)
        remapped[album_key] = os.path.join(new_root, relative_path)
    return remapped

def find_existing_album_folder(dest_folder: str, album_name: str, publish_date: str | None = None):
    target_key = normalize_album_name(album_name)
    for fname in os.listdir(dest_folder):
        fpath = os.path.join(dest_folder, fname)
        if os.path.isdir(fpath):
            folder_info = parse_album_folder_name(fname)
            if not folder_info or normalize_album_name(folder_info["album_name"]) != target_key:
                continue
            if publish_date and folder_info["publish_date"] and folder_info["publish_date"] != publish_date:
                continue
            return fpath
    return None

def create_album_folder(dest_folder: str, album_name: str, publish_date: str):
    folder_path = os.path.join(dest_folder, build_album_folder_name(album_name, publish_date, 0))
    os.makedirs(folder_path, exist_ok=True)
    return folder_path

def merge_album_folder(source_folder: str, target_folder: str, log_func=None):
    if os.path.normcase(os.path.abspath(source_folder)) == os.path.normcase(os.path.abspath(target_folder)):
        return target_folder

    os.makedirs(target_folder, exist_ok=True)
    for entry in os.listdir(source_folder):
        source_path = os.path.join(source_folder, entry)
        target_path = os.path.join(target_folder, entry)

        if os.path.isdir(source_path):
            if os.path.isdir(target_path):
                merge_album_folder(source_path, target_path, log_func=log_func)
            elif os.path.exists(target_path):
                if log_func:
                    log_func(f"跳过冲突目录：{source_path} -> {target_path}")
            else:
                shutil.move(source_path, target_path)
            continue

        if os.path.exists(target_path):
            if os.path.isdir(target_path):
                if log_func:
                    log_func(f"跳过冲突文件：{source_path} -> {target_path}")
                continue
            os.remove(target_path)
        shutil.move(source_path, target_path)

    try:
        os.rmdir(source_folder)
    except OSError:
        pass
    return target_folder

def finalize_album_folders(dest_folder: str, album_folders, album_metadata, log_func=None):
    updated = {}
    for album_key, folder_path in album_folders.items():
        metadata = album_metadata.get(album_key, {})
        album_name = metadata.get("name", album_key)
        publish_date = metadata.get("publish_date", "未知日期")
        if not os.path.isdir(folder_path):
            recovered_folder = find_existing_album_folder(dest_folder, album_name, publish_date)
            if recovered_folder:
                folder_path = recovered_folder
                if log_func:
                    log_func(f"已重新定位专辑文件夹：{album_name} → {folder_path}")
            else:
                if log_func:
                    log_func(f"跳过缺失专辑文件夹：{folder_path}")
                continue
        folder_info = parse_album_folder_name(os.path.basename(folder_path))
        local_publish_date = (folder_info or {}).get("publish_date", "")
        if get_publish_date_rank(local_publish_date) > 0:
            publish_date = normalize_publish_date_text(local_publish_date)
        num_songs = count_audio_files(folder_path)
        new_name = build_album_folder_name(album_name, publish_date, num_songs)
        new_folder_path = os.path.join(dest_folder, new_name)

        if folder_path != new_folder_path:
            try:
                if os.path.exists(new_folder_path):
                    original_folder_path = folder_path
                    folder_path = merge_album_folder(folder_path, new_folder_path, log_func=log_func)
                    if log_func:
                        log_func(f"合并重复文件夹：{original_folder_path} → {new_folder_path}")
                else:
                    rename_with_retry(folder_path, new_folder_path, log_func=log_func)
                    if log_func:
                        log_func(f"更新文件夹名称：{folder_path} → {new_folder_path}")
                    folder_path = new_folder_path
            except Exception as e:
                if log_func:
                    log_func(
                        f"重命名失败：{folder_path} -> {new_folder_path} ({e})。"
                        " 请关闭占用该专辑文件夹的窗口；或设置 AUTO_ASSIGN_CLOSE_EXPLORER=0 禁用自动关窗后手动处理。"
                    )

        updated[album_key] = folder_path

    return updated

def finalize_local_album_folders(dest_folder: str, album_folders, album_names, log_func=None):
    updated = {}
    for album_key, folder_path in album_folders.items():
        album_name = album_names.get(album_key, album_key)
        if not os.path.isdir(folder_path):
            recovered_folder = find_existing_album_folder(dest_folder, album_name)
            if recovered_folder:
                folder_path = recovered_folder
                if log_func:
                    log_func(f"已重新定位本地专辑文件夹：{album_name} → {folder_path}")
            else:
                if log_func:
                    log_func(f"跳过缺失专辑文件夹：{folder_path}")
                continue

        num_songs = count_audio_files(folder_path)
        new_name = build_local_album_folder_name(album_name, num_songs)
        new_folder_path = os.path.join(dest_folder, new_name)

        if folder_path != new_folder_path:
            try:
                if os.path.exists(new_folder_path):
                    original_folder_path = folder_path
                    folder_path = merge_album_folder(folder_path, new_folder_path, log_func=log_func)
                    if log_func:
                        log_func(f"合并重复文件夹：{original_folder_path} → {new_folder_path}")
                else:
                    rename_with_retry(folder_path, new_folder_path, log_func=log_func)
                    if log_func:
                        log_func(f"更新文件夹名称：{folder_path} → {new_folder_path}")
                    folder_path = new_folder_path
            except Exception as e:
                if log_func:
                    log_func(
                        f"重命名失败：{folder_path} -> {new_folder_path} ({e})。"
                        " 请关闭占用该专辑文件夹的窗口；或设置 AUTO_ASSIGN_CLOSE_EXPLORER=0 禁用自动关窗后手动处理。"
                    )

        updated[album_key] = folder_path

    return updated

def collect_existing_album_folders(dest_folder: str, album_metadata, album_folders=None):
    collected = {}
    used_keys = set()
    used_paths = set()

    for album_key, folder_path in (album_folders or {}).items():
        if not os.path.isdir(folder_path):
            continue
        normalized_path = os.path.normcase(os.path.abspath(folder_path))
        if normalized_path in used_paths:
            continue
        collected[album_key] = folder_path
        used_keys.add(album_key)
        used_paths.add(normalized_path)

    if not os.path.isdir(dest_folder):
        return collected

    for fname in os.listdir(dest_folder):
        folder_path = os.path.join(dest_folder, fname)
        if not os.path.isdir(folder_path):
            continue

        folder_info = parse_album_folder_name(fname)
        if not folder_info:
            continue

        candidates = find_album_metadata_candidates(album_metadata, folder_info["album_name"])
        if not candidates:
            continue

        publish_date = (folder_info.get("publish_date") or "").strip()
        matched_candidates = candidates
        if publish_date:
            exact_candidates = [
                (key, item)
                for key, item in candidates
                if release_hint_matches(item.get("publish_date", ""), publish_date)
            ]
            if exact_candidates:
                matched_candidates = exact_candidates

        chosen = None
        for key, item in matched_candidates:
            if key not in used_keys:
                chosen = (key, item)
                break
        if not chosen:
            chosen = matched_candidates[0]

        normalized_path = os.path.normcase(os.path.abspath(folder_path))
        if chosen[0] in used_keys or normalized_path in used_paths:
            continue

        collected[chosen[0]] = folder_path
        used_keys.add(chosen[0])
        used_paths.add(normalized_path)

    return collected

def collect_local_track_match_key_counts(folder_path: str) -> tuple[dict, dict]:
    """每首本地曲目的严格键计数与宽松键计数，用于与远端曲目比对。"""
    strict_keys = {}
    loose_keys = {}
    for fname in os.listdir(folder_path):
        path = os.path.join(folder_path, fname)
        if not os.path.isfile(path) or not is_audio_file(path):
            continue
        title = get_track_title(path) or os.path.splitext(strip_track_prefix(fname))[0]
        for candidate in iter_track_title_candidates(title):
            ks = normalize_track_title_key(candidate)
            kl = normalize_song_name(candidate)
            if ks:
                strict_keys[ks] = strict_keys.get(ks, 0) + 1
            if kl:
                loose_keys[kl] = loose_keys.get(kl, 0) + 1
    return strict_keys, loose_keys

def list_netease_tracks_missing_locally(folder_path: str, track_titles) -> list:
    """根据网易云曲目表，列出本地文件夹中未匹配到的曲名（用于报告）。"""
    if not track_titles:
        return []
    local_strict, local_loose = collect_local_track_match_key_counts(folder_path)
    remote_loose_counts = {}
    remote_title_counts = {}
    normalized_tracks = []
    for item in track_titles:
        if isinstance(item, dict):
            title = item.get("title", "")
            duration = item.get("duration", 0)
        else:
            title = item
            duration = 0
        title = clean_song_name(str(title or ""))
        if not title:
            continue
        strict_key = normalize_track_title_key(title)
        normalized_tracks.append(
            {
                "title": title,
                "strict_key": strict_key,
                "duration": normalize_track_duration_seconds(duration),
            }
        )
        if strict_key:
            remote_title_counts[strict_key] = remote_title_counts.get(strict_key, 0) + 1
        kl = normalize_song_name(title)
        if kl:
            remote_loose_counts[kl] = remote_loose_counts.get(kl, 0) + 1

    missing = []
    for track in normalized_tracks:
        title = track["title"]
        ks = track["strict_key"]
        kl = normalize_song_name(title)
        if ks and local_strict.get(ks, 0) > 0:
            local_strict[ks] -= 1
            continue
        if kl and remote_loose_counts.get(kl, 0) == 1 and local_loose.get(kl, 0) > 0:
            local_loose[kl] -= 1
            continue
        duration_text = (
            format_track_duration(track["duration"])
            if ks and remote_title_counts.get(ks, 0) > 1
            else ""
        )
        missing.append(f"{title} [{duration_text}]" if duration_text else title)
    return missing


def netease_duplicate_metadata_covered_locally(
    album_key: str,
    metadata: dict,
    album_folders: dict,
    album_metadata: dict,
) -> bool:
    """网易云 hotAlbums 里同一专辑可能出现多个 album_id。本地只对应其中一个 key，

    其余同名、同日期的条目不应再报「缺少整张专辑」。
    """
    name_key = metadata.get("name_key") or normalize_album_name(metadata.get("name", ""))
    if not name_key:
        return False
    sort_key = build_publish_sort_key(metadata.get("publish_date", ""))
    for other_key, folder_path in album_folders.items():
        if other_key == album_key:
            continue
        if not os.path.isdir(folder_path):
            continue
        om = album_metadata.get(other_key, {})
        onk = om.get("name_key") or normalize_album_name(om.get("name", ""))
        if onk != name_key:
            continue
        osk = build_publish_sort_key(om.get("publish_date", ""))
        if sort_key == osk:
            return True
        if sort_key == "9999-99-99" and osk == "9999-99-99":
            return True
    return False


def metadata_matches_subfolder_on_disk(metadata: dict, artist_folder: str) -> bool:
    """歌手目录下是否已有与元数据专辑名、发行日一致的子文件夹（不依赖 album_key 是否对齐）。"""
    if not artist_folder or not os.path.isdir(artist_folder):
        return False
    target_nk = metadata.get("name_key") or normalize_album_name(metadata.get("name", ""))
    if not target_nk:
        return False
    target_pub = build_publish_sort_key(metadata.get("publish_date", ""))
    for fname in os.listdir(artist_folder):
        path = os.path.join(artist_folder, fname)
        if not os.path.isdir(path):
            continue
        info = parse_album_folder_name(fname)
        if not info:
            continue
        fnk = normalize_album_name(info["album_name"])
        if fnk != target_nk:
            continue
        hint = (info.get("publish_date") or "").strip()
        folder_pub = build_publish_sort_key(hint) if hint else "9999-99-99"
        if target_pub == folder_pub:
            return True
        if target_pub == "9999-99-99" or folder_pub == "9999-99-99":
            return True
    return False


def same_title_album_folder_exists(metadata: dict, artist_folder: str) -> bool:
    """歌手目录下是否已有「同名」专辑文件夹（不要求发行日与网易某条一致）。

    同一专辑常有原版/再版/不同区服多条 album_id，日期、曲目数不同。本地已有
    「Born To Do It」任一版时，不应再报「缺整张」另一发行日（如 2001 再版）。"""
    if not artist_folder or not os.path.isdir(artist_folder):
        return False
    target_nk = metadata.get("name_key") or normalize_album_name(metadata.get("name", ""))
    if not target_nk:
        return False
    for fname in os.listdir(artist_folder):
        path = os.path.join(artist_folder, fname)
        if not os.path.isdir(path):
            continue
        info = parse_album_folder_name(fname)
        if not info:
            continue
        if normalize_album_name(info["album_name"]) == target_nk:
            return True
    return False


def verify_album_counts(album_folders, album_metadata, log_func=None, artist_folder: str | None = None):
    matched = 0
    mismatched = 0
    skipped = 0
    mismatched_items = []
    missing_album_items = []
    seen_missing_album_keys = set()

    for album_key, folder_path in album_folders.items():
        metadata = album_metadata.get(album_key, {})
        album_name = metadata.get("name") or os.path.basename(folder_path)
        expected_count = int(metadata.get("track_count") or 0)
        if not os.path.isdir(folder_path):
            if log_func:
                log_func(f"跳过校验：{album_name}，文件夹不存在。")
            skipped += 1
            continue
        actual_count = count_audio_files(folder_path)

        if expected_count <= 0:
            skipped += 1
            if log_func:
                log_func(f"跳过校验：{album_name}，未获取到专辑曲目数。")
            continue

        if actual_count == expected_count:
            matched += 1
            if log_func:
                log_func(f"校验通过：{album_name}，文件夹内 {actual_count} 首，与专辑曲目数一致。")
        else:
            mismatched += 1
            delta = actual_count - expected_count
            mismatch_type = "过多" if delta > 0 else "过少"
            missing_titles = []
            if mismatch_type == "过少" and metadata.get("track_titles"):
                missing_titles = list_netease_tracks_missing_locally(
                    folder_path, metadata.get("track_titles") or []
                )
            mismatched_items.append(
                {
                    "album_name": album_name,
                    "folder_path": folder_path,
                    "actual_count": actual_count,
                    "expected_count": expected_count,
                    "mismatch_type": mismatch_type,
                    "missing_count": max(expected_count - actual_count, 0),
                    "extra_count": max(actual_count - expected_count, 0),
                    "missing_titles": missing_titles,
                    "sources": metadata.get("sources") or [],
                }
            )
            if log_func:
                log_func(
                    f"校验失败：{album_name}，文件夹内 {actual_count} 首，"
                    f"远端专辑曲目数 {expected_count} 首。"
                )
                if missing_titles:
                    log_func("疑似缺失曲目：" + "；".join(missing_titles))

    if log_func:
        log_func(f"校验汇总：通过 {matched} 张，失败 {mismatched} 张，跳过 {skipped} 张。")

    for album_key, metadata in album_metadata.items():
        expected_count = int(metadata.get("track_count") or 0)
        if expected_count <= 0 or album_key in album_folders:
            continue
        if netease_duplicate_metadata_covered_locally(
            album_key, metadata, album_folders, album_metadata
        ):
            continue
        if artist_folder and same_title_album_folder_exists(metadata, artist_folder):
            continue
        if artist_folder and metadata_matches_subfolder_on_disk(metadata, artist_folder):
            continue
        dedupe_key = (
            metadata.get("name_key") or normalize_album_name(metadata.get("name", album_key)),
            expected_count,
            normalize_publish_date_text(metadata.get("publish_date", "")),
        )
        if dedupe_key in seen_missing_album_keys:
            continue
        seen_missing_album_keys.add(dedupe_key)
        item = {
            "album_name": metadata.get("name", album_key),
            "publish_date": metadata.get("publish_date", ""),
            "actual_count": 0,
            "expected_count": expected_count,
            "missing_count": expected_count,
            "sources": metadata.get("sources") or [],
        }
        missing_album_items.append(item)
        if log_func:
            log_func(
                f"缺少专辑文件夹：{item['album_name']}，"
                f"远端专辑曲目数 {expected_count} 首，本地未找到对应文件夹。"
            )

    if log_func and missing_album_items:
        log_func(f"缺少专辑文件夹 {len(missing_album_items)} 张。")

    return {
        "matched": matched,
        "mismatched": mismatched,
        "skipped": skipped,
        "mismatched_items": mismatched_items,
        "missing_album_items": missing_album_items,
        "metadata_source_summary": dict(getattr(album_metadata, "source_summary", {}) or {}),
    }

def build_metadata_source_report_line(verification_result) -> str:
    summary = (verification_result or {}).get("metadata_source_summary") or {}
    primary = (summary.get("primary") or "").strip()
    secondary = (summary.get("secondary") or "").strip()
    qq_count = summary.get("qq_count")
    netease_count = summary.get("netease_count")
    if primary and secondary:
        count_text = ""
        if qq_count is not None and netease_count is not None:
            count_text = f"（QQ 音乐 {qq_count} 条，网易云 {netease_count} 条）"
        return f"数据源：{primary}优先，{secondary}补充{count_text}"

    sources = []
    for item in (verification_result or {}).get("mismatched_items", []) or []:
        for source in item.get("sources") or []:
            if source and source not in sources:
                sources.append(str(source))
    for item in (verification_result or {}).get("missing_album_items", []) or []:
        for source in item.get("sources") or []:
            if source and source not in sources:
                sources.append(str(source))
    if sources:
        return "数据源：" + "、".join(sources)
    return "数据源：未获取到远端专辑元数据"

def format_missing_titles_for_report(missing_titles, limit=None) -> str:
    grouped = []
    index_by_title = {}
    for raw_title in missing_titles or []:
        title = str(raw_title or "").strip()
        if not title:
            continue
        if title in index_by_title:
            grouped[index_by_title[title]][1] += 1
            continue
        index_by_title[title] = len(grouped)
        grouped.append([title, 1])

    visible = grouped if limit is None else grouped[:limit]
    parts = [
        title if count == 1 else f"{title} x{count}"
        for title, count in visible
    ]
    if limit is not None and len(grouped) > limit:
        omitted_count = sum(count for _, count in grouped[limit:])
        parts.append(f"等{omitted_count}首")
    return "；".join(parts)

def write_album_mismatch_report(dest_folder: str, artist_name: str, verification_result, log_func=None):
    report_path = get_report_path(artist_name)
    items = verification_result.get("mismatched_items", []) if verification_result else []
    missing_albums = verification_result.get("missing_album_items", []) if verification_result else []
    lines = [
        f"歌手：{artist_name}",
        build_metadata_source_report_line(verification_result),
        f"更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    if not items and not missing_albums:
        lines.append("没有数量不符的专辑。")
    else:
        if items:
            lines.append("已存在但数量不符的专辑：")
            for item in items:
                line = f"{item['album_name']} | 实际 {item['actual_count']} / 应有 {item['expected_count']}"
                sources = item.get("sources") or []
                if sources:
                    line += " | 来源：" + "、".join(str(source) for source in sources)
                if item.get("extra_count", 0) > 0:
                    line += f" | 多出 {item['extra_count']} 首"
                missing_titles = item.get("missing_titles") or []
                if missing_titles:
                    line += " | 缺：" + format_missing_titles_for_report(missing_titles)
                lines.append(line)
            lines.append("")

        if missing_albums:
            lines.append("远端存在但本地缺少文件夹的专辑：")
            for item in missing_albums:
                line = (
                    f"{item['album_name']} | 缺少整张专辑 | "
                    f"应有 {item['expected_count']} 首"
                )
                sources = item.get("sources") or []
                if sources:
                    line += " | 来源：" + "、".join(str(source) for source in sources)
                publish_date = (item.get("publish_date") or "").strip()
                if publish_date:
                    line += f" | 发行日期 {publish_date}"
                lines.append(line)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    if log_func:
        log_func(f"已更新报告：{report_path}")
    return report_path

# -----------------------
# 移动文件函数
# -----------------------
def move_files(src_folder: str, dest_folder: str, artist_name: str, progress_callback=None, log_func=None, stop_event=None):
    if not os.path.exists(dest_folder):
        os.makedirs(dest_folder, exist_ok=True)
    artist_folder = find_existing_artist_folder(dest_folder, artist_name) or os.path.join(
        dest_folder,
        sanitize_folder_name(strip_artist_count_suffix(artist_name)) or "未命名歌手",
    )
    os.makedirs(artist_folder, exist_ok=True)

    file_list = collect_audio_files_recursive(src_folder)
    total = len(file_list)
    moved = 0
    album_folders = {}
    album_names = {}

    if total == 0:
        if log_func:
            log_func("源文件夹没有可识别的音频文件。")
        if progress_callback:
            progress_callback(0, 0, "")

    for src_path in file_list:
        if stop_event and stop_event.is_set():
            if log_func:
                log_func("已取消。")
            break

        entry = os.path.relpath(src_path, src_folder)
        file_name = os.path.basename(src_path)
        try:
            album_name = get_album_name(src_path)
            if not album_name:
                if log_func:
                    log_func(f"未找到 album 元数据，跳过：{entry}")
                continue

            local_album_name = clean_song_name(album_name)
            album_key = normalize_album_name(local_album_name)
            album_names[album_key] = local_album_name

            album_folder = album_folders.get(album_key)
            if not album_folder:
                album_folder = find_existing_album_folder(artist_folder, local_album_name)
            if not album_folder:
                album_folder = os.path.join(artist_folder, build_local_album_folder_name(local_album_name, 0))
                os.makedirs(album_folder, exist_ok=True)
            album_folders[album_key] = album_folder

            dest_path = os.path.join(album_folder, file_name)
            if os.path.exists(dest_path):
                os.remove(dest_path)

            shutil.move(src_path, dest_path)
            moved += 1

            if log_func:
                log_func(f"{entry} → {album_folder}")

        except Exception as e:
            if log_func:
                tb = traceback.format_exc()
                log_func(f"移动失败：{entry} ({e})\n{tb}")
        finally:
            if progress_callback:
                progress_callback(moved, total, entry)

    album_folders = finalize_local_album_folders(
        artist_folder,
        album_folders,
        album_names,
        log_func=log_func,
    )
    old_artist_folder = artist_folder
    artist_folder, artist_song_total, artist_format_counts = finalize_artist_folder(
        dest_folder,
        artist_name,
        artist_folder,
        log_func=log_func,
    )
    album_folders = remap_album_folder_paths(album_folders, old_artist_folder, artist_folder)

    if log_func:
        log_func("本地分配完成。可点击“获取专辑信息并重命名”继续处理发行时间、重排和校验。")
    if progress_callback:
        progress_callback(moved, total, "")
    return {
        "moved": moved,
        "total": total,
        "verification": None,
        "report_path": "",
        "artist_folder": artist_folder,
        "artist_song_total": artist_song_total,
        "artist_format_counts": artist_format_counts,
    }

def fetch_album_info_and_finalize(dest_folder: str, artist_name: str, progress_callback=None, log_func=None, stop_event=None):
    artist_folder = find_existing_artist_folder(dest_folder, artist_name) or os.path.join(
        dest_folder,
        sanitize_folder_name(strip_artist_count_suffix(artist_name)) or "未命名歌手",
    )
    if not os.path.isdir(artist_folder):
        raise FileNotFoundError(f"未找到歌手目录：{artist_folder}")

    folder_items = []
    for fname in sorted(os.listdir(artist_folder)):
        folder_path = os.path.join(artist_folder, fname)
        if not os.path.isdir(folder_path):
            continue
        folder_info = parse_album_folder_name(fname)
        if not folder_info:
            if log_func:
                log_func(f"跳过无法识别的专辑文件夹：{folder_path}")
            continue
        folder_items.append((folder_path, folder_info))

    total = len(folder_items)
    if total == 0:
        if log_func:
            log_func("歌手目录下没有可处理的专辑文件夹。")
        if progress_callback:
            progress_callback(0, 0, "")
        return {
            "moved": 0,
            "total": 0,
            "verification": None,
            "report_path": "",
            "artist_folder": artist_folder,
            "artist_song_total": 0,
            "artist_format_counts": [],
        }

    local_album_names = [
        folder_info.get("album_name", "").strip()
        for _, folder_info in folder_items
    ]
    album_metadata = fetch_album_metadata(
        artist_name,
        local_album_names=local_album_names,
        log_func=log_func,
    )

    album_folders = {}
    for index, (folder_path, folder_info) in enumerate(folder_items, start=1):
        if stop_event and stop_event.is_set():
            if log_func:
                log_func("已取消。")
            break

        album_name = folder_info.get("album_name", "").strip()
        release_hint = folder_info.get("publish_date", "").strip()
        local_track_count = count_audio_files(folder_path)
        album_key, metadata = resolve_album_metadata_entry(
            album_metadata,
            artist_name,
            album_name,
            release_hint,
            album_folders,
            log_func=log_func,
            local_track_count=local_track_count,
        )
        album_metadata[album_key] = metadata

        existing_folder = album_folders.get(album_key)
        if existing_folder and os.path.normcase(os.path.abspath(existing_folder)) != os.path.normcase(os.path.abspath(folder_path)):
            folder_path = merge_album_folder(folder_path, existing_folder, log_func=log_func)
        album_folders[album_key] = folder_path

        if progress_callback:
            progress_callback(index, total, album_name)

    album_folders = collect_existing_album_folders(artist_folder, album_metadata, album_folders)
    album_folders = finalize_album_folders(
        artist_folder,
        album_folders,
        album_metadata,
        log_func=log_func,
    )
    album_folders = collect_existing_album_folders(artist_folder, album_metadata, album_folders)
    reorder_album_folders(album_folders, album_metadata, log_func=log_func)
    old_artist_folder = artist_folder
    artist_folder, artist_song_total, artist_format_counts = finalize_artist_folder(
        dest_folder,
        artist_name,
        artist_folder,
        log_func=log_func,
    )
    album_folders = remap_album_folder_paths(album_folders, old_artist_folder, artist_folder)
    verification_result = verify_album_counts(
        album_folders,
        album_metadata,
        log_func=log_func,
        artist_folder=artist_folder,
    )
    report_path = write_album_mismatch_report(dest_folder, artist_name, verification_result, log_func=log_func)

    if log_func:
        log_func("专辑信息处理完成。")
    if progress_callback:
        progress_callback(total, total, "")
    return {
        "moved": total,
        "total": total,
        "verification": verification_result,
        "report_path": report_path,
        "artist_folder": artist_folder,
        "artist_song_total": artist_song_total,
        "artist_format_counts": artist_format_counts,
    }

# -----------------------
# GUI
# -----------------------
class MoveGui:
    def __init__(self, root):
        self.root = root
        self.root.title("音乐整理工具")
        self.root.geometry("980x720")
        self.root.minsize(820, 560)
        self.settings = load_gui_settings()

        self.src_var = tk.StringVar(value=self.settings.get("default_src", ""))
        self.dest_var = tk.StringVar(value=self.settings.get("default_dest", ""))
        self.artist_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")
        self.progress_percent_var = tk.StringVar(value="0%")
        self.progress_count_var = tk.StringVar(value="0 / 0")
        self.mismatch_paths = []
        self.report_path = ""
        self.current_artist_folder = ""
        self.tray_icon = None
        self.tray_thread = None
        self.is_hidden_to_tray = False
        self.is_quitting = False

        self._make_widgets()
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)

    def _make_widgets(self):
        frm = ttk.Frame(self.root, padding=16)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frm,
            text="音乐整理工具",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            frm,
            text="按文件元数据中的专辑名自动分组、移动并校验专辑曲目数量",
        ).pack(anchor=tk.W, pady=(4, 0))

        path_frame = ttk.LabelFrame(frm, text="基础信息", padding=12)
        path_frame.pack(fill=tk.X, pady=(14, 0))
        path_frame.columnconfigure(1, weight=1)

        ttk.Label(path_frame, text="歌手名").grid(row=0, column=0, sticky=tk.W, padx=(0, 10), pady=(0, 8))
        ttk.Entry(path_frame, textvariable=self.artist_var).grid(row=0, column=1, columnspan=2, sticky=tk.EW, pady=(0, 8))

        ttk.Label(path_frame, text="源文件夹").grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=(0, 8))
        ttk.Entry(path_frame, textvariable=self.src_var).grid(row=1, column=1, sticky=tk.EW, pady=(0, 8))
        ttk.Button(path_frame, text="浏览", command=self.browse_src).grid(row=1, column=2, padx=(10, 0), pady=(0, 8))

        ttk.Label(path_frame, text="目标文件夹").grid(row=2, column=0, sticky=tk.W, padx=(0, 10))
        ttk.Entry(path_frame, textvariable=self.dest_var).grid(row=2, column=1, sticky=tk.EW)
        ttk.Button(path_frame, text="浏览", command=self.browse_dest).grid(row=2, column=2, padx=(10, 0))

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill=tk.X, pady=(12, 0))

        self.start_btn = ttk.Button(btn_frame, text="开始整理", command=self.on_start)
        self.start_btn.pack(side=tk.LEFT)

        self.fetch_info_btn = ttk.Button(
            btn_frame,
            text="获取专辑信息并重命名",
            command=self.on_fetch_album_info,
        )
        self.fetch_info_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.cancel_btn = ttk.Button(btn_frame, text="取消", command=self.on_cancel)
        self.cancel_btn.pack(side=tk.LEFT, padx=(8, 0))
        self.cancel_btn.state(['disabled'])

        ttk.Button(btn_frame, text="保存默认路径", command=self.save_default_paths).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frame, text="打开目标文件夹", command=self.open_dest_folder).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frame, text="打开报告", command=self.open_report_file).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btn_frame, text="清空日志", command=self.clear_log).pack(side=tk.RIGHT)

        progress_frame = ttk.LabelFrame(frm, text="执行进度", padding=12)
        progress_frame.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(progress_frame, text="当前进度").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        self.progressbar = ttk.Progressbar(progress_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progressbar.grid(row=0, column=1, sticky=tk.EW)
        progress_frame.columnconfigure(1, weight=1)
        ttk.Label(progress_frame, textvariable=self.progress_percent_var, width=8, anchor=tk.E).grid(row=0, column=2, padx=(10, 0))
        ttk.Label(progress_frame, textvariable=self.progress_count_var, width=12, anchor=tk.E).grid(row=0, column=3, padx=(10, 0))

        content = ttk.Panedwindow(frm, orient=tk.VERTICAL)
        content.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        result_frame = ttk.LabelFrame(content, text="数量异常专辑", padding=10)
        log_frame = ttk.LabelFrame(content, text="执行日志", padding=10)
        content.add(result_frame, weight=2)
        content.add(log_frame, weight=3)

        result_body = ttk.Frame(result_frame)
        result_body.pack(fill=tk.BOTH, expand=True)
        self.result_listbox = tk.Listbox(result_body, height=8)
        result_vbar = ttk.Scrollbar(result_body, orient=tk.VERTICAL, command=self.result_listbox.yview)
        self.result_listbox.config(yscrollcommand=result_vbar.set)
        self.result_listbox.grid(row=0, column=0, sticky="nsew")
        result_vbar.grid(row=0, column=1, sticky="ns")
        result_body.columnconfigure(0, weight=1)
        result_body.rowconfigure(0, weight=1)
        self.result_listbox.bind("<Double-Button-1>", self.open_selected_mismatch_path)

        result_action_frame = ttk.Frame(result_frame)
        result_action_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(
            result_action_frame,
            text="打开选中路径",
            command=self.open_selected_mismatch_path,
        ).pack(side=tk.LEFT)

        log_body = ttk.Frame(log_frame)
        log_body.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_body, wrap=tk.NONE, height=18, state=tk.DISABLED, font=("Consolas", 10))
        vbar = ttk.Scrollbar(log_body, orient=tk.VERTICAL, command=self.log_text.yview)
        hbar = ttk.Scrollbar(log_body, orient=tk.HORIZONTAL, command=self.log_text.xview)
        self.log_text.config(yscrollcommand=vbar.set, xscrollcommand=hbar.set)

        self.log_text.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        log_body.columnconfigure(0, weight=1)
        log_body.rowconfigure(0, weight=1)

        status_frame = ttk.Frame(self.root)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT, padx=8, pady=6)

    def browse_src(self):
        path = filedialog.askdirectory(title="选择源文件夹")
        if path:
            self.src_var.set(path)

    def browse_dest(self):
        path = filedialog.askdirectory(title="选择目标文件夹")
        if path:
            self.dest_var.set(path)

    def save_default_paths(self):
        self.settings["default_src"] = self.src_var.get().strip()
        self.settings["default_dest"] = self.dest_var.get().strip()
        save_gui_settings(self.settings)
        self.status_var.set("默认路径已保存")

    def log(self, msg):
        def _append():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)
        self.root.after(0, _append)

    def clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_results(self):
        self.mismatch_paths = []
        self.result_listbox.delete(0, tk.END)

    def render_mismatch_results(self, verification_result):
        self.clear_results()
        if not verification_result:
            return

        items = verification_result.get("mismatched_items", [])
        if not items:
            self.result_listbox.insert(tk.END, "没有数量异常的专辑文件夹。")
            return

        for item in items:
            display_text = (
                f"[{item['mismatch_type']}] {item['album_name']} | "
                f"实际 {item['actual_count']} / 应有 {item['expected_count']} | "
                f"{item['folder_path']}"
            )
            missing_titles = item.get("missing_titles") or []
            if missing_titles:
                display_text += " | 缺：" + format_missing_titles_for_report(missing_titles, limit=8)
            self.mismatch_paths.append(item["folder_path"])
            self.result_listbox.insert(tk.END, display_text)

    def get_selected_mismatch_path(self):
        selection = self.result_listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if index >= len(self.mismatch_paths):
            return None
        return self.mismatch_paths[index]

    def open_path_in_explorer(self, path):
        if not path:
            messagebox.showinfo("提示", "请先选择一条异常专辑路径。")
            return
        if not os.path.exists(path):
            messagebox.showerror("错误", f"路径不存在：{path}")
            return
        try:
            subprocess.Popen(["explorer", os.path.normpath(path)])
        except Exception as exc:
            messagebox.showerror("错误", f"打开资源管理器失败：{exc}")

    def open_selected_mismatch_path(self, _event=None):
        self.open_path_in_explorer(self.get_selected_mismatch_path())

    def open_dest_folder(self):
        dest = self.current_artist_folder or self.dest_var.get().strip()
        if not dest:
            messagebox.showinfo("提示", "目标文件夹为空。")
            return
        self.open_path_in_explorer(dest)

    def open_report_file(self):
        report_path = self.report_path.strip() if self.report_path else ""
        if not report_path:
            report_path = get_report_path(self.artist_var.get().strip())
        if not report_path:
            messagebox.showinfo("提示", "报告路径为空。")
            return
        if not os.path.exists(report_path):
            messagebox.showerror("错误", f"报告不存在：{report_path}")
            return
        try:
            os.startfile(os.path.normpath(report_path))
        except Exception as exc:
            messagebox.showerror("错误", f"打开报告失败：{exc}")

    def ensure_tray_icon(self):
        if pystray is None or self.tray_icon is not None:
            return
        image = create_tray_image()
        if image is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("显示窗口", lambda icon, item: self.root.after(0, self.show_window), default=True),
            pystray.MenuItem("退出", lambda icon, item: self.root.after(0, self.quit_app)),
        )
        self.tray_icon = pystray.Icon("auto_assign_gui", image, "音乐整理工具", menu)
        self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()

    def hide_to_tray(self):
        self.ensure_tray_icon()
        if self.tray_icon is None:
            self.root.iconify()
            return
        self.is_hidden_to_tray = True
        self.root.withdraw()
        self.tray_icon.visible = True

    def show_window(self):
        self.is_hidden_to_tray = False
        self.root.state("normal")
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass
        try:
            self.root.focus_force()
        except Exception:
            pass
        if self.tray_icon is not None:
            self.tray_icon.visible = False

    def on_close_window(self):
        if self.is_quitting:
            return
        self.hide_to_tray()

    def quit_app(self):
        self.is_quitting = True
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        self.root.destroy()

    def progress_callback(self, moved, total, current_file):
        pct = int(moved / total * 100) if total else 0

        def _update():
            self.progressbar['value'] = pct
            self.progress_percent_var.set(f"{pct}%")
            self.progress_count_var.set(f"{moved} / {total}")
            if current_file:
                self.status_var.set(f"正在处理：{current_file} ({moved}/{total})")
            else:
                self.status_var.set("就绪" if moved == total else "已取消")

        self.root.after(0, _update)

    def reset_progress(self):
        self.progressbar['value'] = 0
        self.progress_percent_var.set("0%")
        self.progress_count_var.set("0 / 0")

    def set_running_state(self, running: bool):
        if running:
            self.start_btn.state(['disabled'])
            self.fetch_info_btn.state(['disabled'])
            self.cancel_btn.state(['!disabled'])
        else:
            self.start_btn.state(['!disabled'])
            self.fetch_info_btn.state(['!disabled'])
            self.cancel_btn.state(['disabled'])

    def get_artist_folder_path(self, artist_name: str, dest_folder: str) -> str:
        return find_existing_artist_folder(dest_folder, artist_name) or os.path.join(
            dest_folder,
            sanitize_folder_name(strip_artist_count_suffix(artist_name)) or "未命名歌手",
        )

    def count_processible_album_folders(self, artist_folder: str) -> int:
        count = 0
        for fname in os.listdir(artist_folder):
            folder_path = os.path.join(artist_folder, fname)
            if not os.path.isdir(folder_path):
                continue
            if parse_album_folder_name(fname):
                count += 1
        return count

    def on_start(self):
        artist_name = self.artist_var.get().strip()
        src = self.src_var.get().strip()
        dest = self.dest_var.get().strip()

        if not artist_name:
            messagebox.showwarning("歌手名未指定", "请输入歌手名，用于创建歌手目录和后续获取专辑信息。")
            return

        if not src or not dest:
            messagebox.showwarning("路径未指定", "请先选择源文件夹和目标文件夹。")
            return

        if not os.path.isdir(src):
            messagebox.showerror("错误", "源路径无效。")
            return

        self.reset_progress()
        self.clear_log()
        self.clear_results()
        self.report_path = ""
        self.stop_event.clear()
        self.current_artist_folder = self.get_artist_folder_path(artist_name, dest)

        self.set_running_state(True)
        self.status_var.set("正在按本地专辑名分配文件...")

        self.worker_thread = threading.Thread(
            target=self._worker_move,
            args=(artist_name, src, dest),
            daemon=True
        )
        self.worker_thread.start()

    def on_fetch_album_info(self):
        artist_name = self.artist_var.get().strip()
        dest = self.dest_var.get().strip()

        if not artist_name:
            messagebox.showwarning("歌手名未指定", "请输入歌手名。")
            return

        if not dest:
            messagebox.showwarning("路径未指定", "请先选择目标文件夹。")
            return

        artist_folder = self.get_artist_folder_path(artist_name, dest)
        if not os.path.isdir(artist_folder):
            messagebox.showerror("错误", f"未找到歌手目录：{artist_folder}")
            return

        album_count = self.count_processible_album_folders(artist_folder)
        if album_count <= 0:
            messagebox.showinfo("提示", "当前歌手目录下没有可处理的专辑文件夹。")
            return

        confirmed = messagebox.askyesno(
            "确认获取专辑信息",
            f"本次将处理 {album_count} 张专辑。\n\n继续获取专辑信息并执行重命名、重排和校验吗？",
        )
        if not confirmed:
            return

        self.reset_progress()
        self.clear_log()
        self.clear_results()
        self.report_path = ""
        self.stop_event.clear()
        self.current_artist_folder = artist_folder

        self.set_running_state(True)
        self.status_var.set("正在获取专辑信息并重命名...")

        self.worker_thread = threading.Thread(
            target=self._worker_fetch_album_info,
            args=(artist_name, dest),
            daemon=True
        )
        self.worker_thread.start()

    def on_cancel(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.stop_event.set()
            self.log("正在尝试取消，请稍候...")
            self.status_var.set("取消中...")

    def _worker_move(self, artist_name, src, dest):
        try:
            result = move_files(
                src,
                dest,
                artist_name,
                progress_callback=self.progress_callback,
                log_func=self.log,
                stop_event=self.stop_event
            )
            self.report_path = result.get("report_path", "")
            self.current_artist_folder = result.get("artist_folder", self.current_artist_folder)
            self.root.after(0, lambda: self.render_mismatch_results(result.get("verification")))
        except Exception as e:
            self.log(f"发生异常：{e}")
            self.log(traceback.format_exc())
        finally:
            self.root.after(0, self._finish_worker)

    def _worker_fetch_album_info(self, artist_name, dest):
        try:
            result = fetch_album_info_and_finalize(
                dest,
                artist_name,
                progress_callback=self.progress_callback,
                log_func=self.log,
                stop_event=self.stop_event
            )
            self.report_path = result.get("report_path", "")
            self.current_artist_folder = result.get("artist_folder", self.current_artist_folder)
            self.root.after(0, lambda: self.render_mismatch_results(result.get("verification")))
        except Exception as e:
            self.log(f"发生异常：{e}")
            self.log(traceback.format_exc())
        finally:
            self.root.after(0, self._finish_worker)

    def _finish_worker(self):
        self.set_running_state(False)
        self.status_var.set("就绪")

# -----------------------
# 主函数
# -----------------------
def main():
    instance = SingleInstance(SINGLE_INSTANCE_APP_ID, SINGLE_INSTANCE_PORT)
    if not instance.acquire():
        instance.notify_existing()
        return

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        try:
            style.theme_use('clam')
            style.configure("TLabel", font=("Segoe UI", 10))
            style.configure("TButton", font=("Segoe UI", 10))
            style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        except Exception:
            pass
        app = MoveGui(root)
        instance.set_show_callback(lambda: root.after(0, app.show_window))
        root.mainloop()
    finally:
        instance.close()

if __name__ == "__main__":
    main()
