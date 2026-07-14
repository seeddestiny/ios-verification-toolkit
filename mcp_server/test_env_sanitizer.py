import unittest

from mcp_server.env_sanitizer import is_sensitive_env_name, sanitized_env


class EnvSanitizerTests(unittest.TestCase):
    def test_removes_credential_like_values(self):
        source = {
            "PATH": "/usr/bin",
            "IOS_MCP_TEAM_ID": "TEAM",
            "DS_API_KEY": "example-secret-value",
            "SERVICE_TOKEN": "example-token",
            "DB_PASSWORD": "example-password",
        }

        self.assertEqual(
            sanitized_env(source),
            {"PATH": "/usr/bin", "IOS_MCP_TEAM_ID": "TEAM"},
        )

    def test_preserves_non_secret_authentication_build_setting(self):
        self.assertFalse(is_sensitive_env_name("ENABLE_POINTER_AUTHENTICATION"))


if __name__ == "__main__":
    unittest.main()
