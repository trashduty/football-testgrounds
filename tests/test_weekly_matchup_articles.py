from __future__ import annotations

import sys
import unittest
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import weekly_matchup_articles as wma  # noqa: E402


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, timeout=30):  # noqa: ARG002
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


class WeeklyMatchupArticlesTests(unittest.TestCase):
    def test_parse_depth_chart_starters_returns_first_player_cell(self) -> None:
        html = """
        <table>
          <tr><th>POS</th><th>Starter</th><th>2nd</th></tr>
          <tr><td>QB</td><td>Kyler Murray</td><td>Clayton Tune</td></tr>
          <tr><td>RB</td><td>James Conner</td><td>Michael Carter</td></tr>
        </table>
        """
        self.assertEqual(
            wma.parse_depth_chart_starters(html),
            ["Kyler Murray", "James Conner"],
        )

    def test_fetch_espn_starter_injuries_matches_only_starters(self) -> None:
        slug = "ari"
        injuries_url = f"https://www.espn.com/nfl/team/injuries/_/name/{slug}"
        depth_url = f"https://www.espn.com/nfl/team/depth/_/name/{slug}"
        session = FakeSession(
            {
                injuries_url: FakeResponse(
                    """
                    <table>
                      <tr><th>Name</th><th>Status</th><th>Injury</th></tr>
                      <tr><td>Kyler Murray</td><td>Questionable</td><td>Hamstring</td></tr>
                      <tr><td>Michael Wilson</td><td>Out</td><td>Shoulder</td></tr>
                    </table>
                    """
                ),
                depth_url: FakeResponse(
                    """
                    <table>
                      <tr><th>POS</th><th>Starter</th><th>2nd</th></tr>
                      <tr><td>QB</td><td>Kyler Murray</td><td>Clayton Tune</td></tr>
                      <tr><td>RB</td><td>James Conner</td><td>Michael Carter</td></tr>
                    </table>
                    """
                ),
            }
        )

        report = wma.fetch_espn_starter_injuries("ARI", "ari", session, debug_enabled=True)

        self.assertEqual(len(report.starters), 1)
        self.assertEqual(report.starters[0].player, "Kyler Murray")
        self.assertEqual(report.starters[0].status, "Questionable")

    def test_fetch_espn_starter_injuries_debug_contains_url_and_failure(self) -> None:
        slug = "ari"
        injuries_url = f"https://www.espn.com/nfl/team/injuries/_/name/{slug}"
        session = FakeSession(
            {
                injuries_url: requests.ConnectionError("lookup failed"),
            }
        )

        report = wma.fetch_espn_starter_injuries("ARI", slug, session, debug_enabled=True)

        self.assertEqual(report.starters, [])
        self.assertEqual(len(report.debug), 1)
        self.assertEqual(report.debug[0].url, injuries_url)
        self.assertIn("lookup failed", report.debug[0].failure)

    def test_choose_stat_context_uses_previous_season_for_week_one(self) -> None:
        context = wma.choose_stat_context(2026, 1)
        self.assertEqual(context.season, 2025)
        self.assertIsNone(context.through_week)
        self.assertIn("fall back to the 2025 regular season", context.note)


if __name__ == "__main__":
    unittest.main()
