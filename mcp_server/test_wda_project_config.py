import tempfile
import unittest
from pathlib import Path

from mcp_server.wda_project_config import configure_wda_project, render_wda_project


PROJECT = """// !$*UTF8*$!
{
\tobjects = {
\t\tAAAAAAAAAAAAAAAAAAAAAAAA /* WebDriverAgentRunner */ = {
\t\t\tisa = PBXNativeTarget;
\t\t\tbuildConfigurationList = BBBBBBBBBBBBBBBBBBBBBBBB /* Build configuration list for PBXNativeTarget \"WebDriverAgentRunner\" */;
\t\t\tname = WebDriverAgentRunner;
\t\t};
\t\tBBBBBBBBBBBBBBBBBBBBBBBB /* Build configuration list for PBXNativeTarget \"WebDriverAgentRunner\" */ = {
\t\t\tisa = XCConfigurationList;
\t\t\tbuildConfigurations = (
\t\t\t\tCCCCCCCCCCCCCCCCCCCCCCCC /* Debug */,
\t\t\t\tDDDDDDDDDDDDDDDDDDDDDDDD /* Release */,
\t\t\t);
\t\t};
\t\tCCCCCCCCCCCCCCCCCCCCCCCC /* Debug */ = {
\t\t\tisa = XCBuildConfiguration;
\t\t\tbuildSettings = {
\t\t\t\tDEVELOPMENT_TEAM = TEAM_PLACEHOLDER;
\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.facebook.WebDriverAgentRunner;
\t\t\t};
\t\t};
\t\tDDDDDDDDDDDDDDDDDDDDDDDD /* Release */ = {
\t\t\tisa = XCBuildConfiguration;
\t\t\tbuildSettings = {
\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.facebook.WebDriverAgentRunner;
\t\t\t};
\t\t};
\t\tEEEEEEEEEEEEEEEEEEEEEEEE /* WebDriverAgentLib */ = {
\t\t\tisa = XCBuildConfiguration;
\t\t\tbuildSettings = {
\t\t\t\tPRODUCT_BUNDLE_IDENTIFIER = com.facebook.WebDriverAgentLib;
\t\t\t};
\t\t};
\t};
}
"""


class WdaProjectConfigTests(unittest.TestCase):
    def test_updates_only_runner_debug_and_release(self):
        bundle_id = "com.example.wda.machine123"
        rendered, replacements = render_wda_project(PROJECT, bundle_id)
        self.assertEqual(replacements, 2)
        self.assertEqual(rendered.count(f"PRODUCT_BUNDLE_IDENTIFIER = {bundle_id};"), 2)
        self.assertIn("PRODUCT_BUNDLE_IDENTIFIER = com.facebook.WebDriverAgentLib;", rendered)
        self.assertIn("DEVELOPMENT_TEAM = TEAM_PLACEHOLDER;", rendered)

    def test_configure_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "WebDriverAgent.xcodeproj"
            project.mkdir()
            pbxproj = project / "project.pbxproj"
            pbxproj.write_text(PROJECT)
            bundle_id = "com.example.wda.machine123"
            self.assertTrue(
                configure_wda_project(project, bundle_id=bundle_id, validator=None)
            )
            self.assertFalse(
                configure_wda_project(project, bundle_id=bundle_id, validator=None)
            )

    def test_rejects_unrecognized_project(self):
        with self.assertRaisesRegex(ValueError, "WebDriverAgentRunner"):
            render_wda_project("// empty project\n", "com.example.wda.machine123")


if __name__ == "__main__":
    unittest.main()
