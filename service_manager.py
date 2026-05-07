import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import requests


def default_log(message):
    print(message)


class MusicApiServiceManager:
    def __init__(self, base_dir=None, bundle_dir=None, port=3001, log_callback=None):
        self.base_dir = Path(base_dir or Path(__file__).resolve().parent)
        self.bundle_dir = Path(bundle_dir or self.base_dir)
        self.port = port
        self.log = log_callback or default_log
        self.process = None
        self.started_by_us = False
        self.runtime_root = self._resolve_runtime_root()
        self.service_root = self._resolve_service_root()

    def _resolve_runtime_root(self):
        candidates = []
        for root in (self.base_dir, self.bundle_dir, self.base_dir.parent, self.bundle_dir.parent):
            candidate = root / "runtime"
            if candidate not in candidates:
                candidates.append(candidate)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return self.base_dir / "runtime"

    def _resolve_service_root(self):
        runtime_service_root = self.runtime_root / "music-api"
        if (runtime_service_root / "app.js").exists():
            return runtime_service_root
        for root in (self.base_dir, self.bundle_dir, self.base_dir.parent, self.bundle_dir.parent):
            if (root / "app.js").exists():
                return root
        return self.base_dir

    def _is_port_open(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            return sock.connect_ex(("127.0.0.1", self.port)) == 0

    def _health_check(self):
        try:
            r = requests.get(
                f"http://127.0.0.1:{self.port}/hello",
                timeout=2,
            )
            return r.ok
        except requests.RequestException:
            return False

    def _iter_start_commands(self):
        service_exe = self.runtime_root / "music-api-service.exe"
        if service_exe.exists():
            yield {
                "label": "内置服务EXE",
                "command": [str(service_exe)],
                "cwd": str(service_exe.parent),
            }

        bundled_node = self.runtime_root / "node" / "node.exe"
        app_js = self.service_root / "app.js"
        if bundled_node.exists() and app_js.exists():
            yield {
                "label": "内置Node运行时",
                "command": [str(bundled_node), str(app_js)],
                "cwd": str(self.service_root),
            }

        local_node = shutil.which("node")
        if local_node and app_js.exists():
            yield {
                "label": "系统Node",
                "command": [local_node, str(app_js)],
                "cwd": str(self.service_root),
            }

    def ensure_running(self, timeout_seconds=30):
        # 仅当 /hello 可用时视为已就绪；端口被占用但无健康响应时仍尝试启动（便于暴露端口冲突）
        if self._health_check():
            self.log("检测到本地 music-api 服务已在运行。")
            self.started_by_us = False
            return
        if self._is_port_open():
            self.log(
                f"提示：{self.port} 端口已打开，但健康检查未通过。"
                "若随后启动失败，请检查是否有其他程序占用该端口或并非本 music-api。"
            )

        startup_errors = []
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        # 子进程继承的 PORT/HOST 会导致监听端口或绑定地址与健康检查不一致（例如全局 PORT=8080）
        env["PORT"] = str(self.port)
        env.pop("HOST", None)

        for candidate in self._iter_start_commands():
            self.log(f"正在启动服务：{candidate['label']}")
            try:
                self.process = subprocess.Popen(
                    candidate["command"],
                    cwd=candidate["cwd"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                self.started_by_us = True
                if self._wait_until_ready(timeout_seconds):
                    self.log("本地 music-api 服务已启动。")
                    return
                startup_errors.append(f"{candidate['label']} 启动后健康检查失败")
                self.stop()
            except Exception as exc:
                startup_errors.append(f"{candidate['label']} 启动失败: {exc}")
                self.stop()

        raise RuntimeError(
            "无法启动本地 music-api 服务。\n"
            "请确认已准备以下任一方案：\n"
            "1. runtime/music-api-service.exe\n"
            "2. runtime/node/node.exe + runtime/music-api/app.js\n"
            "3. 本机 PATH 中可用的 node.exe\n\n"
            + "\n".join(startup_errors)
        )

    def _wait_until_ready(self, timeout_seconds):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._health_check():
                return True
            if self.process and self.process.poll() is not None:
                return False
            time.sleep(1)
        return False

    def stop(self):
        if not self.started_by_us or not self.process:
            return

        if self.process.poll() is None:
            self.log("正在关闭本次启动的 music-api 服务...")
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

        self.process = None
        self.started_by_us = False

    def __enter__(self):
        self.ensure_running()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop()
