import unittest

from scripts.weekly_matchup_articles import (
    UnitBattle,
    build_bottom_line,
    build_cta,
    model_vs_market_lead,
    qb_xfactor,
    render_risk,
)


class WeeklyMatchupArticlesTargetedUpdatesTest(unittest.TestCase):
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
        self.assertIn("The closest look is the Away Team +2.5", lines[1])

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

    def test_qb_xfactor_removes_em_dash(self):
        lines = qb_xfactor(
            bet_name="Los Angeles Chargers",
            bet_m={"qbname": "J.Herbert", "qb10_rank": 3},
            opp_name="Seattle Seahawks",
            opp_m={"qbname": "G.Smith", "qb10_rank": 30},
            total_teams=32,
            seed="qb-seed",
        )
        self.assertTrue(lines)
        self.assertTrue(all("—" not in line for line in lines))


if __name__ == "__main__":
    unittest.main()
