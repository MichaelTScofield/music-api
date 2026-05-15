import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_gui_module():
    path = PROJECT_ROOT / "music_playlist_tool" / "gui_app.py"
    spec = importlib.util.spec_from_file_location("playlist_gui_app_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeStatusVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class PlaylistGuiCookieTest(unittest.TestCase):
    def test_update_qq_cookie_persists_to_gui_cookie_file(self):
        module = load_gui_module()
        original_workflow = module.WORKFLOW

        class FakeWorkflow:
            saved_args = None

            @classmethod
            def set_qq_cookie(cls, cookie, persist=False, cookie_path=None):
                cls.saved_args = (cookie, persist, cookie_path)
                return cookie

        fake_gui = SimpleNamespace(
            prompt_qq_cookie=lambda: "new-cookie",
            append_log=lambda _message: None,
            status_var=FakeStatusVar(),
        )

        try:
            module.WORKFLOW = FakeWorkflow
            module.PlaylistGui.on_update_qq_cookie(fake_gui)
        finally:
            module.WORKFLOW = original_workflow

        self.assertEqual(
            FakeWorkflow.saved_args,
            ("new-cookie", True, str(module.QQ_COOKIE_PATH)),
        )
        self.assertEqual(fake_gui.status_var.value, "Cookie 已更新")

    def test_update_qq_cookie_shows_error_when_save_fails(self):
        module = load_gui_module()
        original_workflow = module.WORKFLOW
        original_showerror = module.messagebox.showerror
        errors = []

        class FakeWorkflow:
            @staticmethod
            def set_qq_cookie(cookie, persist=False, cookie_path=None):
                raise OSError("cannot write cookie")

        fake_gui = SimpleNamespace(
            root=object(),
            prompt_qq_cookie=lambda: "new-cookie",
            append_log=lambda _message: None,
            status_var=FakeStatusVar(),
        )

        try:
            module.WORKFLOW = FakeWorkflow
            module.messagebox.showerror = lambda title, message, parent=None: errors.append(
                (title, message, parent)
            )

            module.PlaylistGui.on_update_qq_cookie(fake_gui)
        finally:
            module.WORKFLOW = original_workflow
            module.messagebox.showerror = original_showerror

        self.assertEqual(len(errors), 1)
        self.assertIn("QQ Cookie", errors[0][0])
        self.assertIn("cannot write cookie", errors[0][1])
        self.assertIs(errors[0][2], fake_gui.root)


if __name__ == "__main__":
    unittest.main()
