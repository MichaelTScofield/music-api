import socket
import threading


class SingleInstance:
    def __init__(self, app_id: str, port: int, host: str = "127.0.0.1"):
        self.app_id = str(app_id)
        self.port = int(port)
        self.host = host
        self._sock = None
        self._thread = None
        self._show_callback = None
        self._pending_show = False
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def acquire(self) -> bool:
        if self._sock is not None:
            return True
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
            sock.listen(5)
        except OSError:
            sock.close()
            return False
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return True

    def notify_existing(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=1) as sock:
                sock.settimeout(1)
                sock.sendall(f"{self.app_id}\nSHOW\n".encode("utf-8"))
                try:
                    sock.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                response = sock.recv(16)
            return response.strip() == b"OK"
        except OSError:
            return False

    def set_show_callback(self, callback):
        with self._lock:
            self._show_callback = callback
            pending = self._pending_show
            self._pending_show = False
        if pending:
            self._run_show_callback(callback)

    def close(self):
        self._closed.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _serve(self):
        while not self._closed.is_set():
            try:
                conn, _addr = self._sock.accept()
            except OSError:
                break
            with conn:
                try:
                    data = conn.recv(1024).decode("utf-8", errors="replace")
                except OSError:
                    continue
                lines = [line.strip() for line in data.splitlines() if line.strip()]
                if len(lines) >= 2 and lines[0] == self.app_id and lines[1].upper() == "SHOW":
                    self._trigger_show()
                    try:
                        conn.sendall(b"OK\n")
                    except OSError:
                        pass

    def _trigger_show(self):
        with self._lock:
            callback = self._show_callback
            if callback is None:
                self._pending_show = True
                return
        self._run_show_callback(callback)

    @staticmethod
    def _run_show_callback(callback):
        try:
            callback()
        except Exception:
            pass
