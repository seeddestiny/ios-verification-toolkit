import unittest

from mcp_server.signing_identity import (
    SigningIdentity,
    parse_signing_identities,
    resolve_certificate_common_name,
    resolve_team_id,
)


class SigningIdentityTests(unittest.TestCase):
    def test_parses_only_development_identities(self):
        output = """
  1) AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "Apple Development: Developer A (ABCDE12345)"
  2) BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB "Apple Distribution: Company (ZZZZZ99999)"
  3) CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC "iPhone Developer: Developer B (FGHIJ67890)"
     3 valid identities found
"""
        self.assertEqual(
            parse_signing_identities(output),
            [
                SigningIdentity("Apple Development: Developer A (ABCDE12345)", "ABCDE12345"),
                SigningIdentity("iPhone Developer: Developer B (FGHIJ67890)", "FGHIJ67890"),
            ],
        )

    def test_explicit_team_id_wins_without_discovery(self):
        self.assertEqual(resolve_team_id({"IOS_MCP_TEAM_ID": "ABCDE12345"}, identities=[]), "ABCDE12345")

    def test_unique_team_is_inferred(self):
        identities = [SigningIdentity("Apple Development: Developer (ABCDE12345)", "ABCDE12345")]
        self.assertEqual(resolve_team_id({}, identities=identities), "ABCDE12345")

    def test_existing_wda_team_wins_when_multiple_identities_exist(self):
        identities = [
            SigningIdentity("Apple Development: A (ABCDE12345)", "ABCDE12345"),
            SigningIdentity("Apple Development: B (FGHIJ67890)", "FGHIJ67890"),
        ]
        self.assertEqual(
            resolve_team_id(
                {},
                identities=identities,
                wda_team_ids=["FGHIJ67890"],
            ),
            "FGHIJ67890",
        )

    def test_multiple_teams_require_explicit_selection(self):
        identities = [
            SigningIdentity("Apple Development: A (ABCDE12345)", "ABCDE12345"),
            SigningIdentity("Apple Development: B (FGHIJ67890)", "FGHIJ67890"),
        ]
        with self.assertRaisesRegex(ValueError, "IOS_MCP_TEAM_ID"):
            resolve_team_id({}, identities=identities)

    def test_certificate_name_matches_selected_team(self):
        identities = [
            SigningIdentity("Apple Development: A (ABCDE12345)", "ABCDE12345"),
            SigningIdentity("Apple Development: B (FGHIJ67890)", "FGHIJ67890"),
        ]
        self.assertEqual(
            resolve_certificate_common_name(
                {"IOS_MCP_TEAM_ID": "FGHIJ67890"},
                identities=identities,
            ),
            "Apple Development: B (FGHIJ67890)",
        )

    def test_invalid_explicit_team_id_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve_team_id({"IOS_MCP_TEAM_ID": "invalid"}, identities=[])


if __name__ == "__main__":
    unittest.main()
