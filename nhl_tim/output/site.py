"""Mobile-friendly single-file dashboard (data embedded as JSON). Publish data/site_<date>.html as an Artifact."""
import json
from pathlib import Path

from ..config import DATA, ROOT, CUR_SEASON, PREV_SEASON, PRESEASON_D, PRESEASON_D_TIES
from ..sources import nhl_api
from ..db import connect
from ..model import build_rows
from ..sog import build_sog_rows
from ..games import build_game_rows, best_value_pick
from ..matchup_rank import team_matchups
from ..shotmatch import shot_matchups
from ..parlays import build_parlays

_TM = {}  # team -> matchup entry for the slate being built (set in build_site)
from ..tracking import results as _results
from ..odds_math import fmt_american
from .table import _et, team_color, third_color

TEMPLATE = (Path(__file__).parent / "site_template.html").read_text(encoding="utf-8")
TOP_N_ALL = 30  # All Players tab shows the top N players by EV
MIN_P_ALL = 0.15  # ...and only players whose model probability is at least this
STATUS = {"Confirmed": "Confirmed", "Likely": "Likely", "Unconfirmed": "Unconfirmed"}


def _goalie_season(con, s):
    if not s or not s["gp"]:
        return None
    n_g = con.execute("SELECT COUNT(gaa_rank) FROM goalie_season WHERE season=?", (s["season"],)).fetchone()[0]
    n_s = con.execute("SELECT COUNT(sv_rank) FROM goalie_season WHERE season=?", (s["season"],)).fetchone()[0]
    return {"gp": s["gp"], "starts": s["starts"], "gaa": round(s["gaa"], 2), "gr": s["gaa_rank"],
            "gc": third_color(s["gaa_rank"], n_g), "sv": round(s["sv_pct"], 3), "svr": s["sv_rank"],
            "svc": third_color(s["sv_rank"], n_s), "qual": bool(s["qualified"])}


def _mmss(sec):
    return None if sec is None else f"{int(sec // 60)}:{int(sec % 60):02d}"


def _cur_goal_games(nhl_id):
    try:
        log = nhl_api.game_log(nhl_id, CUR_SEASON, force=True)
        return sum(1 for g in log if g["goals"] > 0)
    except Exception:
        return None


_GG = {}


def _gg(pid):
    if pid not in _GG:
        _GG[pid] = _cur_goal_games(pid)
    return _GG[pid]


def _late_eids(con, date):
    """Event ids of the games that start at or after MIN_START_HOUR_ET (Eastern)."""
    from ..config import MIN_START_HOUR_ET
    out = set()
    for g in con.execute("SELECT event_id, start_utc FROM game WHERE date=?", (date,)):
        if _et(g["start_utc"])[1].hour >= MIN_START_HOUR_ET:
            out.add(g["event_id"])
    return out


def _renumber(rows, by_pool):
    """Re-rank rows 1..N (within each pool when by_pool) after hidden games were removed, keeping their order."""
    groups = {}
    for r in rows:
        groups.setdefault(r.get("pool") if by_pool else None, []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: r["rank"])
        for i, r in enumerate(g):
            r["rank"], r["pool_n"] = i + 1, len(g)


def _team_goals(con):
    """{(team, season): {gpg, xgpg, rank, n, gp}} goals and expected goals scored per game, 1 = most goals."""
    out = {}
    for season in (PREV_SEASON, CUR_SEASON):
        rows = con.execute("SELECT team, gp, gf, xgf FROM team_mp WHERE season=? AND sit='all' AND gp>0 ORDER BY gf*1.0/gp DESC", (season,)).fetchall()
        for i, r in enumerate(rows):
            out[(r["team"], season)] = {"gpg": round(r["gf"] / r["gp"], 2), "xgpg": round(r["xgf"] / r["gp"], 2), "rank": i + 1,
                                        "n": len(rows), "gp": r["gp"], "c": team_color(len(rows) + 1 - (i + 1), len(rows))}
    return out


def _pk_ranks(con):
    """{team: (rank, n)} for this season's shorthanded time per game, 1 = the most."""
    rows = con.execute("SELECT team FROM team_pk_toi WHERE season=? ORDER BY pk_toi_pg DESC", (CUR_SEASON,)).fetchall()
    return {r["team"]: (i + 1, len(rows)) for i, r in enumerate(rows)}


