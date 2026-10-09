"""Mobile-friendly single-file dashboard (data embedded as JSON). Publish data/site_<date>.html as an Artifact."""
import json
from pathlib import Path

from ..config import DATA, ROOT, CUR_SEASON, PREV_SEASON, PRESEASON_D, PRESEASON_D_TIES
from ..sources import nhl_api
from ..db import connect
from ..model import build_rows
from ..sog import build_sog_rows
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


def _rows(con, rows):
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
            "pp": r["pp_unit"], "ev": ev, "edge": edge,
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
            "ppt": {"p": _mmss(r["pp_toi"][PREV_SEASON]), "c": _mmss(r["pp_toi"][CUR_SEASON]),
                    "opk_p": _mmss(r["opp_pk_toi"][PREV_SEASON]), "opk_c": _mmss(r["opp_pk_toi"][CUR_SEASON])},
            "goalie": goalie, "why": [[p, t] for p, t in r["reasons_signed"]],
        })
    return out


def _sog_payload(con, date):
    out = []
    for r in build_sog_rows(con, date):
        tm, _ = _et(r["start"])
        n_teams = con.execute("SELECT COUNT(*) FROM team_season WHERE season=?", (CUR_SEASON,)).fetchone()[0]
        op, oc = r["opp_prev"], r["opp_cur"]
        def sa(t, n=32):
            return {"sa": round(t["sa_pg"], 1), "rank": int(t["sa_rank"]), "c": team_color(int(t["sa_rank"]), n),
                    "gp": t["gp"]} if t and t["sa_pg"] else None
        tier = "g" if r["rank"] <= r["n"] / 3 else "r" if r["rank"] > 2 * r["n"] / 3 else "m"
        out.append({
            "rank": r["rank"], "n": r["n"], "tier": tier, "eid": r["event_id"], "start": r["start"],
            "game_name": f"{r['away']} @ {r['home']}", "time": tm, "name": r["name"], "team": r["team"],
            "opp": r["opp"], "pos": r["pos"], "pp": r["pp_unit"], "side": r["side"], "line": r["line"],
            "price": fmt_american(r["price"]), "p": round(r["p"] * 100, 1), "ev": round(r["ev"] * 100, 1),
            "mu": round(r["mu"], 2), "mm": round(r["mu_market"], 2), "po": round(r["p_over"] * 100, 1), "pu": round(r["p_under"] * 100, 1),
            "fair": round(r["fair_over"] * 100, 1),
            "books": [{"b": b["book"], "l": b["line"], "o": fmt_american(b["o"]), "u": fmt_american(b["u"])} for b in r["books"]],
            "s25": {"avg": round(r["sog_prev"], 2), "gp": r["gp_prev"]} if r["sog_prev"] is not None else None,
            "s26": {"avg": round(r["sog_cur"], 2), "gp": r["gp_cur"]} if r["sog_cur"] is not None else None,
            "form": round(r["form"], 1) if r["form"] is not None else None, "nform": r["n_form"], "last5": r["last5"],
            "h10": round(r["hit_l10"] * 100) if r["hit_l10"] is not None else None,
            "hs": round(r["hit_season"] * 100) if r["hit_season"] is not None else None,
            "toi": {"p": _mmss(r["toi_prev"]), "c": _mmss(r["toi_cur"])},
            "ppt": {"p": _mmss(r["pp_prev"]), "c": _mmss(r["pp_cur"])},
            "opp_p": sa(op), "opp_c": sa(oc, n_teams),
            "opp_pre": {"rank": PRESEASON_D[r["opp"]], "tie": r["opp"] in PRESEASON_D_TIES, "c": team_color(PRESEASON_D[r["opp"]])},
            "why": [[c, t] for c, t in r["reasons"]],
        })
    return out


def _goalie_panel(con, date):
    """Every game's two starting goalies with status and last-season / this-season numbers."""
    out = []
    for g in con.execute("SELECT * FROM game WHERE date=? ORDER BY start_utc, event_id", (date,)):
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
                          "p": prev, "c": cur, "fallback": bool(p["is_fallback"]), "note": p["note"] or ""})
        out.append({"game": f"{g['away']} @ {g['home']}", "time": tm, "sides": sides})
    return out


def build_site(date):
    con = connect()
    rows, missing = build_rows(con, date)
    all_rows, _ = build_rows(con, date, all_players=True)
    all_rows = sorted([r for r in all_rows if r["ev"] is not None and r["p"] >= MIN_P_ALL], key=lambda r: r["rank"])[:TOP_N_ALL]
    for i_, r_ in enumerate(all_rows):  # renumber 1..N now that the list is filtered
        r_["rank"], r_["pool_n"] = i_ + 1, len(all_rows)
    meta = {k: (con.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone() or [None])[0]
            for k in ("last_pp_update", "daily_updated")}
    payload = {"date": date, "rows": _rows(con, rows), "all": _rows(con, all_rows), "meta": meta,
               "missing": [m[0] for m in missing], "goalies": _goalie_panel(con, date), "sog": _sog_payload(con, date)}
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
