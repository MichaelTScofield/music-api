import importlib.util
import os
import queue
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from service_manager import MusicApiServiceManager
from single_instance import SingleInstance
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
    draw.ellipse((10, 10, 54, 54), fill=(56, 142, 60, 255))
    draw.rectangle((20, 30, 44, 36), fill=(255, 255, 255, 255))
    draw.rectangle((30, 20, 36, 44), fill=(255, 255, 255, 255))
    return image


APP_DIR = get_app_dir()
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
NETEASE_COOKIE_PATH = APP_DIR / "netease_cookie.txt"
QQ_COOKIE_PATH = Path(os.environ.get("QQ_MUSIC_COOKIE_FILE") or (APP_DIR / "qq_music_cookie.txt"))
SINGLE_INSTANCE_APP_ID = "music-api-playlist"
SINGLE_INSTANCE_PORT = 49232
os.environ.setdefault("QQ_MUSIC_COOKIE_FILE", str(QQ_COOKIE_PATH))


def resolve_workflow_path():
    candidates = [
        BUNDLE_DIR / "auto.py",
        BUNDLE_DIR / "自动建立歌单.py",
        APP_DIR / "auto.py",
        APP_DIR / "自动建立歌单.py",
        APP_DIR / "_internal" / "auto.py",
        APP_DIR / "_internal" / "自动建立歌单.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "未找到工作流脚本 auto.py/自动建立歌单.py。"
        f" 已检查: {', '.join(str(path) for path in candidates)}"
    )


WORKFLOW_PATH = resolve_workflow_path()


