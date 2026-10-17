"""One matchup ranking per team playing today, for the Tim Hortons challenge.

Combines three things, each ranked across the teams on the slate (1 = best for that team's skaters):
  1. the opposing starting goalie (weaker = better): goals allowed vs expected, shrunk, damped by confirmation status
  2. the team's own expected goals (before any goalie adjustment): from the game model
  3. the opposing team's defence (worse = better): current defence ranking, last season's goals against and the
     preseason projected ranking
The total is the weighted average of the three ranks (config.MATCHUP), then ranked 1..N."""
from .config import MATCHUP, PREV_SEASON, PRESEASON_D
from .games import _goalie_factor, build_game_rows
from .posshots import current_defense


def _rank_desc(vals):
    """{key: rank}, 1 = highest value."""
    order = sorted(vals, key=lambda k: -vals[k])
    return {k: i + 1 for i, k in enumerate(order)}


def team_matchups(con, date, eids=None):
    cd = current_defense(con)
    prev = {r["team"]: r["ga_rank"] for r in con.execute("SELECT team, ga_rank FROM team_season WHERE season=?", (PREV_SEASON,))}

    def defence(team):
        """Weighted average defence rank of a team (1 = best defence) from whatever components exist."""
        parts = []
        if team in cd:
            parts.append((MATCHUP["def_cur"], cd[team]["rank"]))
        if prev.get(team):
            parts.append((MATCHUP["def_prev"], prev[team]))
        if PRESEASON_D.get(team):
            parts.append((MATCHUP["def_pre"], PRESEASON_D[team]))
        w = sum(p[0] for p in parts)
        return (sum(p[0] * p[1] for p in parts) / w) if w else 16.5

    ent = {}
    for g in build_game_rows(con, date):
        if eids is not None and g["eid"] not in eids:
            continue
        for team, opp, calc, og in ((g["home"], g["away"], g["h"], g["goalie_away"]), (g["away"], g["home"], g["a"], g["goalie_home"])):
            f = _goalie_factor(con, og["goalie_id"], og["status"], None) if og else 1.0
            ent[team] = {"team": team, "opp": opp, "home": team == g["home"], "xg": calc["xg"], "gf": f,
                         "goalie": og["goalie_name"] if og else None, "status": og["status"] if og else None,
                         "def_score": defence(opp)}
    if not ent:
        return []
    rg = _rank_desc({t: e["gf"] for t, e in ent.items()})
    rx = _rank_desc({t: e["xg"] for t, e in ent.items()})
    rd = _rank_desc({t: e["def_score"] for t, e in ent.items()})
    for t, e in ent.items():
        e.update(r_goalie=rg[t], r_xg=rx[t], r_def=rd[t])
        e["score"] = MATCHUP["goalie"] * rg[t] + MATCHUP["xg"] * rx[t] + MATCHUP["defense"] * rd[t]
    order = sorted(ent.values(), key=lambda e: (e["score"], e["r_xg"]))
    for i, e in enumerate(order):
        e["rank"], e["n"] = i + 1, len(order)
    return order
