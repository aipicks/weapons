# nhl_tim — Tim Hortons Hockey Challenge goal-probability tool

Daily table of P(player scores >= 1 goal) per pool. Data pipeline first; model is a transparent baseline (priors in `nhl_tim/config.py::W`, not fitted).

## Setup
```
pip install -r requirements.txt
echo SGO_API_KEY=... > .env
```

## Commands (`python -m nhl_tim.cli <cmd> [--date YYYY-MM-DD]`)
| cmd | when | what |
|---|---|---|
| `update_history` | once / on demand (`--force` refetch) | 2025-26 full-season skater/goalie/team stats + games-with-a-goal (NHL API, cached in data/raw) |
| `update_power_play_units` | manual, every few weeks | Daily Faceoff PP1/PP2 for all 32 teams -> local DB, `last_pp_update`, history kept |
| `update_daily` | every day | current-season stats (NHL API), games/odds/anytime-goal (SportsGameOdds, 1 slate call), projected goalies (Daily Faceoff only) |
| `build_table` | every day | writes `data/table_<date>.html` + `.csv` |
| `build_site` | every day, after `update_daily` | writes `data/site_<date>.html`, a mobile dashboard (single file, data embedded) |
| `validate` | any time | checks full-season history |

## Pools
Copy `data/pools.csv.example` to `data/pools.csv` (`pool,player,team`), pool = 1/2/3. Without it, the table lists every player with anytime-goal odds as one section.

## Notes
- Anytime goal in SGO = `points-<PLAYER>-game-yn-yes`; FanDuel shown, all books stored for consensus/best price.
- Implied team totals: total shifted by over/under lean, split by de-vigged moneyline (`odds_math.py`).
- Individual expected goals (xG) come from MoneyPuck (free CSV, joined by NHL id); last season cached once, current season refreshed daily.
- Player matching: normalized name + team (+ position for same-name players).
- Goalie colours: ranks among qualified (>=20 GP) goalies in thirds; current-season ranks include any goalie with GP>=1 (small sample, shrunk in the model).

## Tests
```
pip install pytest
python -m pytest tests
```

## Daily routine
```
python -m nhl_tim.cli update_daily
python -m nhl_tim.cli build_site     # or build_table for HTML/CSV table
```
Update `data/pools.csv` first (local only, gitignored).

## Daily publish
1. Update `data/pools.csv` with today's three pools.
2. Run `Publish Board.bat` (or `update_daily`, `build_site`, then commit and push `docs/`).
3. Friends view the board at the repo's GitHub Pages URL (Settings -> Pages -> Deploy from branch `main`, folder `/docs`).

The published page contains sportsbook odds; check your odds provider's terms before sharing it widely.
Estimates only, not betting advice.