def _ppg(con, nhl_id, season, goals):
    """Power-play goals for one season and the share of the player's goals that came on the power play (None if unknown)."""
    r = con.execute("SELECT pp_goals FROM player_ppg WHERE nhl_id=? AND season=?", (nhl_id, season)).fetchone()
    if r is None or goals is None:
        return None
    return {"pp": r["pp_goals"], "g": goals, "pct": round(r["pp_goals"] / goals * 100) if goals else None}


def _pkr(ranks, team):
    if team not in ranks:
        return None
    r, n = ranks[team]
    return {"r": r, "n": n, "c": team_color(n + 1 - r, n)}  # most shorthanded time is the best spot for a PP player


def _rows(con, rows):
    pkr = _pk_ranks(con)
    tgoals = _team_goals(con)
    out = []
    for r in rows:
        tm, _ = _et(r["start"])
        prev, cur, ot, gp, go = r["prev"], r["cur"], r["opp_team"], r["goalie_proj"], r["game_odds"]
        n, rk = r["pool_n"], r["rank"]
        oc = con.execute("SELECT * FROM team_season WHERE team=? AND season=?", (r["opp"], CUR_SEASON)).fetchone()
        n_teams = con.execute("SELECT COUNT(*) FROM team_season WHERE season=?", (CUR_SEASON,)).fetchone()[0]
        tier = "g" if rk <= n / 3 else "r" if rk > 2 * n / 3 else "m"
        game = None
        if go:
            game = {"total": go["total"], "over": fmt_american(go["over_price"]), "fav": None, "ml": None}
            if go["ml_home"] is not None and go["ml_away"] is not None:
                home_fav = go["ml_home"] < go["ml_away"]
                game["fav"] = r["home"] if home_fav else r["away"]
                game["ml"] = fmt_american(go["ml_home"] if home_fav else go["ml_away"])
        a = r["atg_display"]
        ev = edge = None
        if a:  # value vs the displayed (FanDuel) price: EV per $1 staked and probability edge in points
            am = a["american"]
            dec = 1 + (am / 100 if am > 0 else 100 / -am)
            ev = round((r["p"] * dec - 1) * 100, 1)
            edge = round((r["p"] - a["implied"]) * 100, 1)
        # numeric sort keys for the board's sort menu
        lr = con.execute("SELECT grp FROM player_line WHERE nhl_id=?", (r["nhl_id"],)).fetchone()
        line = lr["grp"] if lr else None
        gp25, gp26 = (prev["gp"] if prev else 0), (cur["gp"] if cur else 0)
        gg25 = prev["goal_games"] if prev else 0
        gg26 = _gg(r["nhl_id"]) if cur and cur["gp"] else 0

        def ratio(a_, b_):
            return round(a_ / b_ * 100, 1) if b_ else None

        def wavg(x, wx, y, wy):
            if x is None and y is None:
                return None
            if x is None or not wx:
                return y
            if y is None or not wy:
                return x
            return (x * wx + y * wy) / (wx + wy)
        pk_p, pk_c = r["opp_pk_toi"][PREV_SEASON], r["opp_pk_toi"][CUR_SEASON]
        oc_gp = oc["gp"] if oc else 0
        sk = {"ggp": ratio(gg25, gp25), "ggc": ratio(gg26 or 0, gp26) if gp26 else None,
              "ggb": ratio(gg25 + (gg26 or 0), gp25 + gp26),
              "toip": r["toi_prev"], "toic": r["toi_cur"], "toib": wavg(r["toi_prev"], gp25, r["toi_cur"], gp26),
              "pkp": pk_p, "pkc": pk_c, "pkb": wavg(pk_p, 82, pk_c, oc_gp),
              "line": {"F1": 1, "F2": 2, "F3": 3, "F4": 4, "D1": 5, "D2": 6, "D3": 7}.get(line),
              "tm": round(_TM[r["team"]]["score"], 2) if r["team"] in _TM else None,
              "odds": a["implied"] if a else None}
        goalie = None
        if gp and gp["goalie_name"]:
            goalie = {"name": gp["goalie_name"], "status": STATUS.get(gp["status"], "Unknown"),
                      "p": _goalie_season(con, r["goalie"]["prev"] if r["goalie"] else None),
                      "c": _goalie_season(con, r["goalie"]["cur"] if r["goalie"] else None)}
        out.append({
            "pool": r["pool"], "rank": rk, "n": n, "tier": tier, "start": r["start"],
            "game_name": f"{r['away']} @ {r['home']}", "time": tm, "eid": r["event_id"],
            "name": r["name"], "team": r["team"], "opp": r["opp"], "pos": r["pos"], "chg": r["team_changed"],
            "p": round(r["p"] * 100, 1), "pm": round(r["p_mkt"] * 100, 1) if r["p_mkt"] else None,
            "pp": r["pp_unit"], "ev": ev, "edge": edge, "line": line, "sk": sk,
            "tm": {"score": round(_TM[r["team"]]["score"], 1)} if r["team"] in _TM else None,
            "any": {"am": fmt_american(a["american"]), "pr": round(a["implied"] * 100, 1)} if a else None,
            "game": game, "xg": round(r["xg"], 2) if r["xg"] is not None else None,
            "s25": {"toi": _mmss(r["toi_prev"]), "gp": prev["gp"], "g": prev["g"], "sog": prev["sog"], "gg": prev["goal_games"],
                    "xg": round(r["xg_prev"]["xg"], 1) if r["xg_prev"] else None,
                    "ggp": round(prev["goal_game_pct"] * 100, 1)} if prev else None,
            "s26": {"toi": _mmss(r["toi_cur"]), "gp": cur["gp"], "g": cur["g"], "sog": cur["sog"], "xg": round(r["xg_cur"]["xg"], 2) if r["xg_cur"] else None, "gg": _gg(r["nhl_id"]) if cur["gp"] else 0}
            if cur else {"toi": None, "gp": 0, "g": 0, "sog": 0, "gg": 0, "xg": None},
            "opp_d": {"ga": round(ot["ga_pg"], 2), "rank": ot["ga_rank"], "c": team_color(ot["ga_rank"])} if ot else None,
            "opp_pre": {"rank": PRESEASON_D[r["opp"]], "tie": r["opp"] in PRESEASON_D_TIES,
                        "c": team_color(PRESEASON_D[r["opp"]])},
            "opp_c": {"ga": round(oc["ga_pg"], 2), "rank": oc["ga_rank"], "gp": oc["gp"], "n": n_teams,
                      "c": team_color(oc["ga_rank"], n_teams)} if oc else None,
            "tg": {"p": tgoals.get((r["team"], PREV_SEASON)), "c": tgoals.get((r["team"], CUR_SEASON))},
            "ppg": {"p": _ppg(con, r["nhl_id"], PREV_SEASON, prev["g"] if prev else None), "c": _ppg(con, r["nhl_id"], CUR_SEASON, cur["g"] if cur else 0)},
            "ppt": {"p": _mmss(r["pp_toi"][PREV_SEASON]), "c": _mmss(r["pp_toi"][CUR_SEASON]),
                    "opk_p": _mmss(r["opp_pk_toi"][PREV_SEASON]), "opk_c": _mmss(r["opp_pk_toi"][CUR_SEASON]),
                    "opk_r": _pkr(pkr, r["opp"])},
            "goalie": goalie, "why": [[p, t] for p, t in r["reasons_signed"]],
        })
    return out


