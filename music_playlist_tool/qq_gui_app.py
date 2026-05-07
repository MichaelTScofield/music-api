import importlib.util
import os
import queue
import sys
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    Image = None
    ImageDraw = None


def get_app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def create_tray_image():
    if Image is None or ImageDraw is None:
        return None
    image = Image.new("RGBA", (64, 64), (34, 40, 49, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((10, 10, 54, 54), fill=(25, 118, 210, 255))
    draw.rectangle((20, 30, 44, 36), fill=(255, 255, 255, 255))
    draw.rectangle((30, 20, 36, 44), fill=(255, 255, 255, 255))
    return image


APP_DIR = get_app_dir()
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))


def resolve_workflow_path():
    candidates = [
        BUNDLE_DIR / "qq-auto.py",
        BUNDLE_DIR / "QQ自动建立歌单.py",
        APP_DIR / "qq-auto.py",
        APP_DIR / "QQ自动建立歌单.py",
        APP_DIR / "_internal" / "qq-auto.py",
        APP_DIR / "_internal" / "QQ自动建立歌单.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "未找到 QQ 工作流脚本 qq-auto.py/QQ自动建立歌单.py。"
        f" 已检查: {', '.join(str(path) for path in candidates)}"
    )


WORKFLOW_PATH = resolve_workflow_path()


def load_module(path, module_name):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


WORKFLOW = load_module(WORKFLOW_PATH, "qq_music_workflow")


class CookieDialog(tk.Toplevel):
    def __init__(self, parent, initial_value=""):
        super().__init__(parent)
        self.title("更新 QQ Cookie")
        self.geometry("720x340")
        self.minsize(620, 280)
        self.result = None
        self.transient(parent)
        self.grab_set()

        container = ttk.Frame(self, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text="请输入新的 QQ 音乐 Cookie：").pack(anchor=tk.W)

        text_frame = ttk.Frame(container)
        text_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 12))

        self.text = tk.Text(text_frame, wrap=tk.WORD, height=10)
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        if initial_value:
            self.text.insert("1.0", initial_value)

        button_frame = ttk.Frame(container)
        button_frame.pack(fill=tk.X)
        ttk.Button(button_frame, text="保存并重试", command=self.on_submit).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="取消", command=self.on_cancel).pack(side=tk.RIGHT, padx=(0, 8))

        self.bind("<Escape>", lambda _event: self.on_cancel())
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.after(50, self.text.focus_set)

    def on_submit(self):
        value = self.text.get("1.0", tk.END).strip()
        if not value:
            messagebox.showwarning("提示", "请输入新的 Cookie。", parent=self)
            return
        self.result = value
        self.destroy()

    def on_cancel(self):
        self.result = None
        self.destroy()


