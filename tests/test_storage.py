import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class StorageTests(unittest.TestCase):

    def test_save_atomically_replaces_json_without_temp_files(self):
        from modules import storage

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir)
            with patch.object(storage, "BOT_MEMORY_DIR", memory_dir):
                storage.save("state.json", {"mode": "silent"})
                self.assertEqual(storage.load("state.json", {}), {"mode": "silent"})
                self.assertEqual(list(memory_dir.glob("*.tmp")), [])

    def test_failed_replace_keeps_previous_json_and_raises(self):
        from modules import storage

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "state.json"
            target.write_text(json.dumps({"mode": "verbose"}), encoding="utf-8")

            with patch.object(storage.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    storage.save_json_path(target, {"mode": "silent"})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"mode": "verbose"})
            self.assertEqual(list(target.parent.glob("*.tmp")), [])

    def test_football_memory_write_failure_is_visible_to_caller(self):
        from modules import football_memory

        with patch.object(
            football_memory,
            "save_json_path",
            side_effect=OSError("read-only filesystem"),
        ):
            with self.assertRaises(OSError):
                football_memory.save_memory({"metadata": {}, "leagues": {}, "teams": {}, "matches": {}})


if __name__ == "__main__":
    unittest.main()
