import subprocess
import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from mcp_server.xcode_resolver import resolve_developer_dir


def _developer_dir(root: str, name: str, tool: str = "xcodebuild") -> Path:
    path = Path(root) / name / "Contents" / "Developer"
    executable = path / "usr" / "bin" / tool
    executable.parent.mkdir(parents=True)
    executable.touch()
    return path


class XcodeResolverTests(unittest.TestCase):
    def test_explicit_developer_dir_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            developer = _developer_dir(directory, "XcodeExplicit.app")
            self.assertEqual(
                resolve_developer_dir(
                    "xcodebuild", {"DEVELOPER_DIR": str(developer)}
                ),
                developer.resolve(),
            )

    @mock.patch("mcp_server.xcode_resolver.subprocess.run")
    def test_invalid_command_line_tools_falls_back_to_unique_xcode(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["xcode-select", "-p"], 0, "/Library/Developer/CommandLineTools\n", ""
        )
        with tempfile.TemporaryDirectory() as directory:
            developer = _developer_dir(directory, "Xcode26.app")
            self.assertEqual(
                resolve_developer_dir(
                    "xcodebuild", {}, candidates=[developer]
                ),
                developer.resolve(),
            )

    @mock.patch("mcp_server.xcode_resolver.subprocess.run")
    def test_multiple_xcodes_require_explicit_selection(self, run):
        run.return_value = subprocess.CompletedProcess(
            ["xcode-select", "-p"], 0, "/Library/Developer/CommandLineTools\n", ""
        )
        with tempfile.TemporaryDirectory() as directory:
            first = _developer_dir(directory, "XcodeA.app")
            second = _developer_dir(directory, "XcodeB.app")
            with self.assertRaisesRegex(RuntimeError, "多个可用 Xcode"):
                resolve_developer_dir(
                    "xcodebuild", {}, candidates=[first, second]
                )

    def test_invalid_explicit_path_does_not_silently_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "DEVELOPER_DIR 中找不到"):
                resolve_developer_dir(
                    "xcodebuild", {"DEVELOPER_DIR": directory}, candidates=[]
                )

    @mock.patch("mcp_server.xcode_resolver.load_local_config")
    def test_machine_config_precedes_automatic_discovery(self, load_config):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            developer = _developer_dir(directory, "XcodeConfigured.app")
            load_config.return_value = {"xcode_developer_dir": str(developer)}
            self.assertEqual(
                resolve_developer_dir("xcodebuild", candidates=[]),
                developer.resolve(),
            )


if __name__ == "__main__":
    unittest.main()
