"""Mobile-friendly single-file dashboard (data embedded as JSON). Publish data/site_<date>.html as an Artifact."""
import json
from pathlib import Path

from ..config import DATA, ROOT, CUR_SEASON
from ..sources import nhl_api
from ..db import connect
from ..model import build_rows
from ..odds_math import fmt_american
from .table import _et, team_color, third_color

TEMPLATE = (Path(__file__).parent / "site_template.html").read_text(encoding="utf-8")
STATUS = {"Confirmed": "Confirmed", "Expected": "Projected", "Unknown": "Unknown"}


def _goalie_season(con, s):
    if not s or not s["gp"]:
        return None
    n_g = con.execute("SELECT COUNT(gaa_rank) FROM goalie_season WHERE season=?", (s["season"],)).fetchone()[0]
    n_s = con.execute("SELECT COUNT(sv_rank) FROM goalie_season WHERE season=?", (s["season"],)).fetchone()[0]
    return {"gp": s["gp"], "starts": s["starts"], "gaa": round(s["gaa"], 2), "gr": s["gaa_rank"],
            "gc": third_color(s["gaa_rank"], n_g), "sv": round(s["sv_pct"], 3), "svr": s["sv_rank"],
            "svc": third_color(s["sv_rank"], n_s), "qual": bool(s["qualified"])}


def _cur_goal_games(nhl_id):
    try:
        log = nhl_api.game_log(nhl_id, CUR_SEASON, force=True)
        return sum(1 for g in log if g["goals"] > 0)
    except Exception:
        return None


def build_site(date):
    con = connect()
    rows, missing = build_rows(con, date)
    meta = {k: (con.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone() or [None])[0]
            for k in ("last_pp_update", "daily_updated")}
    out = []
    for r in rows:
        tm, _ = _et(r["start"])
        prev, cur, ot, gp, go = r["prev"], r["cur"], r["opp_team"], r["goalie_proj"], r["game_odds"]
        n, rk = r["pool_n"], r["rank"]
        tier = "g" if rk <= n / 3 else "r" if rk > 2 * n / 3 else "m"
        game = None
        if go:
            game = {"total": go["total"], "over": fmt_american(go["over_price"]), "fav": None, "ml": None}
            if go["ml_home"] is not None and go["ml_away"] is not None:
                home_fav = go["ml_home"] < go["ml_away"]
                game["fav"] = r["home"] if home_fav else r["away"]
                game["ml"] = fmt_american(go["ml_home"] if home_fav else go["ml_away"])
        a = r["atg_display"]
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
            "pp": r["pp_unit"],
            "any": {"am": fmt_american(a["american"]), "pr": round(a["implied"] * 100, 1)} if a else None,
            "game": game, "xg": round(r["xg"], 2) if r["xg"] is not None else None,
            "s25": {"gp": prev["gp"], "sog": prev["sog"], "gg": prev["goal_games"],
                    "ggp": round(prev["goal_game_pct"] * 100, 1)} if prev else None,
            "s26": {"gp": cur["gp"], "sog": cur["sog"], "gg": _cur_goal_games(r["nhl_id"]) if cur["gp"] else 0}
            if cur else {"gp": 0, "sog": 0, "gg": 0},
            "opp_d": {"ga": round(ot["ga_pg"], 2), "rank": ot["ga_rank"], "c": team_color(ot["ga_rank"])} if ot else None,
            "goalie": goalie, "why": [[p, t] for p, t in r["reasons_signed"]],
        })
    payload = {"date": date, "rows": out, "meta": meta, "missing": [m[0] for m in missing]}
    html = TEMPLATE.replace("/*__DATA__*/null", json.dumps(payload, ensure_ascii=False))
    f = DATA / f"site_{date}.html"  # artifact variant (host adds the document skeleton)
    f.write_text(html, encoding="utf-8")
    # standalone variant: opens directly in a browser; always the latest build
    cut = html.index('<div class="wrap">')
    standalone = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                  '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                  + html[:cut] + "<style>body{margin:0}</style></head><body>" + html[cut:] + "</body></html>")
    standalone = standalone.replace("<head>", '<head><meta name="robots" content="noindex">', 1)
    (ROOT / "board.html").write_text(standalone, encoding="utf-8")
    (ROOT / "docs").mkdir(exist_ok=True)  # published copy (GitHub Pages serves /docs)
    (ROOT / "docs" / "index.html").write_text(standalone, encoding="utf-8")
    return str(ROOT / "board.html"), len(out)
