"""Daily refresh: current-season skater/goalie stats, games+odds (SGO), projected goalies (RotoWire)."""
from .config import CUR_SEASON
from .db import connect
from .normalize import norm_name, norm_team
from .odds_math import american_to_prob
from .sources import moneypuck, nhl_api, rotowire, sgo
from .history import _ranks


def update_current_stats(con, force=True):
    for r in nhl_api.skaters(CUR_SEASON, force):
        pid = r["playerId"]
        team = r["teamAbbrevs"].split(",")[-1]
        con.execute("INSERT OR REPLACE INTO player VALUES(?,?,?,?,?)",
                    (pid, r["skaterFullName"], norm_name(r["skaterFullName"]),
                     "D" if r["positionCode"] == "D" else "F", team))
        con.execute("INSERT OR REPLACE INTO player_season VALUES(?,?,?,?,?,?,?,?)",
                    (pid, CUR_SEASON, team, r["gamesPlayed"], r["goals"], r["shots"], None, None))
    g = nhl_api.goalies(CUR_SEASON, force)
    q = [r for r in g if r["gamesPlayed"] >= 1]
    gaa_r = _ranks([(r["playerId"], r["goalsAgainstAverage"]) for r in q], "gaa", True)
    sv_r = _ranks([(r["playerId"], r["savePct"]) for r in q], "sv", False)
    for r in g:
        pid = r["playerId"]
        con.execute("INSERT OR REPLACE INTO goalie_season VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, CUR_SEASON, r["goalieFullName"], r["teamAbbrevs"].split(",")[-1], r["gamesPlayed"],
                     r["gamesStarted"], r["goalsAgainstAverage"], r["savePct"], int(pid in gaa_r),
                     gaa_r.get(pid), sv_r.get(pid)))


def _find_player(con, team, name):
    n = norm_name(name)
    for sql, a in (("SELECT nhl_id FROM player WHERE name_norm=? AND team=?", (n, team)),
                   ("SELECT nhl_id FROM player WHERE name_norm=?", (n,)),
                   ("SELECT nhl_id FROM player WHERE team=? AND name_norm LIKE ?", (team, "% " + n.split()[-1]))):
        rows = con.execute(sql, a).fetchall()
        if len(rows) == 1:
            return rows[0]["nhl_id"]
    return None


def _find_goalie(con, team, name):
    """Match by normalized name (team-scoped first, then league-wide), then last name within team."""
    n = norm_name(name)
    on_team = list(con.execute("SELECT nhl_id, name FROM goalie_season WHERE team=?", (team,)))
    league = list(con.execute("SELECT nhl_id, name FROM goalie_season"))
    for pool, exact in ((on_team, True), (league, True), (on_team, False)):
        if exact:
            ids = {r["nhl_id"] for r in pool if norm_name(r["name"]) == n}
        else:
            ids = {r["nhl_id"] for r in pool if norm_name(r["name"]).split()[-1] == n.split()[-1]}
        if len(ids) == 1:
            return ids.pop()
    return None


