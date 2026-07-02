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

    def test_load_stat_inputs_with_fallback_keeps_2025_when_weekly_file_missing(self) -> None:
        original_read = wma.read_remote_parquet

        def fake_read(url, columns):
            if "play_by_play_2025.parquet" in url:
                row = {column: None for column in columns}
                row.update(
                    {
                        "game_id": "2025_01_ARI_SEA",
                        "season": 2025,
                        "week": 1,
                        "season_type": "REG",
                        "posteam": "ARI",
                        "defteam": "SEA",
                        "home_team": "SEA",
                        "away_team": "ARI",
                        "pass": 1,
                        "rush": 0,
                        "sack": 0,
                        "passing_yards": 10,
                        "rushing_yards": 0,
                        "epa": 0.1,
                        "field_goal_attempt": 0,
                        "touchdown": 0,
                        "special": 0,
                        "td_team": "",
                    }
                )
                return pd.DataFrame([row], columns=columns)
            if "player_stats_2025.parquet" in url:
                raise RuntimeError("404")
            raise AssertionError(f"Unexpected url: {url}")

        try:
            wma.read_remote_parquet = fake_read
            context, pbp, weekly = wma.load_stat_inputs_with_fallback(2026, 1)
        finally:
            wma.read_remote_parquet = original_read

        self.assertEqual(context.season, 2025)
        self.assertFalse(pbp.empty)
        self.assertTrue(weekly.empty)

    def test_compute_team_metrics_derives_special_teams_tds_from_pbp(self) -> None:
        pbp = pd.DataFrame(
            [
                {
                    "game_id": "g1",
                    "season": 2025,
                    "week": 1,
                    "season_type": "REG",
                    "posteam": "ARI",
                    "defteam": "SEA",
                    "home_team": "SEA",
                    "away_team": "ARI",
                    "pass": 1,
                    "rush": 0,
                    "sack": 0,
                    "passing_yards": 8,
                    "rushing_yards": 0,
                    "epa": 0.2,
                    "field_goal_attempt": 0,
                    "field_goal_result": "",
                    "kick_distance": None,
                    "kicker_player_name": None,
                    "touchdown": 1,
                    "special": 1,
                    "td_team": "ARI",
                },
                {
                    "game_id": "g1",
                    "season": 2025,
                    "week": 1,
                    "season_type": "REG",
                    "posteam": "SEA",
                    "defteam": "ARI",
                    "home_team": "SEA",
                    "away_team": "ARI",
                    "pass": 1,
                    "rush": 0,
                    "sack": 1,
                    "passing_yards": 12,
                    "rushing_yards": 0,
                    "epa": -0.1,
                    "field_goal_attempt": 0,
                    "field_goal_result": "",
                    "kick_distance": None,
                    "kicker_player_name": None,
                    "touchdown": 0,
                    "special": 0,
                    "td_team": "",
                },
            ]
        )

        metrics = wma.compute_team_metrics(pbp, pd.DataFrame())
        ari_row = metrics.set_index("team").loc["ARI"]
        self.assertEqual(int(ari_row["special_teams_tds"]), 1)

    def test_format_line_positive(self) -> None:
        self.assertEqual(wma.format_line(11.5), "+11.5")

    def test_format_line_negative(self) -> None:
        self.assertEqual(wma.format_line(-3.5), "-3.5")

    def test_format_line_none(self) -> None:
        self.assertEqual(wma.format_line(None), "N/A")

    def test_extract_team_logo_returns_url(self) -> None:
        row = pd.Series({"logo": "https://example.com/logo.png", "team": "ARI"})
        self.assertEqual(wma.extract_team_logo(row), "https://example.com/logo.png")

    def test_extract_team_logo_prefers_team_logo_espn(self) -> None:
        row = pd.Series(
            {
                "team_logo_espn": "https://a.espncdn.com/ari.png",
                "logo": "https://example.com/fallback.png",
                "team": "ARI",
            }
        )
        self.assertEqual(wma.extract_team_logo(row), "https://a.espncdn.com/ari.png")

    def test_extract_team_logo_returns_none_when_absent(self) -> None:
        row = pd.Series({"team": "ARI"})
        self.assertIsNone(wma.extract_team_logo(row))

    def test_build_article_matches_requested_matchup_format_updates(self) -> None:
        game_rows = pd.DataFrame(
            [
                {
                    "team": "ARI",
                    "game": "ARI@LAC",
                    "game_date_est": pd.Timestamp("2026-09-12"),
                    "game_time_est": "4:05",
                    "market_line": 11.0,
                    "best_line": 11.5,
                    "best_book": "DraftKings",
                    "best_cover_probability": 0.558,
                    "model_cover_probability": 0.558,
                    "best_price": -110,
                    "edge_numeric": 0.025,
                    "team_logo_espn": "https://a.espncdn.com/i/teamlogos/nfl/500/ari.png",
                },
                {
                    "team": "LAC",
                    "game": "ARI@LAC",
                    "game_date_est": pd.Timestamp("2026-09-12"),
                    "game_time_est": "4:05",
                    "market_line": -11.0,
                    "best_line": -9.5,
                    "best_book": "BetRivers",
                    "best_cover_probability": 0.579,
                    "model_cover_probability": 0.579,
                    "best_price": -115,
                    "edge_numeric": 0.044,
                    "team_logo_espn": "https://a.espncdn.com/i/teamlogos/nfl/500/lac.png",
                },
            ]
        )
        metrics = pd.DataFrame([{"team": "ARI"}, {"team": "LAC"}])
        model_frame = pd.DataFrame(
            [
                {
                    "Team": "ARI",
                    "Offensive Expected Points (Season)": 0.10,
                    "Defensive Expected Points (Season)": 0.20,
                    "Offensive Success Rate (%)": 48.2,
                    "Defensive Success Rate (%)": 42.1,
                    "QB Expected Points Added (Last 10 games)": 0.02,
                    "Offensive Eckel Rate Over Expected (%)": 50.0,
                    "Defensive Eckel Rate Over Expected (%)": 45.5,
                    "Qbname": "J.Brissett",
                },
                {
                    "Team": "LAC",
                    "Offensive Expected Points (Season)": 0.35,
                    "Defensive Expected Points (Season)": -0.10,
                    "Offensive Success Rate (%)": 52.4,
                    "Defensive Success Rate (%)": 49.1,
                    "QB Expected Points Added (Last 10 games)": 0.24,
                    "Offensive Eckel Rate Over Expected (%)": 53.6,
                    "Defensive Eckel Rate Over Expected (%)": 47.8,
                    "Qbname": "J.Herbert",
                },
            ]
        )
        team_names = {"ARI": "Arizona Cardinals", "LAC": "Los Angeles Chargers"}
        injuries = {
            "ARI": wma.TeamInjuryReport(team="ARI", status="ok_no_injuries"),
            "LAC": wma.TeamInjuryReport(team="LAC", status="ok_no_injuries"),
        }
        article, _ = wma.build_article(
            "ARI@LAC",
            game_rows,
            metrics,
            {},
            team_names,
            pd.Series({"stadium": "SoFi Stadium"}),
            wma.StatContext(season=2026, through_week=1, note="test"),
            {},
            injuries,
            edge_game_count=6,
            model_ranks_df=wma.model_ranks(model_frame),
        )

        self.assertLess(
            article.index("# Arizona Cardinals vs Los Angeles Chargers Prediction For 09/12/2026"),
            article.index("<p align=\"center\">"),
        )
        self.assertIn(
            "<p align=\"center\"><img src=\"https://a.espncdn.com/i/teamlogos/nfl/500/ari.png\" alt=\"Arizona Cardinals\" width=\"84\" /> <strong>vs</strong> <img src=\"https://a.espncdn.com/i/teamlogos/nfl/500/lac.png\" alt=\"Los Angeles Chargers\" width=\"84\" /></p>",
            article,
        )
        self.assertIn("| Team name | Best Spread/Odds | Best Book | Model Cover% | Edge | BTB Advice |", article)
        self.assertIn("| Arizona Cardinals | +11.5 (-110) | DraftKings | 55.8% | 2.50% | Lean – doesn’t meet our edge criteria to fully bet |", article)
        self.assertIn("| Los Angeles Chargers | -9.5 (-115) | BetRivers | 57.9% | 4.40% | Bet |", article)
        self.assertLess(article.index("| Team name |"), article.index("## The Bottom Line"))
        self.assertIn("## The Bottom Line\nArizona Cardinals takes on Los Angeles Chargers at SoFi Stadium and", article)
        self.assertNotIn("*Line: opened", article)
        self.assertIn(
            "Our model uses data points that correlate best with a team covering. Here’s how these two teams stack up in some of those categories",
            article,
        )
        self.assertIn("## Why The Pick", article)
        self.assertNotIn("## Why the Pick", article)
        self.assertNotIn("## Tale of the Tape", article)
        self.assertIn("| Offensive Eckel Rate Over Expected* |", article)
        self.assertIn("| Defensive Eckel Rate Over Expected |", article)
        self.assertIn(
            "\\*The rate of possessions that result in a big play touchdown or 1st down inside the opponent’s 40 yard line",
            article,
        )
        self.assertNotIn("This rides on", article)
        self.assertNotIn("The edge is real but slim", article)
        self.assertNotIn("## The Risk", article)
        self.assertIn("## Best Bets Of The Week", article)

    def test_build_article_uses_verdict_first_structure(self) -> None:
        game_rows = pd.DataFrame(
            [
                {
                    "team": "ARI",
                    "game": "ARI@SEA",
                    "game_date_est": pd.Timestamp("2026-09-10"),
                    "game_time_est": "8:20",
                    "market_line": -3.5,
                    "best_line": -3.0,
                    "best_book": "DraftKings",
                    "best_cover_probability": None,
                    "model_cover_probability": 0.56,
                    "best_price": -110,
                    "edge_numeric": 0.05,
                    "Offensive Eckel Rate Over Expected (%)": 2.0,
                    "Defensive Eckel Rate Over Expected (%)": -1.0,
                },
                {
                    "team": "SEA",
                    "game": "ARI@SEA",
                    "game_date_est": pd.Timestamp("2026-09-10"),
                    "game_time_est": "8:20",
                    "market_line": 3.5,
                    "best_line": 3.0,
                    "best_book": "FanDuel",
                    "best_cover_probability": None,
                    "model_cover_probability": 0.44,
                    "best_price": -110,
                    "edge_numeric": -0.05,
                    "Offensive Eckel Rate Over Expected (%)": -1.0,
                    "Defensive Eckel Rate Over Expected (%)": 1.5,
                },
            ]
        )
        metrics = pd.DataFrame(
            [
                {
                    "team": "ARI",
                    "off_rush_yards_pg": 130.0,
                    "off_rush_rank": 5,
                    "off_pass_yards_pg": 220.0,
                    "off_pass_rank": 11,
                    "off_epa_per_play": 0.12,
                    "off_epa_rank": 4,
                    "off_sacks_pg": 1.8,
                    "off_sacks_rank": 3,
                    "def_rush_yards_pg_allowed": 105.0,
                    "def_rush_rank": 8,
                    "def_pass_yards_pg_allowed": 210.0,
                    "def_pass_rank": 10,
                    "def_epa_allowed": -0.08,
                    "def_epa_rank": 6,
                    "def_sacks_pg": 2.6,
                    "def_sacks_rank": 4,
                },
                {
                    "team": "SEA",
                    "off_rush_yards_pg": 98.0,
                    "off_rush_rank": 20,
                    "off_pass_yards_pg": 245.0,
                    "off_pass_rank": 7,
                    "off_epa_per_play": 0.01,
                    "off_epa_rank": 15,
                    "off_sacks_pg": 2.9,
                    "off_sacks_rank": 29,
                    "def_rush_yards_pg_allowed": 126.0,
                    "def_rush_rank": 21,
                    "def_pass_yards_pg_allowed": 234.0,
                    "def_pass_rank": 24,
                    "def_epa_allowed": 0.06,
                    "def_epa_rank": 23,
                    "def_sacks_pg": 1.7,
                    "def_sacks_rank": 25,
                },
            ]
        )
        records = {"ARI": {"wins": 10, "losses": 7, "ties": 0}, "SEA": {"wins": 9, "losses": 8, "ties": 0}}
        team_names = {"ARI": "Arizona Cardinals", "SEA": "Seattle Seahawks"}
        injuries = {
            "ARI": wma.TeamInjuryReport(team="ARI", status="ok_no_injuries"),
            "SEA": wma.TeamInjuryReport(team="SEA", status="ok_no_injuries"),
        }
        article, _ = wma.build_article(
            "ARI@SEA",
            game_rows,
            metrics,
            records,
            team_names,
            None,
            wma.StatContext(season=2025, through_week=None, note="Using 2025 regular-season baselines."),
            {},
            injuries,
            edge_game_count=4,
        )

        self.assertIn("## Verdict", article)
        self.assertIn("## The Why", article)
        self.assertIn("## The Mismatch", article)
        self.assertIn("## The Number", article)
        self.assertIn("## The Risk", article)
        self.assertIn("## Arizona Cardinals offense vs Seattle Seahawks defense", article)
        self.assertIn("## Arizona Cardinals defense vs Seattle Seahawks offense", article)
        self.assertIn("## Model Prediction", article)
        self.assertIn("## Why trust this preview", article)
        self.assertNotIn("## Data Context", article)
        self.assertNotIn("## ESPN debug", article)

    def test_build_article_hides_injury_fetch_plumbing_from_readers(self) -> None:
        game_rows = pd.DataFrame(
            [
                {
                    "team": "ARI",
                    "game": "ARI@SEA",
                    "game_date_est": pd.Timestamp("2026-09-10"),
                    "game_time_est": "8:20",
                    "market_line": -3.5,
                    "best_line": -3.0,
                    "best_book": "DraftKings",
                    "best_cover_probability": 0.56,
                    "model_cover_probability": 0.56,
                    "best_price": -110,
                    "edge_numeric": 0.05,
                    "Offensive Eckel Rate Over Expected (%)": 2.0,
                    "Defensive Eckel Rate Over Expected (%)": -1.0,
                },
                {
                    "team": "SEA",
                    "game": "ARI@SEA",
                    "game_date_est": pd.Timestamp("2026-09-10"),
                    "game_time_est": "8:20",
                    "market_line": 3.5,
                    "best_line": 3.0,
                    "best_book": "FanDuel",
                    "best_cover_probability": 0.44,
                    "model_cover_probability": 0.44,
                    "best_price": -110,
                    "edge_numeric": -0.05,
                    "Offensive Eckel Rate Over Expected (%)": -1.0,
                    "Defensive Eckel Rate Over Expected (%)": 1.5,
                },
            ]
        )
        metrics = pd.DataFrame([{"team": "ARI"}, {"team": "SEA"}])
        team_names = {"ARI": "Arizona Cardinals", "SEA": "Seattle Seahawks"}
        injuries = {
            "ARI": wma.TeamInjuryReport(
                team="ARI",
                status="depth_parse_failed",
                debug=[
                    wma.EspnDebugEvent(
                        team="ARI",
                        source="depth",
                        url="https://www.espn.com/nfl/team/depth/_/name/ari",
                        failure="Depth chart page fetched, but no starters were parsed.",
                    )
                ],
            ),
            "SEA": wma.TeamInjuryReport(team="SEA", status="no_starter_match"),
        }
        article, payload = wma.build_article(
            "ARI@SEA",
            game_rows,
            metrics,
            {},
            team_names,
            None,
            wma.StatContext(season=2025, through_week=None, note="fallback note"),
            {},
            injuries,
            edge_game_count=2,
        )

        self.assertIn("## Injury report", article)
        self.assertIn("No confirmed starter injuries are currently listed for either side.", article)
        self.assertNotIn("Depth-chart data unavailable", article)
        self.assertNotIn("## ESPN debug", article)
        self.assertIn("debug", payload["injury_reports"]["ARI"])

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

    def test_build_model_prediction_uses_best_edge_over_model_edge(self) -> None:
        game_rows = pd.DataFrame([
            {
                "team": "ARI",
                "model_cover_probability": 0.5577,
                "best_cover_probability": 0.5577,
                "best_line": 11.5,
                "best_price": -110,
                "best_edge": 0.0339,
                "edge_numeric": -0.0418,
            }
        ])
        team_names = {"ARI": "Arizona Cardinals"}
        result = wma.build_model_prediction(game_rows, 1, team_names)
        self.assertIn("3.39%", result)
        self.assertNotIn("-4.18%", result)

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