def _sog_payload(con, date, eids=None):
    pkr = _pk_ranks(con)
    smx = shot_matchups(con, date, eids)
    out = []
    sog_rows = [r for r in build_sog_rows(con, date) if eids is None or r["event_id"] in eids]
    for i_, r_ in enumerate(sog_rows):  # keep the numbering 1..N now that hidden games are gone
        r_["rank"], r_["n"] = i_ + 1, len(sog_rows)
    for r in sog_rows:
        tm, _ = _et(r["start"])
        n_teams = con.execute("SELECT COUNT(*) FROM team_season WHERE season=?", (CUR_SEASON,)).fetchone()[0]
        op, oc = r["opp_prev"], r["opp_cur"]
        def oppatt(t, rk, opp_):
            if not t:
                return None
            return {"pg": round(t["att_a"] / t["gp"], 1), "blk": round(t["blk_a"] / t["att_a"] * 100, 1),
                    "ot": round(t["sog_a"] / t["att_a"] * 100, 1), "gp": t["gp"],
                    "r_att": rk["att"][opp_], "r_blk": rk["blk"][opp_], "r_ot": rk["ot"][opp_],
                    "c_att": team_color(rk["att"][opp_]), "c_blk": team_color(rk["blk"][opp_]), "c_ot": team_color(rk["ot"][opp_])}
        def attinfo(a):
            if not a:
                return None
            def one(x):
                if not x or not x["gp"]:
                    return None
                return {"gp": x["gp"], "pg": round(x["att"] / x["gp"], 1), "sog": round(x["sog"] / x["gp"], 1),
                        "ot": round(x["sog"] / x["att"] * 100) if x["att"] else None,
                        "blk": round(x["blocked"] / x["att"] * 100) if x["att"] else None,
                        "miss": round(x["missed"] / x["att"] * 100) if x["att"] else None}
            return {"p": one(a["prev"]), "c": one(a["cur"]), "mu": round(a["mu"], 2), "ot": round(a["ot"] * 100), "rate": round(a["rate"], 1)}
        def _pk(con_, team, season):
            row = con_.execute("SELECT pk_toi_pg FROM team_pk_toi WHERE team=? AND season=?", (team, season)).fetchone()
            return row[0] if row else None
        def possa(t, grp):
            k = grp.lower()
            return {"sa": round(t[f"sa_{k}_pg"], 1), "rank": int(t[f"{k}_rank"]), "gp": t["gp"],
                    "c": team_color(int(t[f"{k}_rank"]))} if t else None
        def sa(t, n=32):
            return {"sa": round(t["sa_pg"], 1), "rank": int(t["sa_rank"]), "c": team_color(int(t["sa_rank"]), n),
                    "gp": t["gp"]} if t and t["sa_pg"] else None
        tier = "g" if r["rank"] <= r["n"] / 3 else "r" if r["rank"] > 2 * r["n"] / 3 else "m"
        out.append({
            "rank": r["rank"], "n": r["n"], "tier": tier, "eid": r["event_id"], "start": r["start"],
            "game_name": f"{r['away']} @ {r['home']}", "time": tm, "name": r["name"], "team": r["team"],
            "opp": r["opp"], "pos": r["pos"], "pp": r["pp_unit"], "side": r["side"], "line": r["line"],
            "tline": (lambda lr: lr["grp"] if lr else None)(con.execute("SELECT grp FROM player_line WHERE nhl_id=?", (r["nhl_id"],)).fetchone()),
            "price": fmt_american(r["price"]), "book": r["book"], "p": round(r["p"] * 100, 1), "ev": round(r["ev"] * 100, 1),
            "mu": round(r["mu"], 2), "mm": round(r["mu_market"], 2), "po": round(r["p_over"] * 100, 1), "pu": round(r["p_under"] * 100, 1),
            "fair": round(r["fair_over"] * 100, 1),
            "books": [{"b": b["book"], "l": b["line"], "o": fmt_american(b["o"]), "u": fmt_american(b["u"])} for b in r["books"]],
            "s25": {"avg": round(r["sog_prev"], 2), "gp": r["gp_prev"]} if r["sog_prev"] is not None else None,
            "s26": {"avg": round(r["sog_cur"], 2), "gp": r["gp_cur"]} if r["sog_cur"] is not None else None,
            "form": round(r["form"], 1) if r["form"] is not None else None, "nform": r["n_form"], "last5": r["last5"],
            "h10": round(r["hit_l10"] * 100) if r["hit_l10"] is not None else None,
            "hs": round(r["hit_season"] * 100) if r["hit_season"] is not None else None,
            "toi": {"p": _mmss(r["toi_prev"]), "c": _mmss(r["toi_cur"])},
            "ppt": {"p": _mmss(r["pp_prev"]), "c": _mmss(r["pp_cur"]),
                    "opk_p": _mmss(_pk(con, r["opp"], PREV_SEASON)), "opk_c": _mmss(_pk(con, r["opp"], CUR_SEASON)),
                    "opk_r": _pkr(pkr, r["opp"])},
            "sm": smx.get(r["team"], {}).get(r["pos_grp"]),
            "opp_p": sa(op), "opp_c": sa(oc, n_teams),
            "att": attinfo(r["att"]), "att5": r["att5"],
            "oa_p": oppatt(r["opp_att"][0], r["att_rank"][PREV_SEASON], r["opp"]),
            "oa_c": oppatt(r["opp_att"][1], r["att_rank"][CUR_SEASON], r["opp"]),
            "f_att": round(r["att"]["f_att"], 3) if r["att"] and "f_att" in r["att"] else None,
            "pos_grp": r["pos_grp"], "pos_p": possa(r["opp_pos_prev"], r["pos_grp"]), "pos_c": possa(r["opp_pos_cur"], r["pos_grp"]),
            "def_c": ({"rank": r["opp_def_cur"]["rank"], "c": team_color(r["opp_def_cur"]["rank"], n_teams), "gp": r["opp_def_cur"]["gp"],
                       "xga": round(r["opp_def_cur"]["xga"], 2), "ga": round(r["opp_def_cur"]["ga"], 2),
                       "sa": round(r["opp_def_cur"]["sa"], 1)} if r.get("opp_def_cur") else None),
            "opp_pre": {"rank": PRESEASON_D[r["opp"]], "tie": r["opp"] in PRESEASON_D_TIES, "c": team_color(PRESEASON_D[r["opp"]])},
            "why": [[c, t] for c, t in r["reasons"]],
        })
    return out


