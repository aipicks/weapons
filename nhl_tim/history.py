"""Full-season historical layer. Skater game logs fetched once and cached; goal_games from them."""
from concurrent.futures import ThreadPoolExecutor
from .config import PREV_SEASON, GOALIE_MIN_GP
from .db import connect
from .normalize import norm_name
from .sources import nhl_api

def _ranks(items, key, best_is_low):
    """items: list of (id, value). rank 1 = best."""
    order = sorted(items, key=lambda x: x[1], reverse=not best_is_low)
    return {i: n + 1 for n, (i, _) in enumerate(order)}

def _goal_games(nhl_id, season, force):
    log = nhl_api.game_log(nhl_id, season, force)
    return sum(1 for g in log if g["goals"] > 0), len(log)

def update_history(season=PREV_SEASON, force=False):
    con = connect()
    abbr = nhl_api.team_abbrevs(force=force)

    # teams
    trows = nhl_api.teams(season, force)
    ranks = _ranks([(r["teamFullName"], r["goalsAgainstPerGame"]) for r in trows], "ga", True)
    for r in trows:
        n = r["teamFullName"]
        con.execute("INSERT OR REPLACE INTO team_season VALUES(?,?,?,?,?)",
                    (abbr[n], season, r["gamesPlayed"], r["goalsAgainstPerGame"], ranks[n]))

    # goalies: rank among qualified only
    grows = nhl_api.goalies(season, force)
    # merge traded goalies (multiple rows possible) not expected in summary; keep as-is
    q = [r for r in grows if r["gamesPlayed"] >= GOALIE_MIN_GP]
    gaa_r = _ranks([(r["playerId"], r["goalsAgainstAverage"]) for r in q], "gaa", True)
    sv_r = _ranks([(r["playerId"], r["savePct"]) for r in q], "sv", False)
    for r in grows:
        pid = r["playerId"]
        con.execute("INSERT OR REPLACE INTO goalie_season VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, season, r["goalieFullName"], r["teamAbbrevs"].split(",")[-1],
                     r["gamesPlayed"], r["gamesStarted"], r["goalsAgainstAverage"], r["savePct"],
                     int(pid in gaa_r), gaa_r.get(pid), sv_r.get(pid)))

    # skaters
    srows = nhl_api.skaters(season, force)
    with ThreadPoolExecutor(3) as ex:
        gg = list(ex.map(lambda r: _goal_games(r["playerId"], season, force), srows))
    for r, (ggames, nlog) in zip(srows, gg):
        pid = r["playerId"]
        team = r["teamAbbrevs"].split(",")[-1]
        con.execute("INSERT OR REPLACE INTO player VALUES(?,?,?,?,?)",
                    (pid, r["skaterFullName"], norm_name(r["skaterFullName"]),
                     "D" if r["positionCode"] == "D" else "F", team))
        con.execute("INSERT OR REPLACE INTO player_season VALUES(?,?,?,?,?,?,?,?)",
                    (pid, season, team, r["gamesPlayed"], r["goals"], r["shots"],
                     ggames, ggames / r["gamesPlayed"] if r["gamesPlayed"] else 0.0))
    con.execute("INSERT OR REPLACE INTO meta VALUES('history_updated', datetime('now'))")
    con.commit()
    return con

def validate_history(con, season=PREV_SEASON):
    errs = []
    teams = con.execute("SELECT * FROM team_season WHERE season=?", (season,)).fetchall()
    if len(teams) != 32: errs.append(f"expected 32 teams, got {len(teams)}")
    if sorted(t["ga_rank"] for t in teams) != list(range(1, 33)): errs.append("team ranks not 1..32")
    for t in teams:
        if t["gp"] != 82: errs.append(f"{t['team']} gp={t['gp']} (partial season?)")
    for p in con.execute("SELECT * FROM player_season WHERE season=?", (season,)):
        if p["goal_games"] > p["gp"] or p["goal_games"] > p["g"]: errs.append(f"bad goal_games {dict(p)}")
    dup = con.execute("SELECT name_norm, COUNT(*) c FROM player GROUP BY name_norm HAVING c>1").fetchall()
    for d in dup: errs.append(f"duplicate normalized name: {d['name_norm']}")
    return errs


def refresh_players(season, force=False):
    """Add current-season skaters (rookies, new signings) to player table; current team wins."""
    con = connect()
    for r in nhl_api.skaters(season, force):
        con.execute("INSERT OR REPLACE INTO player VALUES(?,?,?,?,?)",
                    (r["playerId"], r["skaterFullName"], norm_name(r["skaterFullName"]),
                     "D" if r["positionCode"] == "D" else "F", r["teamAbbrevs"].split(",")[-1]))
    con.commit()
    return con
