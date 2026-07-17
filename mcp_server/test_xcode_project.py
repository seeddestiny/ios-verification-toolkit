import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp_server.xcode_project import open_xcode_project, xcode_app_for_developer_dir


def _xcode(root: str) -> tuple[Path, Path]:
    app = Path(root) / "XcodeTest.app"
    developer = app / "Contents" / "Developer"
    (developer / "usr/bin").mkdir(parents=True)
    return app, developer


class XcodeProjectTests(unittest.TestCase):
    def test_resolves_xcode_app_from_developer_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            app, developer = _xcode(directory)
            self.assertEqual(xcode_app_for_developer_dir(developer), app.resolve())

    def test_opens_project_with_selected_xcode(self):
        with tempfile.TemporaryDirectory() as directory:
            app, developer = _xcode(directory)
            project = Path(directory) / "WebDriverAgent.xcodeproj"
            project.mkdir()
            runner = mock.Mock(
                return_value=subprocess.CompletedProcess([], 0, "", "")
            )
            self.assertEqual(
                open_xcode_project(project, {}, developer_dir=developer, runner=runner),
                app.resolve(),
            )
            command = runner.call_args.args[0]
            self.assertEqual(
                command,
                ["/usr/bin/open", "-a", str(app.resolve()), str(project.resolve())],
            )

    def test_rejects_missing_project(self):
        with tempfile.TemporaryDirectory() as directory:
            _, developer = _xcode(directory)
            with self.assertRaisesRegex(ValueError, "工程不存在"):
                open_xcode_project(
                    Path(directory) / "Missing.xcodeproj",
                    {},
                    developer_dir=developer,
                )


if __name__ == "__main__":
    unittest.main()