def _games_payload(con, date, eids=None):
    def f1(x, d=1):
        return None if x is None else round(x, d)

    def side(c):
        return None if not c else {"side": c["side"], "team": c.get("team"), "line": c.get("line"), "price": fmt_american(c["price"]),
                                   "book": c["book"], "p": round(c["p"] * 100, 1), "market": round(c["market"] * 100, 1),
                                   "ev": round(c["ev"] * 100, 1)}
    out = []
    for g in build_game_rows(con, date):
        if eids is not None and g["eid"] not in eids:
            continue
        tm, _ = _et(g["start"])

        def gl(row):
            if not row or not row["goalie_name"]:
                return None
            return {"name": row["goalie_name"], "status": STATUS.get(row["status"], "Unconfirmed")}
        ml, tot = g["ml"], g["total"]
        ref = next((b for b in g["books"] if b["book"] == "fanduel"), g["books"][0] if g["books"] else None)
        out.append({
            "eid": g["eid"], "start": g["start"], "time": tm, "home": g["home"], "away": g["away"],
            "lamH": round(g["lamH"], 2), "lamA": round(g["lamA"], 2), "tot": round(g["lamH"] + g["lamA"], 2),
            "b2b": g["b2b"], "gh": gl(g["goalie_home"]), "ga": gl(g["goalie_away"]),
            "pre": {"home": round(g["p_home_model"] * 100, 1), "away": round((1 - g["p_home_model"]) * 100, 1)},
            "mkt": {"ml_h": fmt_american(ref["ml_home"]) if ref and ref["ml_home"] is not None else None,
                    "ml_a": fmt_american(ref["ml_away"]) if ref and ref["ml_away"] is not None else None,
                    "line": ref["total"] if ref else None,
                    "over": fmt_american(ref["over"]) if ref and ref["over"] is not None else None,
                    "under": fmt_american(ref["under"]) if ref and ref["under"] is not None else None},
            "ml": None if not ml else {"fair_h": round(ml["fair_home"] * 100, 1), "model_h": round(ml["model_home"] * 100, 1),
                                       "final_h": round(ml["final_home"] * 100, 1), "best": side(ml["best"]), "label": ml["label"],
                                       "cands": [side(c) for c in ml["cands"]]},
            "spread": None if not g.get("spread") else {
                "best": side(g["spread"]["best"]), "label": g["spread"]["label"], "cover_h": round(g["spread"]["model_home_cover"] * 100, 1),
                "home": f"{g['home']} {g['spread']['home_line']:+g} ({fmt_american(g['spread']['home_price'])})",
                "away": f"{g['away']} {g['spread']['away_line']:+g} ({fmt_american(g['spread']['away_price'])})"},
            "total": None if not tot else {"line": tot["line"], "model_over": round(tot["model_over"] * 100, 1),
                                           "fair_over": round(tot["fair_over"] * 100, 1), "best": side(tot["best"]), "label": tot["label"]},
            "books": [{"b": b["book"], "mh": fmt_american(b["ml_home"]) if b["ml_home"] is not None else None,
                       "ma": fmt_american(b["ml_away"]) if b["ml_away"] is not None else None, "t": b["total"],
                       "o": fmt_american(b["over"]) if b["over"] is not None else None,
                       "u": fmt_american(b["under"]) if b["under"] is not None else None} for b in g["books"]],
            "st": {k: {kk: f1(vv, 2) if kk not in ("gp_c",) else vv for kk, vv in v.items()} for k, v in g["stats"].items()},
            "parts": {"h": {k: f1(v, 2) for k, v in g["h"].items()}, "a": {k: f1(v, 2) for k, v in g["a"].items()}},
        })
    return out


