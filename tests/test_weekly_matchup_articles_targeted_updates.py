import unittest

import pandas as pd

from scripts.weekly_matchup_articles import (
    UnitBattle,
    build_article,
    build_bottom_line,
    build_cta,
    model_ranks,
    model_vs_market_lead,
    render_logo_row,
    render_risk,
    StatContext,
    TeamInjuryReport,
)


class WeeklyMatchupArticlesTargetedUpdatesTest(unittest.TestCase):
    def test_render_logo_row_uses_borderless_table(self):
        row = render_logo_row(
            away_name="Arizona Cardinals",
            away_logo="https://a.espncdn.com/i/teamlogos/nfl/500/ari.png",
            home_name="Los Angeles Chargers",
            home_logo="https://a.espncdn.com/i/teamlogos/nfl/500/lac.png",
        )

        self.assertIsNotNone(row)
        self.assertIn('<table align="center" border="0" style="border-collapse:collapse;border:none;">', row)
        self.assertIn('style="font-size:69px;border:none;"', row)
        self.assertIn('style="border:none;"', row)

    def test_build_article_no_bet_still_shows_why_the_pick_and_table(self):
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
                    "best_cover_probability": 0.52,
                    "model_cover_probability": 0.52,
                    "best_price": -110,
                    "edge_numeric": 0.02,
                },
                {
                    "team": "SEA",
                    "game": "ARI@SEA",
                    "game_date_est": pd.Timestamp("2026-09-10"),
                    "game_time_est": "8:20",
                    "market_line": 3.5,
                    "best_line": 3.0,
                    "best_book": "FanDuel",
                    "best_cover_probability": 0.48,
                    "model_cover_probability": 0.48,
                    "best_price": -110,
                    "edge_numeric": -0.02,
                },
            ]
        )
        metrics = pd.DataFrame([{"team": "ARI"}, {"team": "SEA"}])
        model_frame = pd.DataFrame(
            [
                {
                    "Team": "ARI",
                    "Offensive Expected Points (Season)": 0.20,
                    "Defensive Expected Points (Season)": -0.05,
                    "Offensive Success Rate (%)": 51.0,
                    "Defensive Success Rate (%)": 47.0,
                    "QB Expected Points Added (Last 10 games)": 0.12,
                    "Offensive Eckel Rate Over Expected (%)": 54.1,
                    "Defensive Eckel Rate Over Expected (%)": 45.8,
                    "Qbname": "K.Murray",
                },
                {
                    "Team": "SEA",
                    "Offensive Expected Points (Season)": 0.05,
                    "Defensive Expected Points (Season)": 0.04,
                    "Offensive Success Rate (%)": 48.0,
                    "Defensive Success Rate (%)": 44.0,
                    "QB Expected Points Added (Last 10 games)": 0.02,
                    "Offensive Eckel Rate Over Expected (%)": 48.4,
                    "Defensive Eckel Rate Over Expected (%)": 50.2,
                    "Qbname": "G.Smith",
                },
            ]
        )
        team_names = {"ARI": "Arizona Cardinals", "SEA": "Seattle Seahawks"}
        injuries = {
            "ARI": TeamInjuryReport(team="ARI", status="ok_no_injuries"),
            "SEA": TeamInjuryReport(team="SEA", status="ok_no_injuries"),
        }

        article, _ = build_article(
            "ARI@SEA",
            game_rows,
            metrics,
            {},
            team_names,
            None,
            StatContext(season=2026, through_week=1, note="test"),
            {},
            injuries,
            edge_game_count=2,
            model_ranks_df=model_ranks(model_frame),
        )

        self.assertIn("## Why The Pick", article)
        self.assertIn("| | Arizona Cardinals | Seattle Seahawks |", article)
        self.assertIn(
            "The model sees a lean here — but the edge does not clear our 4% threshold, so there is no play.",
            article,
        )
        self.assertNotIn("## What the Model Sees", article)

    def test_build_bottom_line_uses_edge_based_hammer_and_the_bet_name(self):
        lines = build_bottom_line(
            away_name="Away Team",
            home_name="Home Team",
            stadium_name="Test Stadium",
            bet_name="Home Team",
            bet_line="-3.5",
            confidence="Lean",
            bet_facts={"cover": 0.56, "edge": 0.05, "price": -110},
            seed="away@home",
            has_bet=True,
            model_lead="Model likes Home Team -3.5 over market.",
        )

        self.assertIn(
            "The Away Team take on the Home Team at Test Stadium and model likes Home Team -3.5 over market.",
            lines[1],
        )
        self.assertIn(
            "This puts the edge at 5.00%, which at -3.5 for -110 makes the Home Team a bet.",
            lines[2],
        )

    def test_build_bottom_line_no_bet_prose_uses_the_bet_name(self):
        lines = build_bottom_line(
            away_name="Away Team",
            home_name="Home Team",
            stadium_name="Test Stadium",
            bet_name="Away Team",
            bet_line="+2.5",
            confidence="Pass",
            bet_facts={"cover": 0.50, "edge": 0.009, "price": -110},
            seed="away@home",
            has_bet=False,
            model_lead=None,
        )

        self.assertIn("The Away Team take on the Home Team at Test Stadium and", lines[1])
        self.assertIn("so we are passing on this one.", lines[1])
        # Rule 1: edge percentage must be explicitly stated
        self.assertIn("with an edge of 0.90%", lines[1])
        # Rule 2: no-bet team must not be mentioned as "closest look"
        self.assertNotIn("closest look", lines[1])
        # Only two elements: header and text (no extra "closest look" sentence)
        self.assertEqual(2, len(lines))

    def test_build_cta_footer_removes_links(self):
        lines = build_cta(edge_game_count=5, has_bet=True)
        self.assertEqual(
            "_Built by the BTB model. We target a 55-57% win rate and publish every result, wins and losses._",
            lines[-1],
        )

    def test_model_vs_market_lead_uses_the_and_hyphen(self):
        line = model_vs_market_lead("Los Angeles Chargers", -6.5, -4.0, "seed")
        self.assertIsNotNone(line)
        self.assertIn("**the Los Angeles Chargers**", line)
        self.assertNotIn("—", line)

    def test_render_risk_removes_em_dash(self):
        line = render_risk(
            ("outclassed", UnitBattle("overall", "A", "B", 20, 12, 0.0)),
            opp_name="Seattle Seahawks",
            total_teams=32,
            seed="risk-seed",
        )
        self.assertNotIn("—", line)


if __name__ == "__main__":
    unittest.main()
