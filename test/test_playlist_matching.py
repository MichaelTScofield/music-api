import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PLAYLIST_AUTO_PATH = ROOT / "music_playlist_tool" / "auto.py"
QQ_PLAYLIST_AUTO_PATH = ROOT / "music_playlist_tool" / "qq-auto.py"
AUTO_ASSIGN_PATH = ROOT / "auto_assign_tool" / "auto-assign.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ArtistNameMatchingTest(unittest.TestCase):
    def test_tank_matches_exact_case_insensitive_names(self):
        for path, module_name in [
            (PLAYLIST_AUTO_PATH, "playlist_auto_artist_match_test"),
            (QQ_PLAYLIST_AUTO_PATH, "qq_playlist_auto_artist_match_test"),
            (AUTO_ASSIGN_PATH, "auto_assign_artist_match_test"),
        ]:
            with self.subTest(path=path):
                module = load_module(path, module_name)

                self.assertTrue(module.artist_name_matches("Tank", "Tank"))
                self.assertTrue(module.artist_name_matches("Tank", "TANK"))

    def test_tank_does_not_match_prefixed_different_artists(self):
        for path, module_name in [
            (PLAYLIST_AUTO_PATH, "playlist_auto_artist_prefix_test"),
            (QQ_PLAYLIST_AUTO_PATH, "qq_playlist_auto_artist_prefix_test"),
            (AUTO_ASSIGN_PATH, "auto_assign_artist_prefix_test"),
        ]:
            with self.subTest(path=path):
                module = load_module(path, module_name)

                for candidate in ["TanKee", "TANK010", "Tank B Music", "Tanks General"]:
                    self.assertFalse(module.artist_name_matches("Tank", candidate))


class NeteaseMatchingTest(unittest.TestCase):
    def setUp(self):
        self.module = load_module(PLAYLIST_AUTO_PATH, "playlist_auto_matching_test")

    def test_direct_netease_song_id_is_added_without_searching(self):
        song = {
            "name": "从今以后",
            "artist": "Tank",
            "source_artists": ["Tank"],
            "song_id": "150354",
            "source_platform": "netease",
            "album": "延长比赛",
            "album_id": "15177",
            "album_song_count": 10,
            "album_publish_time": 0,
        }

        with patch.object(
            self.module,
            "search_netease_song",
            side_effect=AssertionError("direct Netease IDs must not use search"),
        ), patch.object(self.module, "add_netease_batch") as add_batch, patch.object(
            self.module,
            "get_netease_playlist_song_ids",
            return_value={"150354"},
        ), patch.object(self.module.time, "sleep", return_value=None):
            result = self.module.add_netease_songs("playlist-id", [song])

        add_batch.assert_called_once_with("playlist-id", ["150354"])
        self.assertEqual(1, result["matched_count"])
        self.assertEqual(0, result["missing_count"])
        self.assertEqual(1, result["added_song_count"])
        self.assertEqual([], result["missing_reason_lines"])

    def test_search_scoring_rejects_wrong_tank_prefix_artists(self):
        shine_candidate = {
            "name": "Time To Shine",
            "ar": [{"name": "Tank B Music"}, {"name": "Astral Mind Music"}],
            "al": {"name": "Time To Shine"},
        }
        who_candidate = {
            "name": "我是谁",
            "ar": [{"name": "TANK010"}, {"name": "劉二嘉"}],
            "al": {"name": "翻唱-010"},
        }

        self.assertEqual(
            -1,
            self.module.score_netease_song(
                shine_candidate,
                self.module.build_title_candidates("SHINE"),
                "Tank",
                "SHINE",
            ),
        )
        self.assertEqual(
            -1,
            self.module.score_netease_song(
                who_candidate,
                self.module.build_title_candidates("是谁"),
                "Tank",
                "第三回合",
            ),
        )


if __name__ == "__main__":
    unittest.main()