def _parlay_history(con, date):
    """Settled and open parlays from the last 14 days (before today), newest first, from the logged legs."""
    out = {}
    for r in con.execute("SELECT * FROM pred_log WHERE kind='parlay_leg' AND date<? ORDER BY date DESC", (date,)):
        info = json.loads(r["extra"] or "{}")
        k = (r["date"], info.get("parlay"))
        p = out.setdefault(k, {"date": r["date"], "n": info.get("parlay"), "price": info.get("parlay_price"), "legs": []})
        p["legs"].append({"name": r["name"], "side": r["side"], "line": r["line"], "price": fmt_american(r["price"]),
                          "book": r["book"], "outcome": r["outcome"], "actual": r["actual"], "settled": r["settled"]})
    res = []
    for k in sorted(out, reverse=True)[:60]:
        p = out[k]
        oc = [leg["outcome"] for leg in p["legs"]]
        p["result"] = ("open" if any(o is None for o in oc) else "W" if all(o in (1.0, 0.5) for o in oc) and any(o == 1.0 for o in oc)
                       else "push" if all(o == 0.5 for o in oc) else "L")
        res.append(p)
    wl = [p["result"] for p in res]
    return {"rows": res, "w": wl.count("W"), "l": wl.count("L")}


def _safest_payload(con, date, eids=None):
    rows = [g for g in build_game_rows(con, date) if eids is None or g["eid"] in eids]
    c = best_value_pick(rows)
    if not c:
        return None
    return {"game": c["game"], "market": c["market_name"], "sel": c["label_sel"], "price": fmt_american(c["price"]),
            "book": c["book"], "p": round(c["p"] * 100, 1), "mkt": round(c["market"] * 100, 1), "ev": round(c["ev"] * 100, 1), "label": c["label"]}


