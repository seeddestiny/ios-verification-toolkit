import os
import stat
import tempfile
import unittest
from pathlib import Path

from runtime_paths import ensure_runtime_paths, runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def test_default_is_hidden_project_directory(self):
        with tempfile.TemporaryDirectory() as project:
            paths = runtime_paths({}, project_root=project)
            self.assertEqual(paths.root, Path(project).resolve() / ".runtime")
            self.assertEqual(paths.logs, paths.root / "logs")
            self.assertEqual(paths.screenshots, paths.root / "screenshots")
            self.assertEqual(paths.state, paths.root / "state")

    def test_relative_override_is_resolved_under_project(self):
        with tempfile.TemporaryDirectory() as project:
            paths = runtime_paths(
                {"IOS_MCP_RUNTIME_DIR": "private/run"},
                project_root=project,
            )
            self.assertEqual(
                paths.root,
                Path(project).resolve() / ".runtime" / "private" / "run",
            )

    def test_absolute_override_is_preserved(self):
        with tempfile.TemporaryDirectory() as project, tempfile.TemporaryDirectory() as target:
            paths = runtime_paths(
                {"IOS_MCP_RUNTIME_DIR": target},
                project_root=project,
            )
            self.assertEqual(paths.root, Path(target).resolve())

    def test_ensure_creates_private_directories(self):
        with tempfile.TemporaryDirectory() as project:
            paths = ensure_runtime_paths({}, project_root=project)
            for path in (paths.root, paths.logs, paths.screenshots, paths.state):
                self.assertTrue(path.is_dir())
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)

    def test_rejects_project_root_override(self):
        with tempfile.TemporaryDirectory() as project:
            with self.assertRaises(ValueError):
                runtime_paths(
                    {"IOS_MCP_RUNTIME_DIR": project},
                    project_root=project,
                )

    def test_rejects_unignored_project_child(self):
        with tempfile.TemporaryDirectory() as project:
            with self.assertRaises(ValueError):
                runtime_paths(
                    {"IOS_MCP_RUNTIME_DIR": str(Path(project) / "artifacts")},
                    project_root=project,
                )

    def test_rejects_relative_path_traversal(self):
        with tempfile.TemporaryDirectory() as project:
            with self.assertRaises(ValueError):
                runtime_paths(
                    {"IOS_MCP_RUNTIME_DIR": "../../artifacts"},
                    project_root=project,
                )


if __name__ == "__main__":
    unittest.main()
