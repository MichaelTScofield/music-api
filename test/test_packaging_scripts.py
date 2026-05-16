from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackagingScriptTest(unittest.TestCase):
    def test_auto_assign_build_prepares_runtime_bundle(self):
        script = (ROOT / "auto_assign_tool" / "build_exe.bat").read_text(encoding="utf-8")

        self.assertIn("prepare_runtime_bundle.bat", script)


if __name__ == "__main__":
    unittest.main()