def _backup(con, team, starter_id):
    """The goalie most likely to start if the projected starter does not: next on the team's depth chart."""
    rows = con.execute("SELECT * FROM team_goalies WHERE team=? AND (nhl_id IS NULL OR nhl_id != COALESCE(?, -1)) ORDER BY depth",
                       (team, starter_id)).fetchall()
    if not rows:
        return None
    b = rows[0]
    gid = b["nhl_id"]
    prev = _goalie_season(con, con.execute("SELECT * FROM goalie_season WHERE nhl_id=? AND season=?", (gid, PREV_SEASON)).fetchone()) if gid else None
    cur = _goalie_season(con, con.execute("SELECT * FROM goalie_season WHERE nhl_id=? AND season=?", (gid, CUR_SEASON)).fetchone()) if gid else None
    return {"name": b["name"], "depth": b["depth"], "p": prev, "c": cur}


def _goalie_panel(con, date, eids=None):
    """Every game's two starting goalies with status and last-season / this-season numbers."""
    out = []
    from ..games import b2b_teams
    b2b = b2b_teams(date)
    for g in con.execute("SELECT * FROM game WHERE date=? ORDER BY start_utc, event_id", (date,)):
        if eids is not None and g["event_id"] not in eids:
            continue
        tm, _ = _et(g["start_utc"])
        sides = []
        for team in (g["away"], g["home"]):
            p = con.execute("SELECT * FROM goalie_proj WHERE event_id=? AND team=?", (g["event_id"], team)).fetchone()
            if not p or not p["goalie_name"]:
                sides.append({"team": team, "name": None, "status": "Unconfirmed"})
                continue
            gid = p["goalie_id"]
            prev = _goalie_season(con, con.execute("SELECT * FROM goalie_season WHERE nhl_id=? AND season=?", (gid, PREV_SEASON)).fetchone()) if gid else None
            cur = _goalie_season(con, con.execute("SELECT * FROM goalie_season WHERE nhl_id=? AND season=?", (gid, CUR_SEASON)).fetchone()) if gid else None
            sides.append({"team": team, "name": p["goalie_name"], "status": STATUS.get(p["status"], "Unconfirmed"),
                          "p": prev, "c": cur, "fallback": bool(p["is_fallback"]), "note": p["note"] or "",
                          "source": p["source"], "backup": _backup(con, team, gid), "b2b": team in b2b})
        out.append({"game": f"{g['away']} @ {g['home']}", "time": tm, "sides": sides})
    return out