class QQPlaylistGui:
    def __init__(self, root):
        self.root = root
        self.root.title("QQ 音乐歌单工具")
        self.root.geometry("980x760")
        self.root.minsize(820, 620)

        self.artist_var = tk.StringVar()
        self.singer_mid_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就绪")
        self.current_result = None
        self.worker_thread = None
        self.event_queue = queue.Queue()
        self.pending_artist_name = ""
        self.pending_singer_mid = ""
        self.tray_icon = None
        self.tray_thread = None
        self.is_hidden_to_tray = False
        self.is_quitting = False

        self._build_ui()
        self.root.after(200, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)

    def _build_ui(self):
        wrapper = ttk.Frame(self.root, padding=16)
        wrapper.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(wrapper)
        header.pack(fill=tk.X)
        ttk.Label(header, text="QQ 音乐歌单工具", font=("Segoe UI", 18, "bold")).pack(anchor=tk.W)
        ttk.Label(
            header,
            text="按歌手抓取 QQ 音乐全部专辑歌曲并自动建立 QQ 音乐歌单",
        ).pack(anchor=tk.W, pady=(4, 0))

        input_frame = ttk.LabelFrame(wrapper, text="执行参数", padding=14)
        input_frame.pack(fill=tk.X, pady=(14, 0))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="歌手名").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        self.artist_entry = ttk.Entry(input_frame, textvariable=self.artist_var)
        self.artist_entry.grid(row=0, column=1, sticky=tk.EW)
        self.start_button = ttk.Button(input_frame, text="开始执行", command=self.on_start)
        self.start_button.grid(row=0, column=2, padx=(10, 0))
        self.cookie_button = ttk.Button(input_frame, text="更新 Cookie", command=self.on_update_cookie)
        self.cookie_button.grid(row=0, column=3, padx=(10, 0))

        ttk.Label(input_frame, text="Singer MID").grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=(10, 0))
        ttk.Entry(input_frame, textvariable=self.singer_mid_var).grid(row=1, column=1, sticky=tk.EW, pady=(10, 0))
        ttk.Label(
            input_frame,
            text="可选。填写后会跳过歌手搜索，适合同名歌手或搜索结果不准的情况。",
        ).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(10, 0))

        action_frame = ttk.Frame(wrapper)
        action_frame.pack(fill=tk.X, pady=(12, 0))
        self.open_playlist_button = ttk.Button(
            action_frame,
            text="打开 QQ 音乐歌单",
            command=self.open_playlist,
            state=tk.DISABLED,
        )
        self.open_playlist_button.pack(side=tk.LEFT)
        self.open_report_button = ttk.Button(
            action_frame,
            text="打开未添加报告",
            command=self.open_report,
            state=tk.DISABLED,
        )
        self.open_report_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(action_frame, text="清空内容", command=self.clear_output).pack(side=tk.RIGHT)

        content = ttk.Panedwindow(wrapper, orient=tk.VERTICAL)
        content.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        result_frame = ttk.LabelFrame(content, text="执行结果", padding=10)
        log_frame = ttk.LabelFrame(content, text="状态日志", padding=10)
        content.add(result_frame, weight=3)
        content.add(log_frame, weight=2)

        result_text_frame = ttk.Frame(result_frame)
        result_text_frame.pack(fill=tk.BOTH, expand=True)
        self.result_text = tk.Text(
            result_text_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 11),
            height=16,
        )
        result_scroll = ttk.Scrollbar(result_text_frame, orient=tk.VERTICAL, command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=result_scroll.set)
        self.result_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_text_frame = ttk.Frame(log_frame)
        log_text_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(
            log_text_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 10),
            height=12,
        )
        log_scroll = ttk.Scrollbar(log_text_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        status_bar = ttk.Frame(self.root)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(status_bar, textvariable=self.status_var).pack(side=tk.LEFT, padx=10, pady=8)

        self.artist_entry.focus_set()
        self.root.bind("<Return>", lambda _event: self.on_start())

    def append_log(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        summary = " ".join(str(message).splitlines()).strip()
        if len(summary) > 90:
            summary = summary[:87] + "..."
        if summary:
            self.status_var.set(f"执行中：{summary}")

    def set_result_text(self, text):
        self.result_text.configure(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, text)
        self.result_text.configure(state=tk.DISABLED)

    def clear_output(self):
        self.current_result = None
        self.set_result_text("")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.open_playlist_button.config(state=tk.DISABLED)
        self.open_report_button.config(state=tk.DISABLED)
        self.status_var.set("就绪")

    def open_playlist(self):
        if self.current_result and self.current_result.qq:
            webbrowser.open(self.current_result.qq.playlist_url)

    def open_report(self):
        if self.current_result and self.current_result.qq:
            path = self.current_result.qq.missing_report_path
            if path and os.path.exists(path):
                os.startfile(path)

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
        self.tray_icon = pystray.Icon("qq_music_playlist_gui", image, "QQ 音乐歌单工具", menu)
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

    def set_running(self, running):
        state = tk.DISABLED if running else tk.NORMAL
        self.start_button.config(state=state)
        self.cookie_button.config(state=state)

    def prompt_cookie(self):
        dialog = CookieDialog(self.root, initial_value=WORKFLOW.get_qq_cookie() or WORKFLOW.load_qq_cookie())
        self.root.wait_window(dialog)
        return dialog.result

    def on_update_cookie(self):
        cookie = self.prompt_cookie()
        if cookie:
            WORKFLOW.set_qq_cookie(cookie, persist=True)
            self.append_log("QQ Cookie 已更新。")
            self.status_var.set("Cookie 已更新")

    def on_start(self):
        artist_name = self.artist_var.get().strip()
        singer_mid = self.singer_mid_var.get().strip()
        if not artist_name and not singer_mid:
            messagebox.showwarning("提示", "请输入歌手名字，或填写 Singer MID。")
            return
        if not artist_name and singer_mid:
            artist_name = f"mid:{singer_mid}"
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("提示", "任务正在执行，请稍候。")
            return
        self.pending_artist_name = artist_name
        self.pending_singer_mid = singer_mid
        self.clear_output()
        self.set_running(True)
        self.status_var.set("执行中...")
        self._start_worker(artist_name, singer_mid)

    def _start_worker(self, artist_name, singer_mid):
        self.worker_thread = threading.Thread(
            target=self._run_workflow,
            args=(artist_name, singer_mid),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_workflow(self, artist_name, singer_mid):
        try:
            result = WORKFLOW.run_workflow(
                artist_name,
                execute_qq=True,
                output_directory=str(APP_DIR),
                log_callback=lambda msg: self.event_queue.put(("log", msg)),
                verbose_details=False,
                singer_mid=singer_mid or None,
            )
            self.event_queue.put(("done", result))
        except WORKFLOW.CookieExpiredError as exc:
            self.event_queue.put(("cookie_expired", str(exc)))
        except Exception as exc:
            detail = traceback.format_exc()
            self.event_queue.put(("error", f"{exc}\n\n{detail}"))

    def _poll_events(self):
        while not self.event_queue.empty():
            event_type, payload = self.event_queue.get()
            if event_type == "log":
                self.append_log(payload)
            elif event_type == "done":
                self._handle_done(payload)
            elif event_type == "error":
                self._handle_error(payload)
            elif event_type == "cookie_expired":
                self._handle_cookie_expired(payload)
        self.root.after(200, self._poll_events)

    def _handle_done(self, result):
        self.current_result = result
        self.status_var.set("执行完成")
        self.set_running(False)
        self.set_result_text(WORKFLOW.build_result_text(result))
        if result.qq:
            self.open_playlist_button.config(state=tk.NORMAL)
            if result.qq.missing_report_path:
                self.open_report_button.config(state=tk.NORMAL)

    def _handle_error(self, text):
        self.status_var.set("执行失败")
        self.set_running(False)
        self.set_result_text(text)
        self.append_log("执行失败，请查看上方错误信息。")

    def _handle_cookie_expired(self, message):
        self.set_running(False)
        self.append_log(message)
        cookie = self.prompt_cookie()
        if not cookie:
            self.status_var.set("已取消")
            self.append_log("未输入新的 Cookie，任务已取消。")
            return
        WORKFLOW.set_qq_cookie(cookie, persist=True)
        self.append_log("QQ Cookie 已更新，正在重新执行。")
        self.set_running(True)
        self.status_var.set("正在使用新 Cookie 重试...")
        self._start_worker(self.pending_artist_name, self.pending_singer_mid)


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
    except Exception:
        pass
    QQPlaylistGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
