"""Shots matchup score per team playing today, one for forwards and one for defensemen.

For each team's skaters, the opponent is judged on what it allows (this season blended with last season by games
played), ranked among the opponents on the slate so 1 = the best spot for shooters:
  attempts allowed per game, shots on goal allowed per game, shots allowed to that position group (forwards or
  defensemen) per game, share of attempts blocked (fewer is better), share of attempts that reach the net (more is
  better). The score is the weighted average of those ranks (config.SHOTMATCH), then ranked 1..N."""
from .config import CUR_SEASON, PREV_SEASON, SHOTMATCH, SOG


def _rank(vals, desc):
    order = sorted(vals, key=lambda k: -vals[k] if desc else vals[k])
    return {k: i + 1 for i, k in enumerate(order)}


def _blend(prev, cur, key):
    if prev is None:
        return None
    if cur is None:
        return prev[key]
    w = cur["gp"] / (cur["gp"] + SOG["opp_k"])
    return (1 - w) * prev[key] + w * cur[key]


def shot_matchups(con, date, eids=None):
    """{team: {"F": {...}, "D": {...}}} where the team is the one whose skaters shoot."""
    games = [g for g in con.execute("SELECT event_id, home, away FROM game WHERE date=?", (date,)) if eids is None or g["event_id"] in eids]
    opp_of = {}
    for g in games:
        opp_of[g["home"]], opp_of[g["away"]] = g["away"], g["home"]
    att_p = {r["team"]: r for r in con.execute("SELECT * FROM team_att WHERE season=? AND gp>0", (PREV_SEASON,))}
    att_c = {r["team"]: r for r in con.execute("SELECT * FROM team_att WHERE season=? AND gp>0", (CUR_SEASON,))}
    pos_p = {r["team"]: r for r in con.execute("SELECT * FROM team_pos_sa WHERE season=?", (PREV_SEASON,))}
    pos_c = {r["team"]: r for r in con.execute("SELECT * FROM team_pos_sa WHERE season=?", (CUR_SEASON,))}

    def stats(o):
        ap, ac, pp, pc = att_p.get(o), att_c.get(o), pos_p.get(o), pos_c.get(o)
        if not (ap and pp):
            return None
        per = lambda r, k: r[k] / r["gp"]
        att = _blend({"v": per(ap, "att_a"), "gp": ap["gp"]}, {"v": per(ac, "att_a"), "gp": ac["gp"]} if ac else None, "v")
        sog = _blend({"v": per(ap, "sog_a"), "gp": ap["gp"]}, {"v": per(ac, "sog_a"), "gp": ac["gp"]} if ac else None, "v")
        blk = _blend({"v": ap["blk_a"] / ap["att_a"], "gp": ap["gp"]}, {"v": ac["blk_a"] / ac["att_a"], "gp": ac["gp"]} if ac else None, "v")
        ot = _blend({"v": ap["sog_a"] / ap["att_a"], "gp": ap["gp"]}, {"v": ac["sog_a"] / ac["att_a"], "gp": ac["gp"]} if ac else None, "v")
        sf = _blend(pp, pc, "sa_f_pg")
        sd = _blend(pp, pc, "sa_d_pg")
        return {"att": att, "sog": sog, "blk": blk, "ot": ot, "F": sf, "D": sd}

    shooters = {t: stats(o) for t, o in opp_of.items()}
    shooters = {t: s for t, s in shooters.items() if s}
    out = {}
    for grp in ("F", "D"):
        ranks = {
            "att": _rank({t: s["att"] for t, s in shooters.items()}, True),
            "sog": _rank({t: s["sog"] for t, s in shooters.items()}, True),
            "pos": _rank({t: s[grp] for t, s in shooters.items()}, True),
            "blk": _rank({t: s["blk"] for t, s in shooters.items()}, False),
            "ot": _rank({t: s["ot"] for t, s in shooters.items()}, True)}
        score = {t: sum(SHOTMATCH[k] * ranks[k][t] for k in ranks) for t in shooters}
        final = _rank(score, False)
        for t in shooters:
            out.setdefault(t, {})[grp] = {
                "rank": final[t], "n": len(shooters), "score": score[t], "opp": opp_of[t],
                "r_att": ranks["att"][t], "r_sog": ranks["sog"][t], "r_pos": ranks["pos"][t],
                "r_blk": ranks["blk"][t], "r_ot": ranks["ot"][t]}
    return out
