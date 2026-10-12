"""Shots against by the position of the shooter (forwards vs defensemen), plus a current defence ranking.

Shots against by position come from the official boxscores: a team's shots allowed to forwards (defensemen) in a game
are the shots on goal by the other team's forwards (defensemen). Finished boxscores never change, so they are cached
once; the first run for a whole season fetches ~1,300 boxscores, later runs only fetch new games."""
from .config import CUR_SEASON, PREV_SEASON
from .sources import nhl_api

DEF_WEIGHTS = {"xga": 0.40, "ga": 0.35, "sa": 0.25}  # current defence: expected goals against matter most, shots least


def _ranks(vals):
    """{team: rank}, 1 = lowest value (best defence)."""
    order = sorted(vals, key=lambda t: vals[t])
    return {t: i + 1 for i, t in enumerate(order)}


def _games(abbrevs, season):
    """{game id: (away, home)} for finished regular-season games."""
    out = {}
    for t in abbrevs:
        j = nhl_api.get(f"{nhl_api.WEB}/club-schedule-season/{t}/{season}", f"cs_{t}_{season}", force=season == CUR_SEASON)
        for g in (j or {}).get("games", []):
            if g.get("gameType") == 2 and g.get("gameState") in ("OFF", "FINAL"):
                out[g["id"]] = (g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"])
    return out


def _split(gid):
    """{'homeTeam': (forward shots, defense shots), 'awayTeam': (...)} for one finished game."""
    b = nhl_api.get(f"{nhl_api.WEB}/gamecenter/{gid}/boxscore", f"box/{gid}", force=False)
    out = {}
    for side in ("homeTeam", "awayTeam"):
        grp = (b or {}).get("playerByGameStats", {}).get(side, {})
        out[side] = tuple(sum(p.get("sog", 0) or 0 for p in grp.get(k, []) or []) for k in ("forwards", "defense"))
    return out


def update_pos_shots(con):
    """Per team and season: shots allowed per game to forwards and to defensemen, with ranks (1 = fewest)."""
    abbrevs = sorted(set(nhl_api.team_abbrevs(force=False).values()))
    for season in (PREV_SEASON, CUR_SEASON):
        tot = {}
        for gid, (away, home) in _games(abbrevs, season).items():
            try:
                s = _split(gid)
            except Exception:
                continue
            for team, opp_side in ((home, "awayTeam"), (away, "homeTeam")):
                t = tot.setdefault(team, [0, 0, 0])
                t[0] += 1
                t[1] += s[opp_side][0]
                t[2] += s[opp_side][1]
        if not tot:
            continue
        fr = _ranks({t: v[1] / v[0] for t, v in tot.items()})
        dr = _ranks({t: v[2] / v[0] for t, v in tot.items()})
        for t, (gp, f, d) in tot.items():
            con.execute("INSERT OR REPLACE INTO team_pos_sa VALUES(?,?,?,?,?,?,?)",
                        (t, season, gp, f / gp, d / gp, fr[t], dr[t]))
    con.commit()


def current_defense(con, season=CUR_SEASON):
    """{team: {rank, gp, xga, ga, sa}} ranking this season's defence (1 = best) from per-game expected goals against,
    goals against and shots against (all situations), weighted by DEF_WEIGHTS on each metric's rank."""
    rows = con.execute("SELECT team, gp, xga, ga, sa FROM team_mp WHERE season=? AND sit='all' AND gp>0", (season,)).fetchall()
    if not rows:
        return {}
    per = {r["team"]: {"gp": r["gp"], "xga": r["xga"] / r["gp"], "ga": r["ga"] / r["gp"], "sa": r["sa"] / r["gp"]} for r in rows}
    rk = {m: _ranks({t: v[m] for t, v in per.items()}) for m in DEF_WEIGHTS}
    score = {t: sum(w * rk[m][t] for m, w in DEF_WEIGHTS.items()) for t in per}
    final = _ranks(score)
    return {t: dict(per[t], rank=final[t]) for t in per}
