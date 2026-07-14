import argparse
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ios_log_tool


class IOSLogToolTests(unittest.TestCase):
    def test_session_validation(self):
        self.assertEqual(ios_log_tool._validate_session("run-1.example"), "run-1.example")
        for invalid in ("", "../escape", "contains space", "x" * 65):
            with self.assertRaises(ValueError):
                ios_log_tool._validate_session(invalid)

    def test_tail_filters_before_limiting(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "capture.log"
            log.write_text("skip\nMARK first\nMARK second\n")
            self.assertEqual(ios_log_tool._tail(log, 1, "MARK"), "MARK second")

    def test_state_file_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            ios_log_tool._write_json(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text()), {"ok": True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_capture_supervisor_uses_only_idevicesyslog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "idevicesyslog"
            executable.write_text("#!/bin/sh\nprintf 'MARK lightweight-log\\n'\nsleep 2\n")
            executable.chmod(0o700)
            state = root / "state.json"
            output = root / "capture.log"
            args = argparse.Namespace(
                session="run-1",
                udid="synthetic-device",
                output=str(output),
                state_file=str(state),
                max_seconds=1,
            )
            with mock.patch.dict(os.environ, {"PATH": f"{root}:{os.environ.get('PATH', '')}"}):
                self.assertEqual(ios_log_tool._capture(args), 0)
            payload = json.loads(state.read_text())
            self.assertEqual(payload["status"], "expired")
            self.assertIn("MARK lightweight-log", output.read_text())


if __name__ == "__main__":
    unittest.main()
