#!/usr/bin/env python3
# qq-auto-assign.py
# GUI 版 - QQ 音乐专辑元数据版。整理/重命名/校验逻辑复用 auto-assign.py，
# 但不修改网易云版本，QQ 音乐接口与设置、报告文件都单独维护。

import importlib.util
import base64
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime
from urllib.parse import quote, urlparse
import urllib.request

import requests


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_SCRIPT = os.path.join(BASE_DIR, "auto-assign.py")

spec = importlib.util.spec_from_file_location("auto_assign_base", BASE_SCRIPT)
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


QQ_MUSICU_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
QQ_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
QQ_SMARTBOX_URL = "https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg"
QQ_REFERER = "https://y.qq.com/"
QQ_SOURCE_NAME = "QQ音乐"
QQ_REQUEST_TIMEOUT = int(os.environ.get("QQ_AUTO_ASSIGN_HTTP_TIMEOUT", "30"))
QQ_PAGE_SIZE = int(os.environ.get("QQ_AUTO_ASSIGN_PAGE_SIZE", "80"))
QQ_COOKIE_FILE_NAME = "qq_music_cookie.txt"
QQ_LOGIN_PROFILE_DIR = "qq_music_login_profile"
QQ_LOGIN_URL = "https://y.qq.com/"
QQ_CDP_READY_TIMEOUT = int(os.environ.get("QQ_AUTO_ASSIGN_CDP_READY_TIMEOUT", "25"))

base.REPORT_FILE_SUFFIX = "QQ音乐专辑缺失报告.txt"
base.SETTINGS_FILE_NAME = "qq_auto_assign_settings.json"

QQ_SESSION = requests.Session()
QQ_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": QQ_REFERER,
        "Origin": "https://y.qq.com",
        "Accept": "application/json, text/plain, */*",
    }
)


def get_qq_cookie() -> str:
    """优先读取环境变量，其次读取脚本同目录的 qq_music_cookie.txt。"""
    cookie = os.environ.get("QQ_MUSIC_COOKIE", "").strip()
    if cookie:
        return cookie

    cookie_file = os.environ.get(
        "QQ_MUSIC_COOKIE_FILE",
        get_qq_cookie_file_path(),
    )
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as file:
            return file.read().strip()
    return ""


def get_qq_cookie_file_path() -> str:
    return os.environ.get(
        "QQ_MUSIC_COOKIE_FILE",
        os.path.join(base.get_app_dir(), QQ_COOKIE_FILE_NAME),
    )


def write_qq_cookie(cookie_header: str) -> str:
    path = get_qq_cookie_file_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write((cookie_header or "").strip() + "\n")
    return path


def find_browser_executable() -> str:
    configured = os.environ.get("QQ_AUTO_ASSIGN_BROWSER", "").strip()
    if configured and os.path.exists(configured):
        return configured

    names = ["msedge", "chrome", "chrome.exe", "msedge.exe"]
    for name in names:
        path = shutil.which(name)
        if path:
            return path

    env_paths = [
        ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
        ("PROGRAMFILES", r"Google\Chrome\Application\chrome.exe"),
        ("PROGRAMFILES(X86)", r"Google\Chrome\Application\chrome.exe"),
        ("PROGRAMFILES", r"Microsoft\Edge\Application\msedge.exe"),
        ("PROGRAMFILES(X86)", r"Microsoft\Edge\Application\msedge.exe"),
        ("LOCALAPPDATA", r"Microsoft\Edge\Application\msedge.exe"),
    ]
    for env_name, suffix in env_paths:
        root = os.environ.get(env_name, "")
        if not root:
            continue
        path = os.path.join(root, suffix)
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        "未找到 Chrome 或 Edge。可设置环境变量 QQ_AUTO_ASSIGN_BROWSER 指向浏览器 exe。"
    )


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_login_profile_dir(port: int) -> str:
    root = os.path.join(base.get_app_dir(), QQ_LOGIN_PROFILE_DIR)
    os.makedirs(root, exist_ok=True)
    profile_dir = os.path.join(root, f"session-{int(time.time())}-{port}")
    os.makedirs(profile_dir, exist_ok=True)
    return profile_dir


