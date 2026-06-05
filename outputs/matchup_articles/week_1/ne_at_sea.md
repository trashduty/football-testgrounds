# New England Patriots at Seattle Seahawks

## Matchup info
- Teams: New England Patriots (0-0) at Seattle Seahawks (0-0)
- Kickoff: 2026-09-09 08:15 PM ET
- Location: Lumen Field (Seattle Seahawks)
- Line context from `NFL_Odds/Data/spreads_odds.csv`: SEA -4.5 / NE +4.5
- Best book from `NFL_Odds/Data/spreads_odds.csv`: SEA -3.5 at DraftKings; NE +4.5 at BetMGM

## Statistical matchup
- New England Patriots averages 87.7 rushing yards per game (25th in the league in rushing offense), 196.6 passing yards per game (32nd in the league in passing offense), and -0.079 EPA per play (27th in the league in offensive EPA/play). Offensive Eckel ROE from the model file sits at 56.30%.
- Seattle Seahawks allows 110.0 rushing yards per game, 231.2 passing yards per game, and -0.018 EPA allowed per play (10th in the league in defensive EPA/play). Defensive Eckel ROE from the model file sits at 42.76%.
- Seattle Seahawks averages 81.4 rushing yards per game (29th in the league in rushing offense), 257.6 passing yards per game (5th in the league in passing offense), and -0.016 EPA per play. Offensive Eckel ROE from the model file sits at 51.68%. They have taken 3.18 sacks per game, which ranks 29th-most sacks taken.
- New England Patriots allows 121.4 rushing yards per game (29th in the league in rush defense), 220.5 passing yards per game (6th in the league in pass defense), and 0.084 EPA allowed per play (30th in the league in defensive EPA/play). Defensive Eckel ROE from the model file sits at 44.20%. Their defense is producing 1.65 sacks per game, which ranks 32nd in sacks.
- Special teams: J.Slye has already connected from 60-plus yards.
- Special teams: Seattle Seahawks has 1 kick/punt return touchdown(s), tied for 4th.

## Injury report
- New England Patriots: No starter injuries identified from ESPN injury/depth-chart matching.
- Seattle Seahawks: No starter injuries identified from ESPN injury/depth-chart matching.

## ESPN debug
- NE injuries: `https://www.espn.com/nfl/team/injuries/_/name/ne` -> Injuries page fetched, but no injury table rows were parsed.
- NE depth: `https://www.espn.com/nfl/team/depth/_/name/ne` -> Depth chart page fetched, but no starters were parsed.
- SEA injuries: `https://www.espn.com/nfl/team/injuries/_/name/sea` -> Injuries page fetched, but no injury table rows were parsed.
- SEA depth: `https://www.espn.com/nfl/team/depth/_/name/sea` -> Depth chart page fetched, but no starters were parsed.

## Model edge blurb
The model shows edges of at least 4% in 6 of 16 games this week. For this matchup it leans toward NE with a 59.25% cover probability and a 6.87% edge.

## Notes
- Requested 2026 week 1 data was unavailable. Matchup stats fall back to the 2024 regular season.
- Offensive and defensive Eckel values are sourced from the weekly model CSV because no local shared nflverse dictionary field currently names an Eckel metric.
