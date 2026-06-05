# Baltimore Ravens at Indianapolis Colts

## Matchup info
- Teams: Baltimore Ravens (0-0) at Indianapolis Colts (0-0)
- Kickoff: 2026-09-13 01:00 PM ET
- Location: Lucas Oil Stadium (Indianapolis Colts)
- Line context from `NFL_Odds/Data/spreads_odds.csv`: BAL -3.5 / IND +3.5
- Best book from `NFL_Odds/Data/spreads_odds.csv`: BAL -3.5 at DraftKings; IND +3.5 at FanDuel

## Statistical matchup
- Baltimore Ravens averages 164.3 rushing yards per game (1st in the league in rushing offense), 246.4 passing yards per game (8th in the league in passing offense), and 0.197 EPA per play (1st in the league in offensive EPA/play). Offensive Eckel ROE from the model file sits at 50.69%. They have taken 1.41 sacks per game, which ranks 3rd-fewest sacks taken.
- Indianapolis Colts allows 118.9 rushing yards per game (26th in the league in rush defense), 243.6 passing yards per game (25th in the league in pass defense), and 0.006 EPA allowed per play. Defensive Eckel ROE from the model file sits at 51.77%.
- Indianapolis Colts averages 124.7 rushing yards per game (7th in the league in rushing offense), 211.7 passing yards per game (27th in the league in passing offense), and -0.047 EPA per play (24th in the league in offensive EPA/play). Offensive Eckel ROE from the model file sits at 55.80%.
- Baltimore Ravens allows 70.0 rushing yards per game (1st in the league in rush defense), 262.8 passing yards per game (31st in the league in pass defense), and -0.018 EPA allowed per play. Defensive Eckel ROE from the model file sits at 53.79%. Their defense is producing 3.18 sacks per game, which ranks 2nd in sacks.
- Special teams: J.Tucker is hitting 73.3% of field goals (28th among kickers with attempts).

## Injury report
- Baltimore Ravens: No starter injuries identified from ESPN injury/depth-chart matching.
- Indianapolis Colts: No starter injuries identified from ESPN injury/depth-chart matching.

## ESPN debug
- BAL injuries: `https://www.espn.com/nfl/team/injuries/_/name/bal` -> Injuries page fetched, but no injury table rows were parsed.
- BAL depth: `https://www.espn.com/nfl/team/depth/_/name/bal` -> Depth chart page fetched, but no starters were parsed.
- IND injuries: `https://www.espn.com/nfl/team/injuries/_/name/ind` -> Injuries page fetched, but no injury table rows were parsed.
- IND depth: `https://www.espn.com/nfl/team/depth/_/name/ind` -> Depth chart page fetched, but no starters were parsed.

## Model edge blurb
The model shows edges of at least 4% in 6 of 16 games this week. This matchup does not clear that threshold, but BAL still owns the best local lean at 52.85% cover probability and a 0.47% edge.

## Notes
- Requested 2026 week 1 data was unavailable. Matchup stats fall back to the 2024 regular season.
- Offensive and defensive Eckel values are sourced from the weekly model CSV because no local shared nflverse dictionary field currently names an Eckel metric.
