import unittest

from mcp_server.wda_bundle_id import build_wda_bundle_id


class WdaBundleIdTests(unittest.TestCase):
    def test_same_machine_seed_is_stable(self):
        first = build_wda_bundle_id({}, machine_seed="mac-a")
        second = build_wda_bundle_id({}, machine_seed="mac-a")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^com\.iosdevice\.mcp\.WebDriverAgentRunner\.m[0-9a-f]{12}$")
        self.assertNotIn("mac-a", first)

    def test_different_machine_seeds_get_different_bundle_ids(self):
        self.assertNotEqual(
            build_wda_bundle_id({}, machine_seed="mac-a"),
            build_wda_bundle_id({}, machine_seed="mac-b"),
        )

    def test_explicit_bundle_id_keeps_compatibility(self):
        self.assertEqual(
            build_wda_bundle_id(
                {"IOS_MCP_WDA_BUNDLE_ID": "com.example.existing.WebDriverAgentRunner"},
                machine_seed="ignored",
            ),
            "com.example.existing.WebDriverAgentRunner",
        )

    def test_custom_prefix_still_uses_machine_suffix(self):
        value = build_wda_bundle_id(
            {"IOS_MCP_WDA_BUNDLE_PREFIX": "com.example.wda"},
            machine_seed="mac-a",
        )

        self.assertRegex(value, r"^com\.example\.wda\.m[0-9a-f]{12}$")

    def test_invalid_override_is_rejected(self):
        with self.assertRaises(ValueError):
            build_wda_bundle_id(
                {"IOS_MCP_WDA_BUNDLE_ID": "invalid bundle id"},
                machine_seed="ignored",
            )


if __name__ == "__main__":
    unittest.main()