def ingest_slate(con, date, force=True):
    events = sgo.fetch_slate(date, force)
    con.execute("DELETE FROM game WHERE date=?", (date,))
    stats = {"games": len(events), "atg": 0, "atg_unmatched": []}
    for e in events:
        eid = e["eventID"]
        h = norm_team(e["teams"]["home"]["names"]["short"])
        a = norm_team(e["teams"]["away"]["names"]["short"])
        con.execute("INSERT OR REPLACE INTO game VALUES(?,?,?,?,?)", (date, eid, e["status"]["startsAt"], a, h))
        o = e.get("odds", {})
        ov, un = o.get("points-all-game-ou-over"), o.get("points-all-game-ou-under")
        mh, ma = o.get("points-home-game-ml-home"), o.get("points-away-game-ml-away")
        books = set()
        for x in (ov, un, mh, ma):
            if x:
                books |= set(x.get("byBookmaker", {}))
        for b in books:
            def g(x, k="odds"):
                return (x or {}).get("byBookmaker", {}).get(b, {}).get(k)
            if g(ov) is None or g(ov, "overUnder") is None:
                continue
            con.execute("INSERT OR REPLACE INTO odds_game VALUES(?,?,?,?,?,?,?,?)",
                        (eid, b, g(ov, "lastUpdatedAt"), float(g(ov, "overUnder")), float(g(ov)),
                         float(g(un)) if g(un) else None, float(g(mh)) if g(mh) else None,
                         float(g(ma)) if g(ma) else None))
        con.execute("DELETE FROM odds_atg WHERE event_id=?", (eid,))
        tid = {e["teams"]["home"]["teamID"]: (h, a), e["teams"]["away"]["teamID"]: (a, h)}
        for oid, x in o.items():
            if not (oid.startswith("points-") and oid.endswith("-game-yn-yes")):
                continue
            pinfo = e.get("players", {}).get(x.get("playerID"))
            if not pinfo or pinfo.get("teamID") not in tid:
                continue
            team, opp = tid[pinfo["teamID"]]
            pid = _find_player(con, team, pinfo["name"])
            if pid is None:
                stats["atg_unmatched"].append((team, pinfo["name"]))
            for b, bo in x.get("byBookmaker", {}).items():
                if bo.get("odds") is None or not bo.get("available", True):
                    continue
                am = float(bo["odds"])
                no_bo = o.get(oid.replace("-yes", "-no"), {}).get("byBookmaker", {}).get(b, {})
                no_am = float(no_bo["odds"]) if no_bo.get("odds") is not None else None
                con.execute("INSERT OR REPLACE INTO odds_atg VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (eid, pid, pinfo["name"], team, opp, b, am, american_to_prob(am), bo.get("lastUpdatedAt"), no_am))
                stats["atg"] += 1
    return stats


def ingest_goalies(con, date):
    games = con.execute("SELECT * FROM game WHERE date=?", (date,)).fetchall()
    by_pair = {frozenset((g["home"], g["away"])): g["event_id"] for g in games}
    n = 0
    unmatched = []
    for r in rotowire.fetch(date):
        r["team"], r["opp"] = norm_team(r["team"]), norm_team(r["opp"])
        eid = by_pair.get(frozenset((r["team"], r["opp"])))
        if not eid:
            continue
        gid = _find_goalie(con, r["team"], r["goalie"]) if r["goalie"] else None
        if r["goalie"] and gid is None:
            unmatched.append((r["team"], r["goalie"]))
        con.execute("INSERT OR REPLACE INTO goalie_proj VALUES(?,?,?,?,?,?,?)",
                    (eid, r["team"], r["opp"], r["goalie"], gid, r["status"], 0))
        n += 1
    return n, unmatched


def update_team_stats(con, season=CUR_SEASON):
    """Current-season team GA/G + rank (small sample early; ranked among teams that have played)."""
    abbr = nhl_api.team_abbrevs(force=False)
    rows = [r for r in nhl_api.teams(season, True) if r["gamesPlayed"] > 0]
    ranks = _ranks([(r["teamFullName"], r["goalsAgainstPerGame"]) for r in rows], "ga", True)
    for r in rows:
        n = r["teamFullName"]
        if n in abbr:
            con.execute("INSERT OR REPLACE INTO team_season VALUES(?,?,?,?,?)",
                        (abbr[n], season, r["gamesPlayed"], r["goalsAgainstPerGame"], ranks[n]))


def update_toi(con):
    """Season-average time on ice per game (seconds): previous season cached, current refreshed daily."""
    from .config import PREV_SEASON
    for season, force in ((PREV_SEASON, False), (CUR_SEASON, True)):
        for r in nhl_api.skaters(season, force):
            if r.get("timeOnIcePerGame") is not None:
                con.execute("INSERT OR REPLACE INTO player_toi VALUES(?,?,?)",
                            (r["playerId"], season, r["timeOnIcePerGame"]))


def update_pp_time(con):
    """Player PP time on ice per game, and each team's time shorthanded (penalty kill) per game, in seconds.
    Previous season cached once; current season refreshed daily."""
    from .config import PREV_SEASON
    abbr = nhl_api.team_abbrevs(force=False)
    for season, force in ((PREV_SEASON, False), (CUR_SEASON, True)):
        for r in nhl_api.skater_powerplay(season, force):
            if r.get("ppTimeOnIcePerGame") is not None:
                con.execute("INSERT OR REPLACE INTO player_pp_toi VALUES(?,?,?)",
                            (r["playerId"], season, r["ppTimeOnIcePerGame"]))
        for r in nhl_api.team_penaltykill(season, force):
            n = r["teamFullName"]
            if n in abbr and r.get("pkTimeOnIcePerGame") is not None and r["gamesPlayed"] > 0:
                con.execute("INSERT OR REPLACE INTO team_pk_toi VALUES(?,?,?)", (abbr[n], season, r["pkTimeOnIcePerGame"]))


def update_xg(con):
    """Individual expected goals: previous season cached once, current season refreshed daily."""
    for year, season, force in ((2025, 20252026, False), (2026, 20262027, True)):
        for pid, d in moneypuck.fetch(year, force).items():
            con.execute("INSERT OR REPLACE INTO player_xg VALUES(?,?,?,?,?)", (pid, season, d["gp"], d["xg"], d["goals"]))


def update_daily(date):
    con = connect()
    update_current_stats(con)
    update_xg(con)
    update_toi(con)
    update_pp_time(con)
    update_team_stats(con)
    s = ingest_slate(con, date)
    n, um = ingest_goalies(con, date)
    con.execute("INSERT OR REPLACE INTO meta VALUES('daily_updated', datetime('now'))")
    con.commit()
    return s, n, um
