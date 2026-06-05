# Chicago Bears at Carolina Panthers

## Matchup info
- Teams: Chicago Bears (0-0) at Carolina Panthers (0-0)
- Kickoff: 2026-09-13 01:00 PM ET
- Location: Bank of America Stadium (Carolina Panthers)
- Line context from `NFL_Odds/Data/spreads_odds.csv`: CHI -2.5 / CAR +2.5
- Best book from `NFL_Odds/Data/spreads_odds.csv`: CHI -2.5 at DraftKings; CAR +2.5 at FanDuel

## Statistical matchup
- Chicago Bears averages 79.8 rushing yards per game (31st in the league in rushing offense), 208.9 passing yards per game (28th in the league in passing offense), and -0.075 EPA per play (26th in the league in offensive EPA/play). Offensive Eckel ROE from the model file sits at 57.33%. They have taken 4.00 sacks per game, which ranks 32nd-most sacks taken.
- Carolina Panthers allows 164.9 rushing yards per game (32nd in the league in rush defense), 237.8 passing yards per game, and 0.154 EPA allowed per play (32nd in the league in defensive EPA/play). Defensive Eckel ROE from the model file sits at 53.54%. Their defense is producing 1.88 sacks per game, which ranks 29th in sacks.
- Carolina Panthers averages 93.8 rushing yards per game, 200.6 passing yards per game (31st in the league in passing offense), and -0.041 EPA per play (23rd in the league in offensive EPA/play). Offensive Eckel ROE from the model file sits at 48.87%.
- Chicago Bears allows 126.8 rushing yards per game (30th in the league in rush defense), 232.9 passing yards per game, and -0.001 EPA allowed per play. Defensive Eckel ROE from the model file sits at 50.00%.
- Special teams: C.Santos is tied for 1st in blocked field goals; Chicago Bears has 2 kick/punt return touchdown(s), tied for 2nd.

## Injury report
- Chicago Bears: No starter injuries identified from ESPN injury/depth-chart matching.
- Carolina Panthers: No starter injuries identified from ESPN injury/depth-chart matching.

## ESPN debug
- CHI injuries: `https://www.espn.com/nfl/team/injuries/_/name/chi` -> Injuries page fetched, but no injury table rows were parsed.
- CHI depth: `https://www.espn.com/nfl/team/depth/_/name/chi` -> Depth chart page fetched, but no starters were parsed.
- CAR injuries: `https://www.espn.com/nfl/team/injuries/_/name/car` -> Injuries page fetched, but no injury table rows were parsed.
- CAR depth: `https://www.espn.com/nfl/team/depth/_/name/car` -> Depth chart page fetched, but no starters were parsed.

## Model edge blurb
The model shows edges of at least 4% in 6 of 16 games this week. This matchup does not clear that threshold, but CAR still owns the best local lean at 51.85% cover probability and a -0.53% edge.

## Notes
- Requested 2026 week 1 data was unavailable. Matchup stats fall back to the 2024 regular season.
- Offensive and defensive Eckel values are sourced from the weekly model CSV because no local shared nflverse dictionary field currently names an Eckel metric.
