# Cleveland Browns at Jacksonville Jaguars

## Matchup info
- Teams: Cleveland Browns (0-0) at Jacksonville Jaguars (0-0)
- Kickoff: 2026-09-13 01:00 PM ET
- Location: EverBank Stadium (Jacksonville Jaguars)
- Line context from `NFL_Odds/Data/spreads_odds.csv`: JAX -7.0 / CLE +7.5
- Best book from `NFL_Odds/Data/spreads_odds.csv`: JAX -7.5 at FanDuel; CLE +7.5 at BetMGM

## Statistical matchup
- Cleveland Browns averages 81.9 rushing yards per game (28th in the league in rushing offense), 228.2 passing yards per game, and -0.175 EPA per play (32nd in the league in offensive EPA/play). Offensive Eckel ROE from the model file sits at 34.57%. They have taken 3.88 sacks per game, which ranks 31st-most sacks taken.
- Jacksonville Jaguars allows 112.6 rushing yards per game, 270.9 passing yards per game (32nd in the league in pass defense), and 0.127 EPA allowed per play (31st in the league in defensive EPA/play). Defensive Eckel ROE from the model file sits at 45.64%. Their defense is producing 2.00 sacks per game, which ranks 28th in sacks.
- Jacksonville Jaguars averages 91.1 rushing yards per game (24th in the league in rushing offense), 218.6 passing yards per game (24th in the league in passing offense), and -0.022 EPA per play. Offensive Eckel ROE from the model file sits at 52.98%.
- Cleveland Browns allows 112.8 rushing yards per game, 228.1 passing yards per game, and 0.017 EPA allowed per play. Defensive Eckel ROE from the model file sits at 43.40%.
- Special teams: D.Hopkins is hitting 66.7% of field goals (31st among kickers with attempts); Cleveland Browns has 1 kick/punt return touchdown(s), tied for 4th.
- Special teams: C.Little is hitting 93.1% of field goals (5th among kickers with attempts); Jacksonville Jaguars has 1 kick/punt return touchdown(s), tied for 4th.

## Injury report
- Cleveland Browns: No starter injuries identified from ESPN injury/depth-chart matching.
- Jacksonville Jaguars: No starter injuries identified from ESPN injury/depth-chart matching.

## ESPN debug
- CLE injuries: `https://www.espn.com/nfl/team/injuries/_/name/cle` -> Injuries page fetched, but no injury table rows were parsed.
- CLE depth: `https://www.espn.com/nfl/team/depth/_/name/cle` -> Depth chart page fetched, but no starters were parsed.
- JAX injuries: `https://www.espn.com/nfl/team/injuries/_/name/jax` -> Injuries page fetched, but no injury table rows were parsed.
- JAX depth: `https://www.espn.com/nfl/team/depth/_/name/jax` -> Depth chart page fetched, but no starters were parsed.

## Model edge blurb
The model shows edges of at least 4% in 6 of 16 games this week. This matchup does not clear that threshold, but JAX still owns the best local lean at 44.76% cover probability and a -7.62% edge.

## Notes
- Requested 2026 week 1 data was unavailable. Matchup stats fall back to the 2024 regular season.
- Offensive and defensive Eckel values are sourced from the weekly model CSV because no local shared nflverse dictionary field currently names an Eckel metric.
