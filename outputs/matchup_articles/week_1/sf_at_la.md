# San Francisco 49ers at Los Angeles Rams

## Matchup info
- Teams: San Francisco 49ers (0-0) at Los Angeles Rams (0-0)
- Kickoff: 2026-09-10 08:35 PM ET
- Location: Melbourne Cricket Ground (Los Angeles Rams)
- Line context from `NFL_Odds/Data/spreads_odds.csv`: LA -2.5 / SF +2.5
- Best book from `NFL_Odds/Data/spreads_odds.csv`: LA -2.5 at FanDuel; SF +2.5 at DraftKings

## Statistical matchup
- San Francisco 49ers averages 108.8 rushing yards per game, 260.2 passing yards per game (4th in the league in passing offense), and 0.061 EPA per play. Offensive Eckel ROE from the model file sits at 56.52%.
- Los Angeles Rams allows 114.0 rushing yards per game, 241.2 passing yards per game (23rd in the league in pass defense), and 0.050 EPA allowed per play (23rd in the league in defensive EPA/play). Defensive Eckel ROE from the model file sits at 45.07%.
- Los Angeles Rams averages 102.4 rushing yards per game, 240.9 passing yards per game (10th in the league in passing offense), and 0.034 EPA per play. Offensive Eckel ROE from the model file sits at 60.71%.
- San Francisco 49ers allows 115.1 rushing yards per game (23rd in the league in rush defense), 204.5 passing yards per game (3rd in the league in pass defense), and 0.052 EPA allowed per play (26th in the league in defensive EPA/play). Defensive Eckel ROE from the model file sits at 51.09%.
- Special teams: J.Moody is hitting 70.6% of field goals (30th among kickers with attempts); San Francisco 49ers has 1 kick/punt return touchdown(s), tied for 4th.
- Special teams: Los Angeles Rams has 1 kick/punt return touchdown(s), tied for 4th.

## Injury report
- San Francisco 49ers: No starter injuries identified from ESPN injury/depth-chart matching.
- Los Angeles Rams: No starter injuries identified from ESPN injury/depth-chart matching.

## ESPN debug
- SF injuries: `https://www.espn.com/nfl/team/injuries/_/name/sf` -> Injuries page fetched, but no injury table rows were parsed.
- SF depth: `https://www.espn.com/nfl/team/depth/_/name/sf` -> Depth chart page fetched, but no starters were parsed.
- LA injuries: `https://www.espn.com/nfl/team/injuries/_/name/la` -> Injuries page fetched, but no injury table rows were parsed.
- LA depth: `https://www.espn.com/nfl/team/depth/_/name/la` -> Depth chart page fetched, but no starters were parsed.

## Model edge blurb
The model shows edges of at least 4% in 6 of 16 games this week. This matchup does not clear that threshold, but SF still owns the best local lean at 53.46% cover probability and a 1.08% edge.

## Notes
- Requested 2026 week 1 data was unavailable. Matchup stats fall back to the 2024 regular season.
- Offensive and defensive Eckel values are sourced from the weekly model CSV because no local shared nflverse dictionary field currently names an Eckel metric.
