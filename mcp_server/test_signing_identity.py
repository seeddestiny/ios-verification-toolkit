import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp_server.signing_identity import (
    SigningIdentity,
    candidate_team_ids,
    load_cached_team_id,
    parse_signing_identities,
    remember_team_id,
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

    def test_multiple_teams_are_returned_in_stable_order(self):
        identities = [
            SigningIdentity("Apple Development: B (FGHIJ67890)", "FGHIJ67890"),
            SigningIdentity("Apple Development: A (ABCDE12345)", "ABCDE12345"),
        ]
        self.assertEqual(
            candidate_team_ids({}, identities=identities),
            ["FGHIJ67890", "ABCDE12345"],
        )

    def test_cached_team_precedes_other_candidates(self):
        identities = [
            SigningIdentity("Apple Development: A (ABCDE12345)", "ABCDE12345"),
            SigningIdentity("Apple Development: B (FGHIJ67890)", "FGHIJ67890"),
        ]
        self.assertEqual(
            candidate_team_ids(
                {}, identities=identities, cached_team_id="FGHIJ67890"
            ),
            ["FGHIJ67890", "ABCDE12345"],
        )

    def test_successful_team_is_cached_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = {"IOS_MCP_RUNTIME_DIR": directory}
            remember_team_id("ABCDE12345", source)
            self.assertEqual(load_cached_team_id(source), "ABCDE12345")
            state_file = Path(directory) / "state" / "signing-team.json"
            self.assertEqual(os.stat(state_file).st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(state_file.read_text()), {"team_id": "ABCDE12345"})

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

    @mock.patch("mcp_server.signing_identity.load_local_config")
    def test_machine_config_can_fix_team_without_cli_override(self, load_config):
        load_config.return_value = {"signing_team_id": "FGHIJ67890"}
        identities = [
            SigningIdentity("Apple Development: A (ABCDE12345)", "ABCDE12345"),
            SigningIdentity("Apple Development: B (FGHIJ67890)", "FGHIJ67890"),
        ]
        self.assertEqual(resolve_team_id(identities=identities), "FGHIJ67890")


if __name__ == "__main__":
    unittest.main()
