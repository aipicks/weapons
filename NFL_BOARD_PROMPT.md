# Prompt: build the NFL pick board (new folder `nfl-board`)

Paste everything below the line into a new Claude Code chat opened in a new empty folder (e.g. `C:\Users\edenc\OneDrive\Desktop\nfl-board`).

---

Build a **weekly NFL betting board** modeled on my NHL board: a Python project with SQLite, modular data adapters, and a one-file dark-theme mobile HTML dashboard that opens locally as `board.html`. **Local only for now: do not create a GitHub repo, add a remote, or push anything.** Plain local `git init` and commits are fine for history. GitHub Pages comes later, when I say the board is ready. On every market the job is the same: calculate **our own probability**, compare it with the **de-vigged market probability**, and show the **EV** of the best side. Build correct, validated data first; do not jump to a fancy model.

## Rules that carry over from the NHL project
1. **Books:** only FanDuel, DraftKings, BetMGM, ESPN BET and Bovada (`ALLOWED_BOOKS` in config). Drop every other book at ingest. Show FanDuel as the reference line; use the best price across the five for EV.
2. **Odds source:** SportsGameOdds (`SGO_API_KEY` in `.env`, never committed; `.env` gitignored). Slate-level requests only, never one per player; cache the raw response. My plan is the free "amateur" tier (about 2,500 events a month, 10 requests a minute): check `GET /v2/account/usage` and stay well inside it. Props.Cash is paid and login-only: do not scrape it.
3. **Probability:** logit blend of the model and the de-vigged market, **50/50** to start (one config value per market). De-vig with the Yes/No or Over/Under pair when both exist.
4. **EV** = p x payout - (1 - p); a push refunds the stake. Label **BET** at +3% EV or more, **LEAN** at +1% to +3%, **PASS** below. Always show the **best value pick of the slate** even when nothing clears +3% (clearly labelled as below the bar).
5. **Tracking from day one:** log every prediction before kickoff to a `pred_log` table, settle from final box scores, and build a **Results** tab: hit rate vs predicted, Brier score model vs market, calibration buckets, flat-$1 profit. Judge the model after hundreds of picks, not days. Remind me to back up the database.
6. **Engineering:** one adapter per data source so a site change breaks one file; cache everything; use retries and fall back to cached data on network errors; validate names, teams, week, season and that historical stats are full-season (or clearly labelled partial); unit tests for the math and a test that guards every step the weekly update calls; all weights are config priors (not "fitted"). At the end tell me exactly which formula was used and where it lives.
7. **Dashboard:** dark theme, mobile first, tabs. Green = good for the bettor/shooter, yellow = middle or neutral, red = bad (never gray for the middle). Everything visible without tapping, compact. Filters and a sort menu on every player tab (team, game, position, key stats). A "How the model works" section on the page. Put `[hidden]{display:none!important}` in the CSS (otherwise hidden tabs still show on the published page) and test in a phone-size browser before publishing. Build it so the same page can be published with GitHub Pages later (output also written to `docs/index.html`), but do not publish it now.

