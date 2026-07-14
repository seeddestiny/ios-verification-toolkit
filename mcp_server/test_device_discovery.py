import unittest

from mcp_server.device_discovery import ConnectedDevice, DeviceSelectionError, select_target_device


class DeviceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.first = ConnectedDevice(name="Work iPhone", udid="UDID-1", transport="wired")
        self.second = ConnectedDevice(name="Lab iPhone", udid="UDID-2", transport="network")

    def test_single_device_is_selected(self):
        self.assertEqual(select_target_device([self.first]), self.first)

    def test_no_device_is_an_error(self):
        with self.assertRaises(DeviceSelectionError):
            select_target_device([])

    def test_multiple_devices_never_select_first_silently(self):
        with self.assertRaisesRegex(DeviceSelectionError, "禁止静默选择第一台") as raised:
            select_target_device([self.first, self.second])
        self.assertIn("需要你选择本轮验证设备", str(raised.exception))
        self.assertIn("Work iPhone", str(raised.exception))
        self.assertIn("Lab iPhone", str(raised.exception))

    def test_explicit_connected_udid_is_selected(self):
        self.assertEqual(
            select_target_device([self.first, self.second], explicit_udid="udid-2"),
            self.second,
        )

    def test_explicit_disconnected_udid_is_an_error(self):
        with self.assertRaisesRegex(DeviceSelectionError, "当前未连接"):
            select_target_device([self.first], explicit_udid="UDID-missing")

    def test_unique_name_substring_is_selected(self):
        self.assertEqual(
            select_target_device([self.first, self.second], name_selector="Lab"),
            self.second,
        )


if __name__ == "__main__":
    unittest.main()
