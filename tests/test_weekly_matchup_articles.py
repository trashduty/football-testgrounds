from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd
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
        self.assertEqual(report.status, "ok_starters_found")

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

    def test_fetch_espn_starter_injuries_sets_status_no_slug(self) -> None:
        report = wma.fetch_espn_starter_injuries("ARI", None, FakeSession({}), debug_enabled=False)
        self.assertEqual(report.status, "no_slug")
        self.assertEqual(report.starters, [])
        # No debug events when debug_enabled=False
        self.assertEqual(report.debug, [])

    def test_fetch_espn_starter_injuries_status_injury_fetch_failed(self) -> None:
        slug = "ari"
        injuries_url = f"https://www.espn.com/nfl/team/injuries/_/name/{slug}"
        session = FakeSession({injuries_url: requests.ConnectionError("timeout")})
        report = wma.fetch_espn_starter_injuries("ARI", slug, session, debug_enabled=False)
        self.assertEqual(report.status, "injury_fetch_failed")
        self.assertEqual(report.starters, [])
        # No debug events without flag
        self.assertEqual(report.debug, [])

    def test_fetch_espn_starter_injuries_status_no_starter_match(self) -> None:
        """Both pages parse fine but no injured players appear on the depth chart."""
        slug = "ari"
        injuries_url = f"https://www.espn.com/nfl/team/injuries/_/name/{slug}"
        depth_url = f"https://www.espn.com/nfl/team/depth/_/name/{slug}"
        session = FakeSession(
            {
                injuries_url: FakeResponse(
                    """
                    <table>
                      <tr><th>Name</th><th>Status</th><th>Injury</th></tr>
                      <tr><td>Backup Player</td><td>Out</td><td>Knee</td></tr>
                    </table>
                    """
                ),
                depth_url: FakeResponse(
                    """
                    <table>
                      <tr><th>POS</th><th>Starter</th><th>2nd</th></tr>
                      <tr><td>QB</td><td>Kyler Murray</td><td>Clayton Tune</td></tr>
                    </table>
                    """
                ),
            }
        )
        report = wma.fetch_espn_starter_injuries("ARI", slug, session, debug_enabled=False)
        self.assertEqual(report.status, "no_starter_match")
        self.assertEqual(report.starters, [])

    def test_choose_stat_context_uses_previous_season_for_week_one(self) -> None:
        context = wma.choose_stat_context(2026, 1)
        self.assertEqual(context.season, 2025)
        self.assertIsNone(context.through_week)
        self.assertIn("fall back to the 2025 regular season", context.note)

    def test_format_line_positive(self) -> None:
        self.assertEqual(wma.format_line(11.5), "+11.5")

    def test_format_line_negative(self) -> None:
        self.assertEqual(wma.format_line(-3.5), "-3.5")

    def test_format_line_none(self) -> None:
        self.assertEqual(wma.format_line(None), "N/A")

    def test_extract_team_logo_returns_url(self) -> None:
        row = pd.Series({"logo": "https://example.com/logo.png", "team": "ARI"})
        self.assertEqual(wma.extract_team_logo(row), "https://example.com/logo.png")

    def test_extract_team_logo_returns_none_when_absent(self) -> None:
        row = pd.Series({"team": "ARI"})
        self.assertIsNone(wma.extract_team_logo(row))

    def test_build_model_prediction_sentence_with_price(self) -> None:
        game_rows = pd.DataFrame([
            {
                "team": "ARI",
                "model_cover_probability": 0.5577,
                "best_cover_probability": None,
                "best_line": 11.5,
                "best_price": -110,
                "edge_numeric": 0.0339,
            }
        ])
        team_names = {"ARI": "Arizona Cardinals"}
        result = wma.build_model_prediction(game_rows, 3, team_names)
        self.assertIn("Arizona Cardinals", result)
        self.assertIn("55.77%", result)
        self.assertIn("+11.5", result)
        self.assertIn("-110", result)
        self.assertIn("3.39%", result)
        self.assertIn("does not meet our threshold of 4% to bet", result)
        # CTA sentence
        self.assertIn("Our model shows edges of at least 4% on 3 games this week", result)
        self.assertIn("btb-analytics.com/member-access", result)

    def test_build_model_prediction_negative_edge_shows_minus_sign(self) -> None:
        game_rows = pd.DataFrame([
            {
                "team": "ARI",
                "model_cover_probability": 0.48,
                "best_cover_probability": None,
                "best_line": 11.5,
                "best_price": -110,
                "edge_numeric": -0.04181,
            }
        ])
        team_names = {"ARI": "Arizona Cardinals"}
        result = wma.build_model_prediction(game_rows, 0, team_names)
        self.assertIn("-4.18%", result)
        self.assertIn("does not meet our threshold of 4% to bet", result)

    def test_build_model_prediction_meets_threshold(self) -> None:
        game_rows = pd.DataFrame([
            {
                "team": "KC",
                "model_cover_probability": 0.60,
                "best_cover_probability": None,
                "best_line": -7.0,
                "best_price": -115,
                "edge_numeric": 0.05,
            }
        ])
        team_names = {"KC": "Kansas City Chiefs"}
        result = wma.build_model_prediction(game_rows, 2, team_names)
        self.assertIn("meets our threshold of 4% to bet", result)

    def test_build_model_prediction_best_cover_probability_takes_precedence(self) -> None:
        game_rows = pd.DataFrame([
            {
                "team": "ARI",
                "model_cover_probability": 0.48,    # should be ignored
                "best_cover_probability": 0.5577,   # should be used
                "best_line": 11.5,
                "best_price": -110,
                "edge_numeric": 0.0339,
            }
        ])
        team_names = {"ARI": "Arizona Cardinals"}
        result = wma.build_model_prediction(game_rows, 1, team_names)
        self.assertIn("55.77%", result)
        self.assertNotIn("48.", result)


if __name__ == "__main__":
    unittest.main()
