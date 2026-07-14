import json
import tempfile
import unittest
from pathlib import Path

import ios_app_tool


class IOSAppToolTests(unittest.TestCase):
    def test_build_command_enforces_offline_package_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "Example.xcworkspace"
            workspace.mkdir()
            command = ios_app_tool.build_command(
                Path("/tmp/xcodebuild"),
                workspace=str(workspace),
                scheme="Example",
                configuration="Debug",
                destination="generic/platform=iOS",
            )
        for flag in ios_app_tool.SAFE_PACKAGE_FLAGS:
            self.assertIn(flag, command)
        self.assertNotIn("-allowProvisioningUpdates", command)
        self.assertEqual(command[-1], "build")

    def test_build_command_rejects_invalid_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "Example.xcodeproj"
            project.mkdir()
            with self.assertRaises(ValueError):
                ios_app_tool.build_command(
                    Path("/tmp/xcodebuild"),
                    project=str(project),
                    scheme="Example",
                    configuration="Debug",
                    destination="generic/platform=iOS",
                    build_settings=["-allowProvisioningUpdates"],
                )

    def test_environment_rejects_credential_like_names(self):
        with self.assertRaises(ValueError):
            ios_app_tool._decode_environment(json.dumps({"API_TOKEN": "hidden"}))
        self.assertEqual(
            ios_app_tool._decode_environment(json.dumps({"IOS_CODE_VERIFY_SCENARIO": "empty"})),
            {"IOS_CODE_VERIFY_SCENARIO": "empty"},
        )

    def test_launch_command_uses_devicectl_without_wda(self):
        command = ios_app_tool.launch_command(
            Path("/tmp/devicectl"),
            device="device-id",
            bundle_id="com.example.app",
            environment={"VERIFY_RUN": "run-1"},
            arguments=["--example"],
            terminate_existing=True,
        )
        self.assertEqual(command[:4], ["/tmp/devicectl", "device", "process", "launch"])
        self.assertIn("--environment-variables", command)
        self.assertIn("--terminate-existing", command)
        self.assertNotIn("appium", " ".join(command).lower())
        self.assertNotIn("wda", " ".join(command).lower())

    def test_resolve_explicit_developer_dir(self):
        with tempfile.TemporaryDirectory() as directory:
            developer = Path(directory)
            tool = developer / "usr" / "bin" / "devicectl"
            tool.parent.mkdir(parents=True)
            tool.touch()
            self.assertEqual(
                ios_app_tool.resolve_developer_dir("devicectl", {"DEVELOPER_DIR": str(developer)}),
                developer.resolve(),
            )

    def test_top_level_apps_excludes_nested_app_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            derived = Path(directory)
            main = derived / "Build" / "Products" / "Debug-iphoneos" / "Example.app"
            nested = main / "PlugIns" / "Nested.app"
            nested.mkdir(parents=True)
            self.assertEqual(ios_app_tool._top_level_apps(str(derived)), [str(main.resolve())])


if __name__ == "__main__":
    unittest.main()