def build_site(date):
    con = connect()
    late = _late_eids(con, date)
    rows, missing = build_rows(con, date)
    rows = [r for r in rows if r["event_id"] in late]
    _renumber(rows, True)
    all_rows, _ = build_rows(con, date, all_players=True)
    all_rows = [r for r in all_rows if r["event_id"] in late]
    all_rows = sorted([r for r in all_rows if r["ev"] is not None and r["p"] >= MIN_P_ALL], key=lambda r: r["rank"])[:TOP_N_ALL]
    for i_, r_ in enumerate(all_rows):  # renumber 1..N now that the list is filtered
        r_["rank"], r_["pool_n"] = i_ + 1, len(all_rows)
    matchups = team_matchups(con, date, late)
    _TM.clear()
    _TM.update({m["team"]: m for m in matchups})
    meta = {k: (con.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone() or [None])[0]
            for k in ("last_pp_update", "daily_updated")}
    payload = {"date": date, "rows": _rows(con, rows), "all": _rows(con, all_rows), "meta": meta,
               "matchups": [{"team": m["team"], "opp": m["opp"], "home": m["home"],
                            "goalie": m["goalie"], "status": m["status"], "gf": round((m["gf"] - 1) * 100, 1), "p_goalie": round(m["p_goalie"] * 100),
                            "off": round(m["off"], 2) if m["off"] is not None else None, "p_off": round(m["p_off"] * 100),
                            "def": round(m["def_score"], 1), "p_def": round(m["p_def"] * 100),
                            "score": round(m["score"], 1)} for m in matchups],
               "parlays": build_parlays(con, date), "parlay_hist": _parlay_history(con, date), "missing": [m[0] for m in missing], "goalies": _goalie_panel(con, date, late), "sog": _sog_payload(con, date, late), "games": _games_payload(con, date, late), "safe": _safest_payload(con, date, late), "results": _results(con)}
    html = TEMPLATE.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))
    f = DATA / f"site_{date}.html"  # artifact variant (host adds the document skeleton)
    f.write_text(html, encoding="utf-8")
    # standalone variant: opens directly in a browser; always the latest build
    cut = html.index('<div class="wrap">')
    standalone = ('<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">'
                  '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                  + html[:cut] + "<style>body{margin:0}</style></head><body>" + html[cut:] + "</body></html>")
    standalone = standalone.replace("<head>", '<head><meta name="robots" content="noindex">', 1)
    (ROOT / "board.html").write_text(standalone, encoding="utf-8")
    (ROOT / "docs").mkdir(exist_ok=True)  # published copy (GitHub Pages serves /docs)
    (ROOT / "docs" / "index.html").write_text(standalone, encoding="utf-8")
    return str(ROOT / "board.html"), len(payload["rows"]) + len(payload["all"])
