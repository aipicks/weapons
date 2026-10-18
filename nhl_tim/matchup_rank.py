"""One matchup score (1 to 4, 4 = best) per team playing today, for the Tim Hortons challenge.

The score does not depend on the other games on the slate. Each of three pieces is turned into a league-wide percentile
(0 = the worst possible spot for the team's skaters, 1 = the best) by comparing it with every team or goalie in the NHL:
  1. the opposing starting goalie: goals allowed vs expected, shrunk for small samples (weaker = better)
  2. the team's own offence: expected goals for per game this season blended with last season (more = better)
  3. the opposing team's defence: current defence ranking, last season's goals against and the preseason projected
     ranking, averaged (weaker = better)
score = 1 + 3 x (weighted average of the three percentiles), weights in config.MATCHUP."""
from .config import CUR_SEASON, MATCHUP, PREV_SEASON, PRESEASON_D
from .games import _goalie_factor, build_game_rows
from .posshots import current_defense


def _pct(value, population):
    """Share of the population at or below value (0..1); 0.5 when there is nothing to compare."""
    if not population:
        return 0.5
    return sum(1 for v in population if v <= value) / len(population)


def team_matchups(con, date, eids=None):
    cd = current_defense(con)
    prev = {r["team"]: r["ga_rank"] for r in con.execute("SELECT team, ga_rank FROM team_season WHERE season=?", (PREV_SEASON,))}

    def defence(team):
        """Weighted average defence rank (1 = best defence, 32 = worst) from whatever components exist."""
        parts = []
        if team in cd:
            parts.append((MATCHUP["def_cur"], cd[team]["rank"]))
        if prev.get(team):
            parts.append((MATCHUP["def_prev"], prev[team]))
        if PRESEASON_D.get(team):
            parts.append((MATCHUP["def_pre"], PRESEASON_D[team]))
        w = sum(p[0] for p in parts)
        return (sum(p[0] * p[1] for p in parts) / w) if w else 16.5

    # offence: expected goals for per game, last season blended with this season by games played
    mp = {}
    for r in con.execute("SELECT team, season, gp, xgf FROM team_mp WHERE sit='all' AND gp>0"):
        mp.setdefault(r["team"], {})[r["season"]] = r["xgf"] / r["gp"], r["gp"]

    def offence(team):
        d = mp.get(team, {})
        p, c = d.get(PREV_SEASON), d.get(CUR_SEASON)
        if p and c:
            w = c[1] / (c[1] + 20)
            return (1 - w) * p[0] + w * c[0]
        return (p or c or (None,))[0]

    off_all = [v for v in (offence(t) for t in mp) if v is not None]
    def_all = [defence(t) for t in mp] or [16.5]
    goalie_all = []
    for r in con.execute("SELECT DISTINCT nhl_id FROM goalie_xg"):
        f = _goalie_factor(con, r["nhl_id"], "Confirmed", None)
        goalie_all.append(f)

    out = []
    for g in build_game_rows(con, date):
        if eids is not None and g["eid"] not in eids:
            continue
        for team, opp, og in ((g["home"], g["away"], g["goalie_away"]), (g["away"], g["home"], g["goalie_home"])):
            f = _goalie_factor(con, og["goalie_id"], "Confirmed", None) if og and og["goalie_id"] else 1.0
            known = bool(og and og["goalie_id"])
            off = offence(team)
            dscore = defence(opp)
            p_goalie = _pct(f, goalie_all) if known else 0.5
            p_off = _pct(off, off_all) if off is not None else 0.5
            p_def = min(max((dscore - 1) / 31, 0.0), 1.0)
            avg = MATCHUP["goalie"] * p_goalie + MATCHUP["xg"] * p_off + MATCHUP["defense"] * p_def
            out.append({"team": team, "opp": opp, "home": team == g["home"], "goalie": og["goalie_name"] if og else None,
                        "status": og["status"] if og else None, "gf": f, "p_goalie": p_goalie, "off": off, "p_off": p_off,
                        "def_score": dscore, "p_def": p_def, "score": 1 + 3 * avg})
    out.sort(key=lambda e: -e["score"])
    return out
