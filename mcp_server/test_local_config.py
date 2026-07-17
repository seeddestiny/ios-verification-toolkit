import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from mcp_server.local_config import (
    clear_local_config,
    load_local_config,
    save_local_config,
    update_local_config,
)


class LocalConfigTests(unittest.TestCase):
    def test_missing_config_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_local_config(Path(directory) / "config.json"), {})

    def test_save_uses_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "config.json"
            save_local_config({"signing_team_id": "ABCDE12345"}, path)
            self.assertEqual(stat.S_IMODE(os.stat(path.parent).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(load_local_config(path), {"signing_team_id": "ABCDE12345"})

    def test_update_and_remove_do_not_expose_values_in_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            update_local_config(
                {"target_bundle_id": "com.example.test", "target_labels": ["Example"]},
                config_file=path,
            )
            update_local_config({}, remove=("target_bundle_id",), config_file=path)
            self.assertEqual(load_local_config(path), {"target_labels": ["Example"]})
            payload = json.loads(path.read_text())
            self.assertEqual(payload["version"], 1)

    def test_invalid_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with self.assertRaises(ValueError):
                save_local_config({"signing_team_id": "invalid"}, path)
            with self.assertRaises(ValueError):
                save_local_config({"unknown": True}, path)

    def test_clear_removes_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            save_local_config({"allow_insecure_npm": False}, path)
            clear_local_config(path)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
