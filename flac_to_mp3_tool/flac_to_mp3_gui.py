"""
批量音频转换工具：递归扫描父文件夹，将 FLAC 转为 320k MP3，并保留目录结构与关键元数据。
- FLAC -> MP3：ffmpeg(libmp3lame, 320k)，再用 mutagen 写入 ARTIST/TITLE/ALBUM/DATE(TDRC)/LYRICS/Front Cover
- MP3：原样复制（copy2），保留全部原始元数据
- 转换正常结束后：统计输出目录下「直接包含音频」的专辑子文件夹，仅更新文件夹名末尾的曲目数 `(N首)` / `（N首）`，其余文字（含日期前缀等）保持不变
- GUI：Tkinter；后台线程 + ThreadPoolExecutor；支持“停止”可靠终止正在运行的 ffmpeg

依赖：
- 外部：ffmpeg (需在 PATH 中)
- Python 包：mutagen
- 标准库：tkinter, concurrent.futures, threading, queue, subprocess, pathlib, shutil, os, sys, time, atexit, traceback
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from queue import Queue, Empty
from typing import Optional, Dict, Tuple, List, Callable

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, ID3NoHeaderError, TDRC

AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".wma", ".ape", ".aiff", ".alac"}


# -----------------------------
# 子进程/停止控制（避免 PIPE 死锁）
# -----------------------------

class ProcessRegistry:
    """跟踪所有正在运行的 ffmpeg 进程，支持停止时可靠终止。"""
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: Dict[int, subprocess.Popen] = {}

    def add(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if proc.pid is not None:
                self._procs[proc.pid] = proc

    def remove(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if proc.pid is not None:
                self._procs.pop(proc.pid, None)

    def kill_all(self) -> None:
        with self._lock:
            procs = list(self._procs.values())
        for p in procs:
            safe_terminate_process(p)


PROC_REGISTRY = ProcessRegistry()


def safe_terminate_process(proc: subprocess.Popen, timeout: float = 2.0) -> None:
    """尽最大努力终止进程并 wait，避免僵尸进程。"""
    if proc is None:
        return

    try:
        if proc.poll() is not None:
            return

        try:
            proc.terminate()
        except Exception:
            pass

        try:
            proc.wait(timeout=timeout)
            return
        except Exception:
            pass

        try:
            proc.kill()
        except Exception:
            pass

        try:
            proc.wait(timeout=timeout)
            return
        except Exception:
            pass

        # Windows 兜底：taskkill /T /F
        if sys.platform.startswith("win") and proc.pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    creationflags=_win_creationflags(),
                    check=False,
                )
            except Exception:
                pass
    finally:
        try:
            proc.wait(timeout=0.2)
        except Exception:
            pass


def _win_creationflags() -> int:
    if not sys.platform.startswith("win"):
        return 0
    flags = 0
    # PyInstaller / GUI 友好：不弹黑窗
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # 新进程组：更易于管理（虽然我们主要 kill/terminate/taskkill）
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


atexit.register(PROC_REGISTRY.kill_all)


# -----------------------------
# 音频任务与处理
# -----------------------------

@dataclass(frozen=True)
class Task:
    src: Path
    dst: Path
    kind: str  # "copy_mp3" | "flac_to_mp3"


def find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def is_audio_ext(p: Path) -> bool:
    ext = p.suffix.lower()
    return ext in {".flac", ".mp3"}


def strip_song_count_suffix(name: str) -> str:
    return re.sub(r"[（(]\d+首[）)]$", "", (name or "").strip()).strip()


def build_counted_folder_name(name: str, total_songs: int) -> str:
    return f"{strip_song_count_suffix(name)}（{total_songs}首）"


def find_existing_output_folder(parent: Path, base_name: str) -> Optional[Path]:
    if not parent.exists():
        return None
    for item in parent.iterdir():
        if item.is_dir() and strip_song_count_suffix(item.name) == base_name:
            return item
    return None


def count_audio_summary(folder_path: Path) -> Tuple[int, List[Tuple[str, int]]]:
    total = 0
    format_counts: Dict[str, int] = {}
    for p in folder_path.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext not in AUDIO_EXTENSIONS:
            continue
        total += 1
        key = ext.lstrip(".")
        format_counts[key] = format_counts.get(key, 0) + 1
    ordered_counts: List[Tuple[str, int]] = []
    for ext in [item.lstrip(".") for item in sorted(AUDIO_EXTENSIONS)]:
        if ext in format_counts:
            ordered_counts.append((ext, format_counts.pop(ext)))
    for ext in sorted(format_counts):
        ordered_counts.append((ext, format_counts[ext]))
    return total, ordered_counts


def finalize_output_folder(output_dir: Path) -> Tuple[Path, int, List[Tuple[str, int]]]:
    total_songs, format_counts = count_audio_summary(output_dir)
    target_dir = output_dir.with_name(build_counted_folder_name(output_dir.name, total_songs))
    if output_dir.resolve() != target_dir.resolve():
        if target_dir.exists():
            raise FileExistsError(f"目标目录已存在：{target_dir}")
        output_dir.rename(target_dir)
        output_dir = target_dir
    return output_dir, total_songs, format_counts


# -----------------------------
# 专辑子文件夹：仅更新末尾曲目数，其余名称不变
# -----------------------------

def update_album_folder_count_in_name(folder_name: str, new_count: int) -> str:
    """
    保留原文件夹名（含日期前缀、专辑名、括号样式），只把末尾的「(N首)」或「（N首）」里的数字改为 new_count。
    若没有曲目数后缀，则在末尾追加「 (new_count首)」。
    """
    m = re.match(r"^(.*)\s*([（(])(\d+)首([）)])$", folder_name)
    if m:
        prefix, open_b, _, close_b = m.group(1), m.group(2), m.group(3), m.group(4)
        base = prefix.rstrip()
        return f"{base}{open_b}{new_count}首{close_b}"
    return f"{folder_name} ({new_count}首)"


def count_direct_audio_files(folder: Path) -> int:
    n = 0
    try:
        for child in folder.iterdir():
            if child.is_file() and child.suffix.lower() in AUDIO_EXTENSIONS:
                n += 1
    except OSError:
        return 0
    return n


def iter_album_leaf_dirs(output_root: Path) -> List[Path]:
    """输出根目录下，「直接含有音频文件」的子目录，由深到浅排序以便安全 rename。"""
    root = output_root.resolve()
    found: List[Path] = []
    if not root.is_dir():
        return found
    for p in root.rglob("*"):
        if not p.is_dir():
            continue
        if p.resolve() == root:
            continue
        if count_direct_audio_files(p) > 0:
            found.append(p)
    found.sort(key=lambda x: len(x.parts), reverse=True)
    return found


def rename_album_folders_under_output(output_root: Path, log_func: Optional[Callable[[str], None]] = None) -> int:
    """仅更新专辑文件夹名中的曲目数；返回成功重命名数量。"""
    renamed = 0
    for folder in iter_album_leaf_dirs(output_root):
        n = count_direct_audio_files(folder)
        if n <= 0:
            continue
        target_name = update_album_folder_count_in_name(folder.name, n)
        if folder.name == target_name:
            continue
        target_path = folder.parent / target_name
        if target_path.resolve() == folder.resolve():
            continue
        if target_path.exists():
            if log_func:
                log_func(f"跳过专辑目录重命名（目标已存在）：{folder} -> {target_name}")
            continue
        try:
            folder.rename(target_path)
            renamed += 1
        except OSError as exc:
            if log_func:
                log_func(f"专辑目录重命名失败：{folder.name} -> {target_name} ({exc})")
    return renamed


def build_tasks(input_root: Path, output_root: Path) -> List[Task]:
    tasks: List[Task] = []
    for p in input_root.rglob("*"):
        if not p.is_file():
            continue
        if not is_audio_ext(p):
            continue

        rel = p.relative_to(input_root)
        if p.suffix.lower() == ".mp3":
            dst = output_root / rel
            tasks.append(Task(src=p, dst=dst, kind="copy_mp3"))
        else:
            dst = (output_root / rel).with_suffix(".mp3")
            tasks.append(Task(src=p, dst=dst, kind="flac_to_mp3"))
    return tasks


def ensure_parent_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)


def copy_mp3(src: Path, dst: Path) -> None:
    ensure_parent_dir(dst)
    shutil.copy2(src, dst)


def _read_flac_metadata(src_flac: Path) -> Tuple[dict, Optional[Tuple[bytes, str]]]:
    """
    返回：
    - tags：{artist,title,album,lyrics}（尽力从 FLAC Vorbis Comment 中取）
    - cover：(image_bytes, mime) 仅 Front Cover（type=3）；若无则 None
    """
    fl = FLAC(src_flac)
    tags = {
        "artist": (fl.get("ARTIST", [None]) or [None])[0],
        "title": (fl.get("TITLE", [None]) or [None])[0],
        "album": (fl.get("ALBUM", [None]) or [None])[0],
        "date": (
            (fl.get("DATE", [None]) or [None])[0]
            or (fl.get("YEAR", [None]) or [None])[0]
            or (fl.get("ORIGINALDATE", [None]) or [None])[0]
            or (fl.get("ORIGINALYEAR", [None]) or [None])[0]
        ),
        # LYRICS 可能大小写/命名不同，做多键兜底
        "lyrics": (
            (fl.get("LYRICS", [None]) or [None])[0]
            or (fl.get("LYRIC", [None]) or [None])[0]
            or (fl.get("UNSYNCEDLYRICS", [None]) or [None])[0]
        ),
    }

    cover = None
    front_pics = [pic for pic in getattr(fl, "pictures", []) if getattr(pic, "type", None) == 3]
    if front_pics:
        pic = front_pics[0]
        mime = pic.mime or "image/jpeg"
        cover = (pic.data, mime)

    return tags, cover


def _write_mp3_metadata(dst_mp3: Path, tags: dict, cover: Optional[Tuple[bytes, str]]) -> None:
    """
    写入 ID3：
    - ARTIST/TITLE/ALBUM：用标准帧（TPE1/TIT2/TALB）由 mutagen.MP3+ID3 处理
    - LYRICS：USLT（无时间轴歌词）
    - Cover：APIC（Front Cover）
    """
    audio = MP3(dst_mp3)

    try:
        id3 = audio.tags
        if id3 is None:
            audio.add_tags()
            id3 = audio.tags
    except Exception:
        try:
            id3 = ID3(dst_mp3)
        except ID3NoHeaderError:
            id3 = ID3()

    # 使用 ID3 的文本帧（更通用）
    if tags.get("artist"):
        id3.add(mutagen_id3_text_frame("TPE1", tags["artist"]))
    if tags.get("title"):
        id3.add(mutagen_id3_text_frame("TIT2", tags["title"]))
    if tags.get("album"):
        id3.add(mutagen_id3_text_frame("TALB", tags["album"]))

    if tags.get("date"):
        id3.delall("TDRC")
        id3.add(TDRC(encoding=3, text=str(tags["date"])))

    # 歌词
    lyrics = tags.get("lyrics")
    if lyrics:
        # 清理旧的 USLT，避免重复
        id3.delall("USLT")
        id3.add(USLT(encoding=3, lang="eng", desc="", text=str(lyrics)))

    # 封面（仅 Front Cover）
    if cover:
        img_bytes, mime = cover
        id3.delall("APIC")
        id3.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,   # Front cover
                desc="Front Cover",
                data=img_bytes,
            )
        )

    id3.save(dst_mp3)


def mutagen_id3_text_frame(frame_id: str, text: str):
    # 延迟导入，避免顶部 import 太多类名
    from mutagen.id3 import Frames
    cls = Frames.get(frame_id)
    if cls is None:
        raise ValueError(f"Unknown ID3 frame: {frame_id}")
    return cls(encoding=3, text=[str(text)])


def flac_to_mp3(ffmpeg_path: str, src: Path, dst: Path, stop_event: threading.Event) -> None:
    """
    关键点：
    - 禁止 stdout=PIPE / stderr=PIPE
    - ffmpeg stderr 输出写入临时文件（安全，不会缓冲区死锁），失败时可读回原因
    - 停止时可可靠 kill/terminate
    """
    ensure_parent_dir(dst)

    tags, cover = _read_flac_metadata(src)

    # ffmpeg 临时 stderr 日志文件（不是 PIPE，不会卡死）
    tmp_log = dst.with_suffix(dst.suffix + ".ffmpeg.log.tmp")
    if tmp_log.exists():
        try:
            tmp_log.unlink()
        except Exception:
            pass

    # 若目标已存在，允许覆盖（-y）
    # -loglevel error：尽量少输出；但我们仍保留到文件用于失败诊断
    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(src),
        "-map", "0:a:0",
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "320k",
        str(dst),
    ]

    if stop_event.is_set():
        raise RuntimeError("Stopped")

    with open(tmp_log, "wb") as err_f:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,   # 强制避免 PIPE
            stderr=err_f,                # 写文件也不会 PIPE 死锁
            creationflags=_win_creationflags(),
        )
        PROC_REGISTRY.add(proc)
        try:
            # 不用 poll + PIPE；直接 wait
            while True:
                if stop_event.is_set():
                    safe_terminate_process(proc)
                    raise RuntimeError("Stopped")
                rc = proc.poll()
                if rc is not None:
                    if rc != 0:
                        # 读回 stderr 文件（可选）
                        err_f.flush()
                        try:
                            msg = tmp_log.read_text(encoding="utf-8", errors="replace")
                        except Exception:
                            msg = ""
                        raise RuntimeError(f"ffmpeg failed (code={rc}). {msg.strip()}")
                    break
                time.sleep(0.1)
        finally:
            PROC_REGISTRY.remove(proc)

    # 写入元数据（仅要求的字段 + Front Cover）
    _write_mp3_metadata(dst, tags, cover)

    try:
        tmp_log.unlink(missing_ok=True)  # py3.8+ on win? (3.8 supports)
    except Exception:
        try:
            if tmp_log.exists():
                tmp_log.unlink()
        except Exception:
            pass


# -----------------------------
# GUI 与后台调度
# -----------------------------

class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("批量 FLAC -> MP3 转换工具（稳定版）")
        self.root.geometry("900x600")

        self.msg_q: "Queue[Tuple[str, object]]" = Queue()
        self.stop_event = threading.Event()

        self.controller_thread: Optional[threading.Thread] = None
        self.executor: Optional[ThreadPoolExecutor] = None

        self.total_tasks = 0
        self.done_tasks = 0
        self.fail_tasks = 0
        self.current_output_dir: Optional[Path] = None

        self.ffmpeg_path = find_ffmpeg()

        self._build_ui()
        self._start_ui_pump()

        if not self.ffmpeg_path:
            self._log("ERROR: 未检测到 ffmpeg。请安装 ffmpeg 并将其加入 PATH 后重启。")
            messagebox.showerror("缺少 ffmpeg", "未检测到 ffmpeg（PATH 中找不到 ffmpeg）。\n请安装并配置 PATH 后重启。")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        frm = ttk.Frame(self.root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        # 选择目录
        row1 = ttk.Frame(frm)
        row1.pack(fill=tk.X)

        ttk.Label(row1, text="父文件夹：").pack(side=tk.LEFT)
        self.path_var = tk.StringVar()
        self.path_entry = ttk.Entry(row1, textvariable=self.path_var)
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Button(row1, text="📂 选择", command=self.on_pick_dir).pack(side=tk.LEFT)

        # 并发数
        row2 = ttk.Frame(frm)
        row2.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(row2, text="并发数：").pack(side=tk.LEFT)

        default_workers = max(1, min(4, (os.cpu_count() or 2)))
        self.workers_var = tk.IntVar(value=default_workers)
        self.workers_spin = ttk.Spinbox(row2, from_=1, to=32, textvariable=self.workers_var, width=6)
        self.workers_spin.pack(side=tk.LEFT, padx=(6, 18))
        ttk.Label(row2, text="（建议 2-4，过高会导致 IO/CPU 拥塞）").pack(side=tk.LEFT)

        # 按钮
        row3 = ttk.Frame(frm)
        row3.pack(fill=tk.X, pady=(10, 0))

        self.start_btn = ttk.Button(row3, text="▶ 开始转换", command=self.on_start)
        self.start_btn.pack(side=tk.LEFT)

        self.stop_btn = ttk.Button(row3, text="⛔ 停止", command=self.on_stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=8)

        # 进度条
        row4 = ttk.Frame(frm)
        row4.pack(fill=tk.X, pady=(10, 0))
        self.progress = ttk.Progressbar(row4, orient="horizontal", mode="determinate")
        self.progress.pack(fill=tk.X, expand=True)
        self.progress_label = ttk.Label(row4, text="0 / 0")
        self.progress_label.pack(anchor="e", pady=(4, 0))

        # 日志
        log_frm = ttk.LabelFrame(frm, text="📜 日志", padding=8)
        log_frm.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        self.log_text = tk.Text(log_frm, height=18, wrap="word")
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        sb = ttk.Scrollbar(log_frm, command=self.log_text.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=sb.set)

        self._log("就绪。请选择父文件夹后点击“开始转换”。")

    def _start_ui_pump(self) -> None:
        def pump() -> None:
            try:
                while True:
                    typ, payload = self.msg_q.get_nowait()
                    if typ == "log":
                        self._log(str(payload))
                    elif typ == "progress":
                        done, total, fail = payload  # type: ignore
                        self._set_progress(done, total, fail)
                    elif typ == "finished":
                        self._on_finished()
                    else:
                        self._log(f"DEBUG: unknown msg {typ}")
            except Empty:
                pass
            self.root.after(100, pump)

        self.root.after(100, pump)

    def _log(self, s: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {s}\n")
        self.log_text.see(tk.END)

    def _set_progress(self, done: int, total: int, fail: int) -> None:
        self.progress["maximum"] = max(1, total)
        self.progress["value"] = done
        self.progress_label.configure(text=f"{done} / {total}（失败 {fail}）")

    def on_pick_dir(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.path_var.set(d)

    def on_start(self) -> None:
        if not self.ffmpeg_path:
            messagebox.showerror("缺少 ffmpeg", "未检测到 ffmpeg（PATH 中找不到 ffmpeg）。")
            return

        input_dir = Path(self.path_var.get().strip('"').strip())
        if not input_dir.exists() or not input_dir.is_dir():
            messagebox.showerror("路径错误", "请选择一个有效的父文件夹。")
            return

        # 输出目录：同级创建 “<父文件夹名> MP3”
        output_base_name = f"{strip_song_count_suffix(input_dir.name)} MP3"
        output_dir = find_existing_output_folder(input_dir.parent, output_base_name) or (input_dir.parent / output_base_name)
        self.current_output_dir = output_dir

        workers = int(self.workers_var.get() or 2)
        workers = max(1, min(32, workers))

        self.stop_event.clear()
        self.done_tasks = 0
        self.fail_tasks = 0

        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

        self._log(f"开始扫描：{input_dir}")
        self._log(f"输出目录：{output_dir}")
        self._log(f"并发数：{workers}")

        # 后台控制线程：只扫描一次，然后提交任务
        self.controller_thread = threading.Thread(
            target=self._controller_run,
            args=(input_dir, output_dir, workers),
            daemon=True,
        )
        self.controller_thread.start()

    def on_stop(self) -> None:
        self._log("收到停止请求：将终止未完成任务，并尝试 kill 正在运行的 ffmpeg...")
        self.stop_event.set()
        PROC_REGISTRY.kill_all()

    def _controller_run(self, input_dir: Path, output_dir: Path, workers: int) -> None:
        try:
            tasks = build_tasks(input_dir, output_dir)
            self.total_tasks = len(tasks)

            self.msg_q.put(("log", f"扫描完成：共 {self.total_tasks} 个任务（FLAC 转码 + MP3 复制）。"))
            self.msg_q.put(("progress", (0, self.total_tasks, 0)))

            if self.total_tasks == 0:
                self.msg_q.put(("log", "未找到 .flac 或 .mp3 文件。"))
                self.msg_q.put(("finished", None))
                return

            self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="worker")

            futures = []
            for t in tasks:
                if self.stop_event.is_set():
                    break
                futures.append(self.executor.submit(self._do_one_task, t, self.ffmpeg_path, self.stop_event))

            # 尽力取消尚未开始的任务
            if self.stop_event.is_set():
                for f in futures:
                    f.cancel()

            for f in as_completed(futures):
                # 即使 stop，仍要把已完成/失败的回收掉，避免资源悬挂
                try:
                    ok, msg = f.result()
                    if ok:
                        self.done_tasks += 1
                        self.msg_q.put(("log", f"OK: {msg}"))
                    else:
                        self.done_tasks += 1
                        self.fail_tasks += 1
                        self.msg_q.put(("log", f"FAIL: {msg}"))
                except Exception as e:
                    self.done_tasks += 1
                    self.fail_tasks += 1
                    self.msg_q.put(("log", f"FAIL: 未知异常：{e}"))

                self.msg_q.put(("progress", (self.done_tasks, self.total_tasks, self.fail_tasks)))

                if self.stop_event.is_set():
                    # 停止后尽快退出 as_completed 循环：取消其余 future，并 kill 所有 ffmpeg
                    for other in futures:
                        other.cancel()
                    PROC_REGISTRY.kill_all()
                    break

        except Exception:
            self.msg_q.put(("log", "FATAL: 控制线程异常："))
            self.msg_q.put(("log", traceback.format_exc()))
        finally:
            try:
                if self.executor:
                    self.executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            self.msg_q.put(("finished", None))

    def _do_one_task(self, task: Task, ffmpeg_path: str, stop_event: threading.Event) -> Tuple[bool, str]:
        if stop_event.is_set():
            return False, f"Stopped: {task.src}"

        try:
            if task.kind == "copy_mp3":
                copy_mp3(task.src, task.dst)
                return True, f"复制 MP3：{task.src} -> {task.dst}"

            if task.kind == "flac_to_mp3":
                flac_to_mp3(ffmpeg_path, task.src, task.dst, stop_event)
                return True, f"转码 FLAC->MP3：{task.src} -> {task.dst}"

            return False, f"未知任务类型：{task.kind} ({task.src})"

        except RuntimeError as e:
            # 停止也算失败但不影响整体
            if str(e).strip().lower() == "stopped":
                return False, f"Stopped: {task.src}"
            return False, f"{task.src}：{e}"

        except Exception as e:
            return False, f"{task.src}：{e}\n{traceback.format_exc()}"

    def _on_finished(self) -> None:
        self.stop_btn.configure(state=tk.DISABLED)
        self.start_btn.configure(state=tk.NORMAL)

        if self.stop_event.is_set():
            self._log(f"已停止：完成 {self.done_tasks}/{self.total_tasks}，失败 {self.fail_tasks}。")
        else:
            if self.current_output_dir and self.current_output_dir.exists():
                try:
                    self.current_output_dir, total_songs, format_counts = finalize_output_folder(self.current_output_dir)
                    if format_counts:
                        summary = " ".join(f"{ext} {count}首" for ext, count in format_counts)
                        self._log(f"目录统计：共 {total_songs} 首，{summary}")
                    else:
                        self._log(f"目录统计：共 {total_songs} 首。")
                    self._log(f"输出目录已更新：{self.current_output_dir}")
                except Exception as exc:
                    self._log(f"目录统计失败：{exc}")
                try:
                    n_album = rename_album_folders_under_output(self.current_output_dir, self._log)
                    self._log(f"专辑子目录已更新曲目数后缀：共 {n_album} 个。")
                except Exception as exc:
                    self._log(f"专辑子目录重命名失败：{exc}")
            self._log(f"完成：共 {self.total_tasks}，失败 {self.fail_tasks}。")

    def on_close(self) -> None:
        # 关闭窗口时，确保不会遗留 ffmpeg 进程
        try:
            self.stop_event.set()
            PROC_REGISTRY.kill_all()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    # PyInstaller 友好：用 --noconsole / --windowed 打包即可（脚本本身不弹控制台）
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()