def load_workflow_module():
    spec = importlib.util.spec_from_file_location("music_workflow", WORKFLOW_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


WORKFLOW = load_workflow_module()


class CookieDialog(tk.Toplevel):
    def __init__(
        self,
        parent,
        initial_value="",
        title="更新网易云 Cookie",
        prompt="检测到网易云 Cookie 已失效，请输入新的 Cookie：",
        submit_text="保存并重试",
    ):
        super().__init__(parent)
        self.title(title)
        self.geometry("720x340")
        self.minsize(620, 280)
        self.result = None
        self.transient(parent)
        self.grab_set()

        container = ttk.Frame(self, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            container,
            text=prompt,
        ).pack(anchor=tk.W)

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
        ttk.Button(button_frame, text=submit_text, command=self.on_submit).pack(side=tk.RIGHT)
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


class PlaylistGui:
    def __init__(self, root):
        self.root = root
        self.root.title("音乐歌单工具")
        self.root.geometry("980x760")
        self.root.minsize(820, 620)

        self.artist_var = tk.StringVar()
        self.mode_var = tk.StringVar(value="auto")
        self.status_var = tk.StringVar(value="就绪")
        self.current_result = None
        self.worker_thread = None
        self.event_queue = queue.Queue()
        self.pending_artist_name = ""
        self.pending_mode = "auto"
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
        ttk.Label(header, text="音乐歌单工具", font=("Segoe UI", 18, "bold")).pack(anchor=tk.W)
        ttk.Label(
            header,
            text="自动创建专辑数更多的平台歌单，或手动指定 QQ/网易云歌单",
        ).pack(anchor=tk.W, pady=(4, 0))

        input_frame = ttk.LabelFrame(wrapper, text="执行参数", padding=14)
        input_frame.pack(fill=tk.X, pady=(14, 0))
        input_frame.columnconfigure(1, weight=1)

        ttk.Label(input_frame, text="歌手名").grid(row=0, column=0, sticky=tk.W, padx=(0, 10))
        self.artist_entry = ttk.Entry(input_frame, textvariable=self.artist_var)
        self.artist_entry.grid(row=0, column=1, sticky=tk.EW)
        self.start_button = ttk.Button(input_frame, text="开始执行", command=self.on_start)
        self.start_button.grid(row=0, column=2, padx=(10, 0))
        self.cookie_button = ttk.Button(input_frame, text="更新网易云 Cookie", command=self.on_update_cookie)
        self.cookie_button.grid(row=0, column=3, padx=(10, 0))

        ttk.Label(input_frame, text="创建模式").grid(row=1, column=0, sticky=tk.W, padx=(0, 10), pady=(10, 0))
        mode_frame = ttk.Frame(input_frame)
        mode_frame.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=(10, 0))
        ttk.Radiobutton(
            mode_frame,
            text="自动创建",
            variable=self.mode_var,
            value="auto",
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            mode_frame,
            text="只创建 QQ 音乐歌单",
            variable=self.mode_var,
            value="qq_only",
        ).pack(anchor=tk.W, pady=(6, 0))
        ttk.Radiobutton(
            mode_frame,
            text="只创建网易云歌单",
            variable=self.mode_var,
            value="netease_only",
        ).pack(anchor=tk.W, pady=(6, 0))
        self.qq_cookie_button = ttk.Button(input_frame, text="更新 QQ Cookie", command=self.on_update_qq_cookie)
        self.qq_cookie_button.grid(row=2, column=3, sticky=tk.E, pady=(10, 0))

        action_frame = ttk.Frame(wrapper)
        action_frame.pack(fill=tk.X, pady=(12, 0))
        self.open_diff_report_button = ttk.Button(
            action_frame,
            text="打开差异报告",
            command=self.open_diff_report,
            state=tk.DISABLED,
        )
        self.open_diff_report_button.pack(side=tk.LEFT)
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
        self.open_diff_report_button.config(state=tk.DISABLED)
        self.status_var.set("就绪")

    def open_diff_report(self):
        if self.current_result and self.current_result.album_diff:
            path = self.current_result.album_diff.report_path
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
        self.tray_icon = pystray.Icon("music_playlist_gui", image, "音乐歌单工具", menu)
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

    def prompt_cookie(self):
        dialog = CookieDialog(
            self.root,
            initial_value=WORKFLOW.load_netease_cookie(str(NETEASE_COOKIE_PATH)) or WORKFLOW.get_netease_cookie(),
        )
        self.root.wait_window(dialog)
        return dialog.result

    def prompt_qq_cookie(self):
        dialog = CookieDialog(
            self.root,
            initial_value=WORKFLOW.load_qq_cookie(str(QQ_COOKIE_PATH)) or WORKFLOW.get_qq_cookie(),
            title="更新 QQ Cookie",
            prompt="请输入新的 QQ 音乐 Cookie：",
        )
        self.root.wait_window(dialog)
        return dialog.result

    def on_update_cookie(self):
        try:
            cookie = self.prompt_cookie()
            if cookie:
                WORKFLOW.set_netease_cookie(cookie, persist=True, cookie_path=str(NETEASE_COOKIE_PATH))
                self.append_log("网易云 Cookie 已更新。")
                self.status_var.set("Cookie 已更新")
        except Exception as exc:
            self.append_log(f"网易云 Cookie 更新失败：{exc}")
            messagebox.showerror("更新网易云 Cookie 失败", str(exc), parent=getattr(self, "root", None))

    def on_update_qq_cookie(self):
        try:
            cookie = self.prompt_qq_cookie()
            if cookie:
                WORKFLOW.set_qq_cookie(cookie, persist=True, cookie_path=str(QQ_COOKIE_PATH))
                self.append_log("QQ Cookie 已更新。")
                self.status_var.set("Cookie 已更新")
        except Exception as exc:
            self.append_log(f"QQ Cookie 更新失败：{exc}")
            messagebox.showerror("更新 QQ Cookie 失败", str(exc), parent=getattr(self, "root", None))

    def set_running(self, running):
        state = tk.DISABLED if running else tk.NORMAL
        self.start_button.config(state=state)
        self.cookie_button.config(state=state)
        self.qq_cookie_button.config(state=state)

    def on_start(self):
        artist_name = self.artist_var.get().strip()
        if not artist_name:
            messagebox.showwarning("提示", "请输入歌手名字。")
            return
        mode = self.mode_var.get()
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("提示", "任务正在执行，请稍候。")
            return
        self.pending_artist_name = artist_name
        self.pending_mode = mode
        self.clear_output()
        self.set_running(True)
        self.status_var.set("执行中...")
        self._start_worker(artist_name, mode)

    def _start_worker(self, artist_name, mode):
        self.worker_thread = threading.Thread(
            target=self._run_workflow,
            args=(artist_name, mode),
            daemon=True,
        )
        self.worker_thread.start()

    def _run_workflow(self, artist_name, mode):
        auto_create = mode == "auto"
        compare_album_diff = False
        execute_qq = mode == "qq_only"
        execute_netease = mode == "netease_only"
        service_manager = MusicApiServiceManager(
            base_dir=APP_DIR,
            bundle_dir=BUNDLE_DIR,
            log_callback=lambda msg: self.event_queue.put(("log", msg)),
        )
        try:
            if auto_create or execute_netease:
                service_manager.ensure_running(timeout_seconds=60)
            result = WORKFLOW.run_workflow(
                artist_name,
                execute_netease=execute_netease,
                execute_qq=execute_qq,
                compare_album_diff=compare_album_diff,
                auto_create=auto_create,
                output_directory=str(APP_DIR),
                log_callback=lambda msg: self.event_queue.put(("log", msg)),
                verbose_details=False,
            )
            self.event_queue.put(("done", result))
        except WORKFLOW.CookieExpiredError as exc:
            self.event_queue.put(("cookie_expired", str(exc)))
        except WORKFLOW.QQCookieExpiredError as exc:
            self.event_queue.put(("qq_cookie_expired", str(exc)))
        except Exception as exc:
            detail = traceback.format_exc()
            self.event_queue.put(("error", f"{exc}\n\n{detail}"))
        finally:
            if auto_create or execute_netease:
                service_manager.stop()

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
            elif event_type == "qq_cookie_expired":
                self._handle_qq_cookie_expired(payload)
        self.root.after(200, self._poll_events)

    def _handle_done(self, result):
        self.current_result = result
        self.status_var.set("执行完成")
        self.set_running(False)
        self.set_result_text(WORKFLOW.build_result_text(result))
        if result.album_diff:
            self.open_diff_report_button.config(state=tk.NORMAL)

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
        WORKFLOW.set_netease_cookie(cookie, persist=True)
        self.append_log("网易云 Cookie 已更新，正在重新执行。")
        self.set_running(True)
        self.status_var.set("正在使用新 Cookie 重试...")
        self._start_worker(
            self.pending_artist_name,
            self.pending_mode,
        )

    def _handle_qq_cookie_expired(self, message):
        self.set_running(False)
        self.append_log(message)
        cookie = self.prompt_qq_cookie()
        if not cookie:
            self.status_var.set("已取消")
            self.append_log("未输入新的 QQ Cookie，任务已取消。")
            return
        WORKFLOW.set_qq_cookie(cookie, persist=True)
        self.append_log("QQ Cookie 已更新，正在重新执行。")
        self.set_running(True)
        self.status_var.set("正在使用新 Cookie 重试...")
        self._start_worker(
            self.pending_artist_name,
            self.pending_mode,
        )


def main():
    instance = SingleInstance(SINGLE_INSTANCE_APP_ID, SINGLE_INSTANCE_PORT)
    if not instance.acquire():
        instance.notify_existing()
        return

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TButton", font=("Segoe UI", 10))
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"))
    except Exception:
        pass
    try:
        app = PlaylistGui(root)
        instance.set_show_callback(lambda: root.after(0, app.show_window))
        root.mainloop()
    finally:
        instance.close()


if __name__ == "__main__":
    main()
