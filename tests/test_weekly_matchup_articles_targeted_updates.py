import unittest

from scripts.weekly_matchup_articles import build_bottom_line, build_cta


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

        self.assertIn("That puts **the Home Team -3.5** on the card", lines[2])
        self.assertIn("— a bet.", lines[2])

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

        self.assertIn("The closest look is the Away Team +2.5", lines[1])

    def test_build_cta_footer_removes_links(self):
        lines = build_cta(edge_game_count=5, has_bet=True)
        self.assertEqual(
            "_Built by the BTB model. We target a 55–57% win rate and publish every result, wins and losses._",
            lines[-1],
        )


if __name__ == "__main__":
    unittest.main()