def launch_qq_login_browser() -> tuple[subprocess.Popen, int, str]:
    browser = find_browser_executable()
    port = find_free_port()
    profile_dir = make_login_profile_dir(port)
    proc = subprocess.Popen(
        [
            browser,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            QQ_LOGIN_URL,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_cdp(port, timeout_seconds=QQ_CDP_READY_TIMEOUT, proc=proc)
    except Exception:
        stop_login_browser(proc)
        raise
    return proc, port, profile_dir


def cdp_http_json(port: int, path: str) -> dict | list:
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_cdp(port: int, timeout_seconds: int = 15, proc: subprocess.Popen | None = None) -> dict:
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(
                "登录浏览器已退出，调试端口无法连接。"
                " 请确认 Chrome/Edge 可以正常启动；必要时设置 QQ_AUTO_ASSIGN_BROWSER 指向浏览器 exe。"
            )
        try:
            return cdp_http_json(port, "/json/version")
        except Exception as exc:
            last_error = exc
            time.sleep(0.3)
    raise RuntimeError(
        f"浏览器调试端口未就绪：{last_error}。"
        " 请关闭刚打开的登录窗口后重试；如果仍失败，设置 QQ_AUTO_ASSIGN_BROWSER 指向 chrome.exe 或 msedge.exe。"
    )


class MinimalWebSocket:
    def __init__(self, ws_url: str):
        parsed = urlparse(ws_url)
        if parsed.scheme != "ws":
            raise ValueError(f"仅支持本地 ws 调试地址：{ws_url}")
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        if parsed.query:
            self.path += "?" + parsed.query
        self.sock = socket.create_connection((self.host, self.port), timeout=5)
        self._handshake()

    def _handshake(self):
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError("浏览器 WebSocket 握手失败")

    def _read_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining > 0:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise RuntimeError("浏览器 WebSocket 连接已关闭")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def send_json(self, payload: dict):
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(bytes(header) + mask + masked)

    def recv_json(self) -> dict:
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 8:
                raise RuntimeError("浏览器 WebSocket 已关闭")
            if opcode == 9:
                self._send_pong(payload)

    def _send_pong(self, payload: bytes):
        header = bytearray([0x8A])
        length = len(payload)
        header.append(0x80 | length)
        mask = secrets.token_bytes(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def request(self, method: str, params: dict | None = None, command_id: int = 1) -> dict:
        self.send_json({"id": command_id, "method": method, "params": params or {}})
        while True:
            message = self.recv_json()
            if message.get("id") == command_id:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result") or {}

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def cdp_request(ws_url: str, method: str, params: dict | None = None) -> dict:
    ws = MinimalWebSocket(ws_url)
    try:
        return ws.request(method, params=params)
    finally:
        ws.close()


def get_cdp_cookies(port: int, proc: subprocess.Popen | None = None) -> list[dict]:
    version = wait_for_cdp(port, proc=proc)
    browser_ws = version.get("webSocketDebuggerUrl")
    errors = []
    cookie_urls = [
        "https://y.qq.com/",
        "https://u.y.qq.com/",
        "https://c.y.qq.com/",
        "https://qq.com/",
    ]
    if browser_ws:
        for method, params in (
            ("Storage.getCookies", {}),
            ("Network.getCookies", {"urls": cookie_urls}),
            ("Network.getAllCookies", {}),
        ):
            try:
                result = cdp_request(browser_ws, method, params=params)
                cookies = result.get("cookies") or []
                if cookies:
                    return cookies
            except Exception as exc:
                errors.append(str(exc))

    pages = cdp_http_json(port, "/json/list")
    qq_pages = [
        page for page in pages
        if page.get("type") == "page" and "qq.com" in str(page.get("url") or "")
    ]
    other_pages = [
        page for page in pages
        if page not in qq_pages and page.get("type") == "page"
    ]
    for page in qq_pages + other_pages:
        page_ws = page.get("webSocketDebuggerUrl")
        if not page_ws:
            continue
        for method, params in (
            ("Network.getCookies", {"urls": cookie_urls}),
            ("Storage.getCookies", {}),
            ("Network.getAllCookies", {}),
        ):
            try:
                result = cdp_request(page_ws, method, params=params)
                cookies = result.get("cookies") or []
                if cookies:
                    return cookies
            except Exception as exc:
                errors.append(str(exc))

    raise RuntimeError("未能从登录浏览器读取 Cookie：" + "；".join(errors[-3:]))


def build_cookie_header_from_cdp(cookies: list[dict]) -> tuple[str, list[str]]:
    qq_cookies = []
    seen = set()
    for cookie in cookies:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if not name or value == "":
            continue
        if not (domain == "qq.com" or domain.endswith(".qq.com") or domain.endswith("y.qq.com")):
            continue
        if name in seen:
            continue
        seen.add(name)
        qq_cookies.append((name, value))
    cookie_header = "; ".join(f"{name}={value}" for name, value in qq_cookies)
    return cookie_header, [name for name, _ in qq_cookies]


def qq_cookie_has_login_marker(cookie_names: list[str]) -> bool:
    names = set(cookie_names)
    account_markers = {"uin", "wxuin", "euin", "psrf_qqopenid", "psrf_qqunionid"}
    token_markers = {"qm_keyst", "qqmusic_key", "psrf_qqaccess_token", "psrf_access_token_expiresAt"}
    return bool(names & account_markers) and bool(names & token_markers)


def collect_and_save_qq_cookie(port: int, proc: subprocess.Popen | None = None) -> tuple[str, list[str]]:
    cookies = get_cdp_cookies(port, proc=proc)
    cookie_header, cookie_names = build_cookie_header_from_cdp(cookies)
    if not cookie_header:
        raise RuntimeError("未读取到 QQ 音乐相关 Cookie，请确认登录窗口已打开 QQ 音乐。")
    path = write_qq_cookie(cookie_header)
    return path, cookie_names


def stop_login_browser(proc: subprocess.Popen | None):
    if proc is None:
        return
    try:
        proc.terminate()
    except Exception:
        pass


def build_qq_headers(referer: str | None = None) -> dict:
    headers = {
        "Referer": referer or QQ_REFERER,
        "Origin": "https://y.qq.com",
    }
    cookie = get_qq_cookie()
    if cookie:
        headers["Cookie"] = cookie
    return headers


def parse_qq_response(response: requests.Response) -> dict:
    response.raise_for_status()
    text = response.text.strip()
    if not text:
        return {}
    try:
        return response.json()
    except ValueError:
        # 部分老接口可能返回 callback({...}) 形式。
        match = re.match(r"^[\w$]+\((.*)\)\s*;?$", text, re.S)
        if not match:
            raise
        return json.loads(match.group(1))


def qq_get_json(url: str, params: dict, referer: str | None = None) -> dict:
    params = dict(params or {})
    params.setdefault("format", "json")
    params.setdefault("inCharset", "utf8")
    params.setdefault("outCharset", "utf-8")
    params.setdefault("g_tk", "5381")
    response = QQ_SESSION.get(
        url,
        params=params,
        headers=build_qq_headers(referer),
        timeout=QQ_REQUEST_TIMEOUT,
    )
    return parse_qq_response(response)


def qq_musicu_request(payload: dict, referer: str | None = None) -> dict:
    params = {
        "format": "json",
        "inCharset": "utf8",
        "outCharset": "utf-8",
        "data": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }
    return qq_get_json(QQ_MUSICU_URL, params, referer=referer)


def first_value(obj: dict, keys: list[str], default=None):
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return default


def to_int(value, default=0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_qq_publish_date(value) -> str:
    value = first_value(value, ["date"], "") if isinstance(value, dict) else value
    publish_date = base.format_publish_date(value)
    if publish_date == "未知日期":
        return ""
    return publish_date


def looks_like_qq_singer_mid(value: str) -> bool:
    text = (value or "").strip()
    if text.lower().startswith("mid:"):
        return True
    return bool(re.fullmatch(r"[0-9A-Za-z_-]{10,}", text))


def search_qq_singers(keyword: str) -> list[dict]:
    search_referer = f"https://y.qq.com/n/ryqq/search?w={quote(keyword)}"
    singers = []
    smartbox = qq_get_json(
        QQ_SMARTBOX_URL,
        {
            "key": keyword,
        },
        referer=search_referer,
    )
    smartbox_singers = (((smartbox.get("data") or {}).get("singer") or {}).get("itemlist") or [])
    for item in smartbox_singers:
        mid = first_value(item, ["mid", "singermid", "singer_mid"], "")
        name = first_value(item, ["name", "singer"], "")
        if mid and name:
            singers.append({"mid": str(mid).strip(), "name": str(name).strip(), "raw": item})

    if singers:
        return singers

    try:
        result = qq_get_json(
            QQ_SEARCH_URL,
            {
                "w": keyword,
                "p": 1,
                "n": 20,
                "cr": 1,
                "t": 9,
                "remoteplace": "txt.yqq.singer",
            },
            referer=search_referer,
        )
    except requests.RequestException:
        return []

    singer_data = (result.get("data") or {}).get("singer") or {}
    legacy_singers = singer_data.get("list") or []

    normalized = []
    for item in legacy_singers:
        mid = first_value(
            item,
            [
                "singermid",
                "singerMID",
                "singerMid",
                "singer_mid",
                "mid",
                "Fsinger_mid",
            ],
            "",
        )
        name = first_value(
            item,
            [
                "singername",
                "singerName",
                "name",
                "Fsinger_name",
                "singer_name",
            ],
            "",
        )
        if mid and name:
            normalized.append({"mid": str(mid).strip(), "name": str(name).strip(), "raw": item})
    return normalized


def resolve_qq_singer(artist_name: str, log_func=None) -> dict:
    artist_name = (artist_name or "").strip()
    if not artist_name:
        raise ValueError("歌手名为空")

    if artist_name.lower().startswith("mid:"):
        return {"mid": artist_name.split(":", 1)[1].strip(), "name": artist_name}
    if looks_like_qq_singer_mid(artist_name):
        return {"mid": artist_name, "name": artist_name}

    singers = search_qq_singers(artist_name)
    if not singers:
        raise ValueError(f"QQ音乐未找到歌手：{artist_name}")

    target_key = base.normalize_artist_name(artist_name)
    exact = [
        singer
        for singer in singers
        if base.normalize_artist_name(singer.get("name", "")) == target_key
        or base.artist_name_matches(artist_name, singer.get("name", ""))
    ]
    chosen = exact[0] if exact else singers[0]
    if log_func:
        log_func(f"已匹配 QQ 音乐歌手：{chosen['name']} ({chosen['mid']})")
    return chosen


def extract_qq_album_item(album: dict) -> dict:
    album_mid = first_value(
        album,
        [
            "album_mid",
            "albumMid",
            "albummid",
            "mid",
            "Falbum_mid",
            "albumMID",
        ],
        "",
    )
    album_id = first_value(
        album,
        [
            "album_id",
            "albumID",
            "albumId",
            "albumid",
            "id",
            "Falbum_id",
        ],
        "",
    )
    album_name = first_value(
        album,
        [
            "album_name",
            "albumName",
            "albumname",
            "name",
            "Falbum_name",
        ],
        "",
    )
    publish_date = normalize_qq_publish_date(
        first_value(
            album,
            [
                "publish_time",
                "publishTime",
                "publish_date",
                "public_time",
                "publicTime",
                "pub_time",
                "pubTime",
                "Fpublic_time",
                "date",
                "ctime",
            ],
            "",
        )
    )
    track_count = to_int(
        first_value(
            album,
            [
                "song_count",
                "songCount",
                "song_num",
                "songNum",
                "total_song_num",
                "totalSongNum",
                "songnum",
                "Fsong_num",
            ],
            0,
        )
    )
    return {
        "album_mid": str(album_mid).strip(),
        "album_id": str(album_id).strip(),
        "album_name": str(album_name).strip(),
        "publish_date": publish_date,
        "track_count": track_count,
    }


def fetch_qq_singer_album_page(singer_mid: str, begin: int, num: int) -> tuple[list, int]:
    payload = {
        "comm": {
            "ct": 24,
            "cv": 10000,
        },
        "singerAlbum": {
            "module": "music.web_singer_info_svr",
            "method": "get_singer_album",
            "param": {
                "singermid": singer_mid,
                "order": "time",
                "begin": begin,
                "num": num,
                "exstatus": 1,
            },
        },
    }
    result = qq_musicu_request(payload, referer=f"https://y.qq.com/n/ryqq/singer/{singer_mid}")
    data = ((result.get("singerAlbum") or {}).get("data") or {})
    albums = data.get("list") or data.get("albumList") or []
    total = to_int(
        first_value(
            data,
            [
                "total",
                "totalNum",
                "total_num",
            ],
            len(albums),
        ),
        len(albums),
    )
    return albums, total


def extract_qq_song_object(song_item: dict) -> dict:
    return (
        song_item.get("songInfo")
        or song_item.get("song_info")
        or song_item.get("song")
        or song_item
    )


def extract_qq_song_title(song_item: dict) -> str:
    song = extract_qq_song_object(song_item)
    title = first_value(
        song,
        [
            "name",
            "songname",
            "songName",
            "title",
            "songorig",
            "songOrig",
        ],
        "",
    )
    return base.clean_song_name(str(title or ""))


def extract_qq_song_artists(song_item: dict) -> list[dict]:
    song = extract_qq_song_object(song_item)
    raw_artists = []
    for key in ("singer", "singers", "singerList", "singer_list", "artist", "artists"):
        value = song.get(key)
        if isinstance(value, list):
            raw_artists.extend(value)
        elif isinstance(value, dict):
            raw_artists.append(value)
        elif isinstance(value, str) and value.strip():
            raw_artists.append({"name": value.strip()})

    artists = []
    for item in raw_artists:
        if not isinstance(item, dict):
            continue
        name = first_value(
            item,
            ["name", "singerName", "singername", "singer_name", "title"],
            "",
        )
        mid = first_value(
            item,
            ["mid", "singerMid", "singerMID", "singermid", "singer_mid"],
            "",
        )
        if name or mid:
            artists.append({"name": str(name or "").strip(), "mid": str(mid or "").strip()})
    return artists


def qq_song_has_target_artist(song_item: dict, target_artist_name: str = "", target_singer_mid: str = "") -> bool:
    if not (target_artist_name or target_singer_mid):
        return True
    artists = extract_qq_song_artists(song_item)
    if not artists:
        return True
    target_mid = str(target_singer_mid or "").strip()
    for artist in artists:
        mid = str(artist.get("mid") or "").strip()
        name = str(artist.get("name") or "").strip()
        if target_mid and mid and mid == target_mid:
            return True
        if name and base.artist_name_matches(target_artist_name, name):
            return True
    return False


def fetch_qq_album_detail(
    album_mid: str,
    album_id: str | int = "",
    target_artist_name: str = "",
    target_singer_mid: str = "",
) -> dict:
    payload = {
        "comm": {
            "ct": 24,
            "cv": 10000,
        },
        "albumSonglist": {
            "module": "music.musichallAlbum.AlbumSongList",
            "method": "GetAlbumSongList",
            "param": {
                "albumMid": album_mid or "",
                "albumID": to_int(album_id),
                "begin": 0,
                "num": 999,
                "order": 2,
            },
        },
    }
    result = qq_musicu_request(
        payload,
        referer=f"https://y.qq.com/n/ryqq/albumDetail/{album_mid}" if album_mid else QQ_REFERER,
    )
    data = ((result.get("albumSonglist") or {}).get("data") or {})
    songs = data.get("songList") or data.get("songlist") or data.get("list") or []

    ordered_titles = []
    artist_metadata_seen = False
    for index, song_item in enumerate(songs, start=1):
        title = extract_qq_song_title(song_item)
        if not title:
            continue
        artists = extract_qq_song_artists(song_item)
        if artists:
            artist_metadata_seen = True
        if not qq_song_has_target_artist(song_item, target_artist_name, target_singer_mid):
            continue
        ordered_titles.append({"title": title, "track_no": index})

    album_info = data.get("albumInfo") or data.get("album_info") or {}
    publish_date = normalize_qq_publish_date(
        first_value(
            album_info,
            [
                "publishDate",
                "publish_date",
                "publishTime",
                "publish_time",
                "publicTime",
                "public_time",
            ],
            "",
        )
    )
    total = to_int(
        first_value(
            data,
            [
                "totalNum",
                "total_num",
                "total",
                "songCount",
                "song_count",
            ],
            len(ordered_titles),
        ),
        len(ordered_titles),
    )
    artist_filtered = bool((target_artist_name or target_singer_mid) and artist_metadata_seen)
    return {
        "track_titles": ordered_titles,
        "track_count": len(ordered_titles) if artist_filtered else max(total, len(ordered_titles)),
        "publish_date": publish_date,
        "artist_filtered": artist_filtered,
    }


def fetch_qq_album_metadata(artist_name: str, log_func=None) -> dict:
    singer = resolve_qq_singer(artist_name, log_func=log_func)
    singer_mid = singer["mid"]
    page_size = max(1, QQ_PAGE_SIZE)
    begin = 0
    total = None
    metadata = {}
    raw_album_count = 0

    if log_func:
        log_func("正在拉取 QQ 音乐数据（歌手专辑较多时可能需要一些时间）…")

    while True:
        albums, page_total = fetch_qq_singer_album_page(singer_mid, begin, page_size)
        if total is None:
            total = page_total
        if not albums:
            break

        for album in albums:
            item = extract_qq_album_item(album)
            album_name = item["album_name"]
            album_mid = item["album_mid"]
            album_id = item["album_id"]
            if not album_name or not (album_mid or album_id):
                continue

            raw_album_count += 1
            try:
                detail = fetch_qq_album_detail(
                    album_mid,
                    album_id,
                    target_artist_name=artist_name,
                    target_singer_mid=singer_mid,
                )
            except Exception as exc:
                detail = {
                    "track_titles": [],
                    "track_count": 0,
                    "publish_date": "",
                    "artist_filtered": False,
                }
                if log_func:
                    log_func(f"QQ 音乐专辑详情获取失败：{album_name} ({album_mid or album_id})，{exc}")

            publish_date = detail.get("publish_date") or item.get("publish_date") or "未知日期"
            track_titles = detail.get("track_titles") or []
            track_count = int(detail.get("track_count") or item.get("track_count") or len(track_titles) or 0)
            unique_id = album_mid or album_id
            key = f"qq-{unique_id}"
            metadata[key] = {
                "id": unique_id,
                "name": album_name,
                "name_key": base.normalize_album_name(album_name),
                "publish_date": publish_date,
                "track_count": track_count,
                "track_titles": track_titles,
                "artist_filtered": detail.get("artist_filtered", False),
                "sources": [QQ_SOURCE_NAME],
            }

        begin += len(albums)
        if begin >= (total or 0) or len(albums) < page_size:
            break
        time.sleep(0.2)

    if not metadata:
        raise ValueError(f"QQ音乐未获取到歌手专辑：{artist_name}")

    if log_func:
        log_func(
            f"QQ 音乐专辑元数据获取完成：共 {raw_album_count} 张，"
            f"按专辑 MID/ID 保留 {len(metadata)} 条。"
        )
    return metadata


def fetch_album_metadata(artist_name: str, log_func=None):
    try:
        metadata = fetch_qq_album_metadata(artist_name, log_func=log_func)
    except Exception as exc:
        if log_func:
            extra = ""
            if "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                extra = " [可设置环境变量 QQ_AUTO_ASSIGN_HTTP_TIMEOUT=60 延长 QQ 音乐接口超时]"
            log_func(f"QQ 音乐专辑元数据获取失败：{exc}{extra}")
        metadata = {}

    if log_func:
        log_func(f"本次已加载专辑元数据：仅 QQ 音乐，共 {len(metadata)} 条。")
    return metadata


def write_album_mismatch_report(dest_folder: str, artist_name: str, verification_result, log_func=None):
    report_path = base.get_report_path(artist_name)
    items = verification_result.get("mismatched_items", []) if verification_result else []
    missing_albums = verification_result.get("missing_album_items", []) if verification_result else []
    lines = [
        f"歌手：{artist_name}",
        f"数据源：QQ 音乐",
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
                if item.get("extra_count", 0) > 0:
                    line += f" | 多出 {item['extra_count']} 首"
                missing_titles = item.get("missing_titles") or []
                if missing_titles:
                    line += " | 缺：" + "；".join(missing_titles)
                lines.append(line)
            lines.append("")

        if missing_albums:
            lines.append("QQ 音乐存在但本地缺少文件夹的专辑：")
            for item in missing_albums:
                line = (
                    f"{item['album_name']} | 缺少整张专辑 | "
                    f"应有 {item['expected_count']} 首"
                )
                publish_date = (item.get("publish_date") or "").strip()
                if publish_date:
                    line += f" | 发行日期 {publish_date}"
                lines.append(line)

    with open(report_path, "w", encoding="utf-8") as file:
        file.write("\n".join(lines) + "\n")

    if log_func:
        log_func(f"已更新报告：{report_path}")
    return report_path


_original_reorder_album_files = base.reorder_album_files


def reorder_album_files(folder_path: str, metadata, log_func=None):
    if log_func is None:
        return _original_reorder_album_files(folder_path, metadata, log_func=None)

    def qq_log(message: str):
        log_func(str(message).replace("网易云", "QQ 音乐"))

    return _original_reorder_album_files(folder_path, metadata, log_func=qq_log)


def relabel_widget_tree(widget):
    replacements = {
        "音乐整理工具": "QQ 音乐整理工具",
        "按文件元数据中的专辑名自动分组、移动并校验专辑曲目数量": (
            "按文件元数据中的专辑名自动分组、移动，并用 QQ 音乐校验专辑曲目数量"
        ),
        "获取专辑信息并重命名": "获取 QQ 专辑信息并重命名",
        "数量异常专辑": "数量异常专辑（QQ 音乐）",
    }
    try:
        text = widget.cget("text")
        if text in replacements:
            widget.configure(text=replacements[text])
    except Exception:
        pass

    for child in widget.winfo_children():
        relabel_widget_tree(child)


class QQMoveGui(base.MoveGui):
    def _make_widgets(self):
        super()._make_widgets()
        self.cookie_btn = base.ttk.Button(
            self.start_btn.master,
            text="更新 QQ Cookie",
            command=self.on_update_qq_cookie,
        )
        self.cookie_btn.pack(side=base.tk.LEFT, padx=(8, 0))

    def __init__(self, root):
        super().__init__(root)
        self.root.title("QQ 音乐整理工具")
        relabel_widget_tree(self.root)

    def set_running_state(self, running: bool):
        super().set_running_state(running)
        if hasattr(self, "cookie_btn"):
            if running:
                self.cookie_btn.state(["disabled"])
            else:
                self.cookie_btn.state(["!disabled"])

    def on_update_qq_cookie(self):
        confirmed = base.messagebox.askokcancel(
            "更新 QQ Cookie",
            "将打开一个独立的 Chrome/Edge 登录窗口。\n\n"
            "请在该窗口登录 QQ 音乐；登录完成后回到本工具点击“确定”，"
            "程序会保存 QQ 音乐 Cookie 到 qq_music_cookie.txt。",
        )
        if not confirmed:
            return

        proc = None
        try:
            proc, port, profile_dir = launch_qq_login_browser()
            self.log(f"已打开 QQ 音乐登录窗口，专用浏览器配置目录：{profile_dir}")
            base.messagebox.showinfo(
                "等待登录",
                "请在刚打开的浏览器窗口完成 QQ 音乐登录。\n\n"
                "确认页面右上角已显示账号后，再点击这里的“确定”。",
            )
            path, cookie_names = collect_and_save_qq_cookie(port)
            self.log(f"QQ Cookie 已更新：{path}")
            if qq_cookie_has_login_marker(cookie_names):
                base.messagebox.showinfo("更新完成", f"QQ Cookie 已保存：\n{path}")
            else:
                base.messagebox.showwarning(
                    "可能未登录",
                    "已保存 Cookie，但未检测到明确的登录凭据字段。\n"
                    "如果后续 QQ 音乐接口异常，请重新点击“更新 QQ Cookie”并确认已登录。",
                )
        except Exception as exc:
            self.log(f"更新 QQ Cookie 失败：{exc}")
            base.messagebox.showerror("更新 QQ Cookie 失败", str(exc))
        finally:
            stop_login_browser(proc)

    def ensure_tray_icon(self):
        if base.pystray is None or self.tray_icon is not None:
            return
        image = base.create_tray_image()
        if image is None:
            return
        menu = base.pystray.Menu(
            base.pystray.MenuItem("显示窗口", lambda icon, item: self.root.after(0, self.show_window), default=True),
            base.pystray.MenuItem("退出", lambda icon, item: self.root.after(0, self.quit_app)),
        )
        self.tray_icon = base.pystray.Icon("qq_auto_assign_gui", image, "QQ 音乐整理工具", menu)
        self.tray_thread = base.threading.Thread(target=self.tray_icon.run, daemon=True)
        self.tray_thread.start()


base.fetch_album_metadata = fetch_album_metadata
base.write_album_mismatch_report = write_album_mismatch_report
base.reorder_album_files = reorder_album_files


def main():
    root = base.tk.Tk()
    style = base.ttk.Style(root)
    try:
        style.theme_use("clam")
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
    except Exception:
        pass
    QQMoveGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
