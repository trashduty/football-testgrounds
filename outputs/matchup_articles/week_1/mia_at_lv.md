# Miami Dolphins at Las Vegas Raiders

## Matchup info
- Teams: Miami Dolphins (0-0) at Las Vegas Raiders (0-0)
- Kickoff: 2026-09-13 04:25 PM ET
- Location: Allegiant Stadium (Las Vegas Raiders)
- Line context from `NFL_Odds/Data/spreads_odds.csv`: LV -3.0 / MIA +3.0
- Best book from `NFL_Odds/Data/spreads_odds.csv`: LV -3.5 at BetMGM; MIA +3.5 at BetMGM

## Statistical matchup
- Miami Dolphins averages 95.4 rushing yards per game, 237.4 passing yards per game, and -0.040 EPA per play. Offensive Eckel ROE from the model file sits at 49.28%.
- Las Vegas Raiders allows 104.9 rushing yards per game, 234.6 passing yards per game, and 0.014 EPA allowed per play. Defensive Eckel ROE from the model file sits at 56.52%.
- Las Vegas Raiders averages 73.7 rushing yards per game (32nd in the league in rushing offense), 242.2 passing yards per game (9th in the league in passing offense), and -0.131 EPA per play (31st in the league in offensive EPA/play). Offensive Eckel ROE from the model file sits at 34.72%.
- Miami Dolphins allows 95.9 rushing yards per game, 225.2 passing yards per game (9th in the league in pass defense), and -0.021 EPA allowed per play (8th in the league in defensive EPA/play). Defensive Eckel ROE from the model file sits at 52.21%.

## Injury report
- Miami Dolphins: No starter injuries identified from ESPN injury/depth-chart matching.
- Las Vegas Raiders: No starter injuries identified from ESPN injury/depth-chart matching.

## ESPN debug
- MIA injuries: `https://www.espn.com/nfl/team/injuries/_/name/mia` -> Injuries page fetched, but no injury table rows were parsed.
- MIA depth: `https://www.espn.com/nfl/team/depth/_/name/mia` -> Depth chart page fetched, but no starters were parsed.
- LV injuries: `https://www.espn.com/nfl/team/injuries/_/name/lv` -> Injuries page fetched, but no injury table rows were parsed.
- LV depth: `https://www.espn.com/nfl/team/depth/_/name/lv` -> Depth chart page fetched, but no starters were parsed.

## Model edge blurb
The model shows edges of at least 4% in 6 of 16 games this week. This matchup does not clear that threshold, but MIA still owns the best local lean at 53.46% cover probability and a 1.08% edge.

## Notes
- Requested 2026 week 1 data was unavailable. Matchup stats fall back to the 2024 regular season.
- Offensive and defensive Eckel values are sourced from the weekly model CSV because no local shared nflverse dictionary field currently names an Eckel metric.
