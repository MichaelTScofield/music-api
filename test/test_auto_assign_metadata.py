import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AUTO_ASSIGN_PATH = ROOT / "auto_assign_tool" / "auto-assign.py"


def load_auto_assign_module():
    spec = importlib.util.spec_from_file_location("auto_assign_under_test", AUTO_ASSIGN_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def album_entry(module, source, name, publish_date="2024-01-01", track_count=1):
    return {
        "id": f"{source}-{name}",
        "name": name,
        "name_key": module.normalize_album_name(name),
        "publish_date": publish_date,
        "track_count": track_count,
        "track_titles": [{"title": f"{name} song", "track_no": 1, "artists": ["Artist"]}],
        "artist_filtered": True,
        "sources": [source],
    }


class DummyMusicApiServiceManager:
    def __init__(self, *args, **kwargs):
        self.started = False

    def ensure_running(self, timeout_seconds=60):
        self.started = True

    def stop(self):
        self.started = False


class FetchAlbumMetadataPriorityTest(unittest.TestCase):
    def setUp(self):
        self.module = load_auto_assign_module()

    def test_prefers_netease_when_netease_has_more_albums(self):
        qq_metadata = {
            "qq-shared": album_entry(self.module, "QQ 音乐", "Shared Album", "2020-01-01"),
        }
        netease_metadata = {
            "netease-shared": album_entry(self.module, "网易云", "Shared Album", "2021-01-01"),
            "netease-extra": album_entry(self.module, "网易云", "Extra Album", "2022-01-01"),
        }

        with patch.object(self.module, "fetch_qq_album_metadata", return_value=qq_metadata), \
            patch.object(self.module, "fetch_netease_album_metadata", return_value=netease_metadata), \
            patch.object(self.module, "MusicApiServiceManager", DummyMusicApiServiceManager), \
            patch.object(self.module.time, "sleep", return_value=None):
            result = self.module.fetch_album_metadata(
                "Artist",
                local_album_names=["Shared Album", "Extra Album"],
            )

        self.assertEqual({"netease-shared", "netease-extra"}, set(result))
        self.assertEqual(["网易云"], result["netease-shared"]["sources"])
        self.assertEqual("2021-01-01", result["netease-shared"]["publish_date"])
        self.assertEqual(
            {
                "primary": "网易云",
                "secondary": "QQ 音乐",
                "qq_count": 1,
                "netease_count": 2,
            },
            result.source_summary,
        )

    def test_uses_qq_when_album_counts_are_equal(self):
        self.assertEqual("qq", self.module.choose_album_metadata_primary_source(3, 3))

    def test_report_describes_actual_netease_primary_source(self):
        verification_result = {
            "mismatched_items": [
                {
                    "album_name": "Shared Album",
                    "actual_count": 0,
                    "expected_count": 1,
                    "sources": ["网易云"],
                }
            ],
            "missing_album_items": [],
            "metadata_source_summary": {
                "primary": "网易云",
                "secondary": "QQ 音乐",
                "qq_count": 33,
                "netease_count": 37,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.txt"
            with patch.object(self.module, "get_report_path", return_value=str(report_path)):
                self.module.write_album_mismatch_report(temp_dir, "Artist", verification_result)

            content = report_path.read_text(encoding="utf-8")

        self.assertIn("数据源：网易云优先，QQ 音乐补充（QQ 音乐 33 条，网易云 37 条）", content)
        self.assertNotIn("数据源：QQ 音乐优先，未匹配时网易云补充", content)

    def test_windows_duplicate_suffix_does_not_hide_accompaniment_variant(self):
        track_index_map, _ = self.module.build_track_index_map(
            [
                {"title": "爱怎么回不来", "track_no": 10},
                {"title": "爱怎么回不来 (伴奏)", "track_no": 11},
            ]
        )

        match_key = self.module.resolve_track_match_key(
            "Tank - 爱怎么回不来 (伴奏) (2)",
            track_index_map,
        )

        self.assertEqual("爱怎么回不来 (伴奏)", match_key)
        self.assertEqual([11], track_index_map[match_key])

    def test_missing_report_keeps_base_track_separate_from_accompaniment_duplicate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "11. Tank - 爱怎么回不来 (伴奏) (2).flac").write_bytes(b"")

            missing = self.module.list_netease_tracks_missing_locally(
                temp_dir,
                [
                    {"title": "爱怎么回不来", "track_no": 10},
                    {"title": "爱怎么回不来 (伴奏)", "track_no": 11},
                ],
            )

        self.assertEqual(["爱怎么回不来"], missing)


if __name__ == "__main__":
    unittest.main()