## Data sources (use these; verify access and each site's terms first; never log in, never bypass a paywall)
- **Lines and props:** SportsGameOdds, markets for spread, moneyline, total and the player props below, with opening lines for line movement.
- **Stats:** nflverse / nflfastR (prefer the `nflreadpy` package; `nfl_data_py` is the older fallback; or read the nflverse release files directly; install what you need into this project's environment): play-by-play with EPA and success rate, weekly player stats, snap counts, target and carry shares, red-zone and goal-line usage, schedules, rosters, depth charts. Last season (full, labelled) and this season to date.
- **Weather:** https://www.nflweather.com/ (server-rendered; per-game forecast with temperature, wind, conditions). Domes and closed roofs ignore weather.
- **Injuries:** https://www.rotowire.com/football/injury-report.php (server-rendered table: player, team, position, injury, status). Cross-check with the official practice reports and nflverse injuries if available.
- **Public betting splits:** https://www.sportsbettingdime.com/nfl/public-betting-trends/ (tickets % vs money % per side for spread, moneyline, total).
- Scout other free sources if they add real information (ESPN API, Pro Football Reference, Open-Meteo as a weather backup) and tell me what you used and why.

## Markets (tabs)
1. **Game Bets:** spread, moneyline, total for every game. Project points per team from offensive and defensive EPA per play (pass and rush split), success rate, pace (plays per game), QB (EPA per dropback), injuries, home field, rest (bye, short week, Thursday night), travel and weather. Win and cover probabilities from the **real historical distribution of NFL margins** (margins cluster on 3 and 7, so a plain normal curve misprices spreads at those numbers), centered on our projected margin; the total likewise from the historical distribution of totals around the projection. Tune the spreads from last season's actual errors. Show best side and best book for each market, plus the best value pick.
2. **+EV TD SCORERS:** every player with anytime-TD odds; **only players the model gives 15%+** (config value); **top 50 by EV** (config value); renumber after filtering. Features: TDs per game last and this season, **games-with-a-TD rate** (not only total TDs), red-zone targets and carries, goal-line carries, snap share, target share, team implied points from the spread and total, opponent TDs allowed to the position, injuries to teammates.
3. **Player Props:** from the sportsbook menu: Pass Yds, Rush Yds, Rec Yds, Receptions, Pass TD, Pass Completions, Pass Att, Longest Pass, Interceptions, P+R Yds, Rush Att, Longest Rush, R+R Yds, Longest Reception (build the ones SportsGameOdds actually offers; tell me which are missing). Projection = expected volume x efficiency, adjusted for opponent defense vs position, game script (spread and total), weather, and injuries to teammates; use a distribution that fits each stat (negative binomial or normal); output P(over), P(under), best side and EV. Show the opposing defense prominently (rank and yards allowed per game to that position, last and this season).
4. **Results** (tracking) as described above.
No survivor or pick'em pool features. No bankroll or stake-sizing features: show EV and BET/LEAN/PASS only.
5. **Parlay Ideas** (built late, after the single-bet tabs work):
   - **Alternate lines are allowed, but never as single bets**: they appear only as parlay legs, and never show or use any leg priced shorter than **-200** (nothing like -250); every leg must pay -200 or better. Singles (spread, moneyline, total, TD, props) keep the normal BET/LEAN/PASS rules and are never alternate lines.
   - Cross-game parlays: price = product of the legs' decimal odds at the best of the five books; probability = product of our final leg probabilities; show EV and which legs carry the value. Prefer legs that are individually at least break-even.
   - Same-game parlays are only ideas with a clear "correlated, price it at the book" note (books price SGP with a haircut and our odds feed does not return SGP prices). Estimate correlation from a game simulation or historical data and label it as an estimate.
   - Track parlay results separately in the Results tab.

## Injuries
- Out/IR = 0%; **Doubtful about 10%, Questionable about 60% to play** (config); then redistribute the missing player's targets, carries and red-zone work to the players behind him on the depth chart and update every affected projection.
- Show a status tag on each player (Healthy / Questionable / Doubtful / Out) and a "status may change before kickoff" note, because Sunday inactives post about 90 minutes before kickoff and I only run Thursday and Saturday.
- Provide an optional quick re-run command for Sunday morning.

## Public money and line movement
- Show opening line vs current line and the key numbers (3 and 7) crossed.
- Show tickets % vs money % per side. **Money % well above tickets % on a side = sharp-looking; heavy tickets with light money = public.** Display it on every game card.
- Give both signals a **small, capped weight** in the model (config prior, for example at most +/-0.75 points on a spread projection and +/-0.5 on a total), and let the Results tab show whether it helps before we raise it. Do not "fade the public" by default.

## Weather and rest (small, config adjustments)
Wind of 15+ mph, rain or snow lowers passing and totals; extreme cold lowers totals a little; domes ignore it. Bye week, short week and travel adjust the margin by small amounts. Everything is a prior to be checked by tracking.

## Weekly schedule: two runs
- **Thursday:** refresh lines, injuries, ratings and weather; build and publish the board for the whole week (the Thursday game is checked against its inactives).
- **Saturday:** refresh injuries, weather and lines again; rebuild and publish the final board; snapshot predictions before kickoff.
- After games: settle results (Monday or Tuesday) from box scores.
- Show a "Last updated" timestamp and the data ages on the page. Launchers: `Update Thursday.bat`, `Update Saturday.bat`, `Open Board.bat`, `Publish Board.bat`, plus a README with the commands.

## Scope notes
- It is currently NFL Week 5 of the 2026 season. First build: last season in full plus this season's Weeks 1-4; start tracking from the first week we run it. Regular season only.
- Rookies and players with no NFL history: use a role-based starting estimate (draft position, depth chart, snaps) and label the row "limited data".
- Public-betting splits and line movement: display them and give them a small capped weight only (cautious); no "fade the public" rule.

## Build order (stop and show me at each step)
1. Architecture, schema and each data source (confirm access to every site above).
2. Data layer: team and player history; validate against known box scores.
3. Odds ingest (five books), de-vig, and line movement.
4. Injuries, weather and public-betting adapters.
5. Game model and the Game Bets tab.
6. Anytime-TD model and tab.
7. Player props model and tab.
8. Tracking, settlement and the Results tab; then the Parlay Ideas tab.
9. Tests, README and the formula write-up.

Work in small steps and stop after each one for my review. Start with step 1. Ask me only what you cannot decide yourself.
