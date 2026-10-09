"""Season-long results tracking.

snapshot(): before games start, logs every prediction the board makes (goal scorers, shots lines, moneylines, totals) with
our probability, the market's, the price and the EV.  settle(): after games finish, fills in what actually happened from the
NHL API.  results(): compares our probabilities with the market's (Brier score), checks calibration, and totals up flat-stake
profit, so we can tell whether the model is any good and how much weight it deserves."""
import datetime as dt
import json
import zoneinfo

from .config import GAME
from .db import connect
from .games import best_value_pick, build_game_rows
from .model import build_rows
from .sog import build_sog_rows
from .sources import nhl_api

ET = zoneinfo.ZoneInfo("America/New_York")


def _started(start_utc):
    return dt.datetime.fromisoformat(start_utc.replace("Z", "+00:00")) <= dt.datetime.now(dt.timezone.utc)


def _put(con, date, kind, key, start, **f):
    """Insert/replace a prediction, but never overwrite one after its game has started (keeps it pre-game)."""
    old = con.execute("SELECT 1 FROM pred_log WHERE date=? AND kind=? AND key=?", (date, kind, key)).fetchone()
    if old and _started(start):
        return
    cols = ["pool", "name", "team", "opp", "game", "side", "line", "price", "book", "p", "p_mkt", "p_model", "ev", "label", "extra"]
    vals = [f.get(c) for c in cols]
    con.execute(f"INSERT OR REPLACE INTO pred_log(date, kind, key, start, {','.join(cols)}) VALUES(?,?,?,?,{','.join('?' * len(cols))})",
                (date, kind, key, start, *vals))


def snapshot(con, date):
    n = 0
    games = {g["event_id"]: g for g in con.execute("SELECT * FROM game WHERE date=?", (date,))}
    # ---- goal scorers: every player with anytime odds (pool 0) and the Tim Hortons pools ----
    for pool, rows in ((0, build_rows(con, date, all_players=True)[0]), (-1, build_rows(con, date)[0])):
        for r in rows:
            a = r["atg_display"]
            if not a:
                continue
            p_pool = r["pool"] if pool == -1 else 0
            _put(con, date, "goal", f"{r['nhl_id']}:{p_pool}", r["start"], pool=p_pool, name=r["name"], team=r["team"], opp=r["opp"],
                 game=f"{r['away']} @ {r['home']}", price=a["american"], book=a["book"], p=r["p"], p_mkt=r["p_mkt"],
                 p_model=r["p_fund"], ev=r["ev"], extra=json.dumps({"rank": r["rank"], "pp": r["pp_unit"]}))
            n += 1
    # ---- shots on goal ----
    for r in build_sog_rows(con, date):
        _put(con, date, "sog", f"{r['nhl_id']}", r["start"], name=r["name"], team=r["team"], opp=r["opp"],
             game=f"{r['away']} @ {r['home']}", side=r["side"], line=r["line"], price=r["price"], book="fanduel/ref",
             p=r["p_over"], p_mkt=r["fair_over"], p_model=None, ev=r["ev"], extra=json.dumps({"mu": r["mu"], "best_p": r["p"]}))
        n += 1
    # ---- games: probability rows (calibration) and the best-bet rows (profit) ----
    grows = build_game_rows(con, date)
    for g in grows:
        ml, tot = g["ml"], g["total"]
        if ml:
            _put(con, date, "ml_home", g["eid"], g["start"], name=g["home"], team=g["home"], opp=g["away"], game=f"{g['away']} @ {g['home']}",
                 p=ml["final_home"], p_mkt=ml["fair_home"], p_model=ml["model_home"], ev=None, extra=json.dumps({"lamH": g["lamH"], "lamA": g["lamA"]}))
            b = ml["best"]
            _put(con, date, "ml_bet", g["eid"], g["start"], name=b["team"], team=b["team"], game=f"{g['away']} @ {g['home']}", side=b["side"],
                 price=b["price"], book=b["book"], p=b["p"], p_mkt=b["market"], ev=b["ev"], label=ml["label"])
            n += 2
        if tot:
            b = tot["best"]
            _put(con, date, "total_bet", g["eid"], g["start"], name=f"{b['side']} {b['line']:g}", game=f"{g['away']} @ {g['home']}", side=b["side"],
                 line=b["line"], price=b["price"], book=b["book"], p=b["p"], p_mkt=b["market"], p_model=b["model"], ev=b["ev"], label=tot["label"],
                 extra=json.dumps({"model_total": tot["model_total"]}))
            n += 1
    bv = best_value_pick(grows)
    if bv:
        _put(con, date, "best_value", bv["eid"], next(g["start"] for g in grows if g["eid"] == bv["eid"]), name=bv["label_sel"], game=bv["game"],
             side=bv["side"] if "side" in bv else None, line=bv.get("line"), price=bv["price"], book=bv["book"], p=bv["p"], p_mkt=bv["market"],
             ev=bv["ev"], label=bv["label"], extra=json.dumps({"market": bv["market_name"]}))
        n += 1
    con.commit()
    return n


# ----------------------------------------------------------------------------------------------------------------------
def _final_games(date):
    """{(home, away): {gid, hs, as_, total_ex_so}} for finished games on a date."""
    j = nhl_api.get(f"{nhl_api.WEB}/schedule/{date}", f"schedule_final_{date}", force=True)
    out = {}
    for day in (j or {}).get("gameWeek", []):
        if day.get("date") != date:
            continue
        for g in day.get("games", []):
            if g.get("gameState") not in ("OFF", "FINAL"):
                continue
            hs, as_ = g["homeTeam"].get("score"), g["awayTeam"].get("score")
            if hs is None or as_ is None:
                continue
            so = (g.get("gameOutcome") or {}).get("lastPeriodType") == "SO"
            out[(g["homeTeam"]["abbrev"], g["awayTeam"]["abbrev"])] = {"gid": g["id"], "hs": hs, "as": as_, "total": hs + as_ - (1 if so else 0)}
    return out


def _players(gid):
    b = nhl_api.get(f"{nhl_api.WEB}/gamecenter/{gid}/boxscore", f"box/{gid}", force=False)
    out = {}
    for side in ("homeTeam", "awayTeam"):
        for grp in ("forwards", "defense"):
            for p in (b.get("playerByGameStats", {}).get(side, {}).get(grp, []) or []):
                out[p["playerId"]] = {"goals": p.get("goals", 0), "sog": p.get("sog", 0)}
    return out


def _result(kind, row, game, box):
    """(outcome, actual) for one logged prediction, or (None, None) if it cannot be settled / is void."""
    if kind == "ml_home":
        return (1.0 if game["hs"] > game["as"] else 0.0), game["hs"] - game["as"]
    if kind == "ml_bet":
        won = (game["hs"] > game["as"]) == (row["side"] == "home")
        return (1.0 if won else 0.0), game["hs"] - game["as"]
    if kind in ("total_bet", "total_over"):
        t = game["total"]
        if t == row["line"]:
            return 0.5, t  # push
        over = t > row["line"]
        return (1.0 if (over == (row["side"] == "Over")) else 0.0), t
    if kind == "best_value":
        info = json.loads(row["extra"] or "{}")
        if info.get("market") == "Moneyline":
            home_pick = row["name"].startswith(row["game"].split(" @ ")[1]) if row["game"] else False
            won = (game["hs"] > game["as"]) == home_pick
            return (1.0 if won else 0.0), game["hs"] - game["as"]
        t = game["total"]
        if t == row["line"]:
            return 0.5, t
        return (1.0 if ((t > row["line"]) == (row["side"] == "Over")) else 0.0), t
    pid = int(row["key"].split(":")[0])
    pl = box.get(pid)
    if pl is None:
        return None, None
    if kind == "goal":
        return (1.0 if pl["goals"] > 0 else 0.0), pl["goals"]
    if kind == "sog":
        if pl["sog"] == row["line"]:
            return 0.5, pl["sog"]
        over = pl["sog"] > row["line"]
        return (1.0 if over == (row["side"] == "Over") else 0.0), pl["sog"]
    return None, None


def settle(con):
    """Fill in outcomes for every unsettled prediction whose game is final. Safe to run repeatedly."""
    dates = [r["date"] for r in con.execute("SELECT DISTINCT date FROM pred_log WHERE settled=0 ORDER BY date")]
    done = 0
    for date in dates:
        finals = _final_games(date)
        by_event = {}
        for g in con.execute("SELECT * FROM game WHERE date=?", (date,)):
            if (g["home"], g["away"]) in finals:
                by_event[g["event_id"]] = (g, finals[(g["home"], g["away"])])
        boxes = {}
        for r in con.execute("SELECT * FROM pred_log WHERE date=? AND settled=0", (date,)).fetchall():
            row = dict(r)
            if row["kind"] in ("ml_home", "ml_bet", "total_bet", "best_value"):
                ev_id = row["key"]
            else:  # player rows: find the game from the "AWAY @ HOME" label
                away, home = row["game"].split(" @ ")
                ev_id = next((e for e, (g, f) in by_event.items() if g["home"] == home and g["away"] == away), None)
            if ev_id not in by_event:
                continue
            g, fin = by_event[ev_id]
            if row["kind"] in ("goal", "sog") and fin["gid"] not in boxes:
                boxes[fin["gid"]] = _players(fin["gid"])
            ml_row = dict(row)
            if row["kind"] in ("ml_bet",):
                ml_row["side"] = row["side"]
            outcome, actual = _result(row["kind"], ml_row, fin, boxes.get(fin["gid"], {}))
            if row["kind"] == "total_over":
                pass
            con.execute("UPDATE pred_log SET outcome=?, actual=?, settled=1 WHERE date=? AND kind=? AND key=?",
                        (outcome, actual, date, row["kind"], row["key"]))
            done += 1
    con.commit()
    return done


# ----------------------------------------------------------------------------------------------------------------------
def _dec(american):
    return 1 + (american / 100 if american > 0 else 100 / -american)


def _profit(r):
    """Flat $1 profit for a settled bet row."""
    if r["outcome"] is None or r["price"] is None:
        return None
    if r["outcome"] == 0.5:
        return 0.0
    return (_dec(r["price"]) - 1) if r["outcome"] == 1 else -1.0


def _brier(rows, field):
    xs = [(r[field] - r["outcome"]) ** 2 for r in rows if r[field] is not None and r["outcome"] in (0.0, 1.0)]
    return (sum(xs) / len(xs), len(xs)) if xs else (None, 0)


def _bucket_table(rows, edges):
    out = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [r for r in rows if lo <= r["p"] < hi and r["outcome"] in (0.0, 1.0)]
        if sel:
            out.append({"range": f"{lo*100:.0f}-{hi*100:.0f}%", "n": len(sel), "pred": round(sum(r["p"] for r in sel) / len(sel) * 100, 1),
                        "actual": round(sum(r["outcome"] for r in sel) / len(sel) * 100, 1)})
    return out


def results(con):
    q = lambda sql, *a: [dict(r) for r in con.execute(sql, a)]
    days = q("SELECT COUNT(DISTINCT date) n, MIN(date) first FROM pred_log WHERE settled=1")[0]
    out = {"days_settled": days["n"], "since": days["first"], "pending": q("SELECT COUNT(*) n FROM pred_log WHERE settled=0")[0]["n"]}

    goal = q("SELECT * FROM pred_log WHERE kind='goal' AND settled=1 AND outcome IS NOT NULL AND pool=0")
    top = []  # the +EV GOAL SCORERS list each day: 15%+ probability, best 30 by EV
    for d in sorted({r["date"] for r in goal}):
        day = [r for r in goal if r["date"] == d and r["p"] >= 0.15 and r["ev"] is not None]
        top += sorted(day, key=lambda r: -r["ev"])[:30]
    b_m, n_m = _brier(goal, "p_mkt")
    b_f, n_f = _brier(goal, "p")
    out["goal"] = {"n": len(goal), "hit": round(sum(r["outcome"] for r in goal) / len(goal) * 100, 1) if goal else None,
                   "avg_p": round(sum(r["p"] for r in goal) / len(goal) * 100, 1) if goal else None,
                   "brier_model": b_f, "brier_market": b_m,
                   "calib": _bucket_table(goal, [0, .05, .10, .15, .20, .30, .40, 1.01]),
                   "ev_list": {"n": len(top), "hit": round(sum(r["outcome"] for r in top) / len(top) * 100, 1) if top else None,
                               "avg_p": round(sum(r["p"] for r in top) / len(top) * 100, 1) if top else None,
                               "profit": round(sum(_profit(r) for r in top), 2) if top else None}}
    tim = q("SELECT * FROM pred_log WHERE kind='goal' AND settled=1 AND outcome IS NOT NULL AND pool>0")
    out["tim"] = []
    for pool in (1, 2, 3):
        pr = [r for r in tim if r["pool"] == pool]
        tops = [r for r in pr if json.loads(r["extra"] or "{}").get("rank") == 1]
        out["tim"].append({"pool": pool, "n": len(pr), "top_n": len(tops),
                           "top_hit": round(sum(r["outcome"] for r in tops) / len(tops) * 100, 1) if tops else None,
                           "top_p": round(sum(r["p"] for r in tops) / len(tops) * 100, 1) if tops else None,
                           "all_hit": round(sum(r["outcome"] for r in pr) / len(pr) * 100, 1) if pr else None})

    def bets(kind, label_filter=None):
        rows = q("SELECT * FROM pred_log WHERE kind=? AND settled=1 AND outcome IS NOT NULL", kind)
        if label_filter:
            rows = [r for r in rows if r["label"] in label_filter]
        profs = [_profit(r) for r in rows if _profit(r) is not None]
        dec = [r for r in rows if r["outcome"] in (0.0, 1.0)]
        return {"n": len(rows), "wins": int(sum(1 for r in dec if r["outcome"] == 1)), "profit": round(sum(profs), 2) if profs else 0.0,
                "avg_ev": round(sum(r["ev"] for r in rows) / len(rows) * 100, 1) if rows else None,
                "avg_p": round(sum(r["p"] for r in rows) / len(rows) * 100, 1) if rows else None}
    out["ml"] = {"all": bets("ml_bet"), "bet": bets("ml_bet", ("BET", "LEAN"))}
    out["total"] = {"all": bets("total_bet"), "bet": bets("total_bet", ("BET", "LEAN"))}
    out["best_value"] = bets("best_value")
    mlh = q("SELECT * FROM pred_log WHERE kind='ml_home' AND settled=1 AND outcome IS NOT NULL")
    bm, nm = _brier(mlh, "p_mkt")
    bf, _ = _brier(mlh, "p")
    br, _ = _brier(mlh, "p_model")
    out["ml_brier"] = {"n": nm, "model_raw": br, "blend": bf, "market": bm}
    sog = q("SELECT * FROM pred_log WHERE kind='sog' AND settled=1 AND outcome IS NOT NULL")
    best = [dict(r, p=json.loads(r["extra"] or "{}").get("best_p", r["p"])) for r in sog]
    profs = [_profit(r) for r in best if _profit(r) is not None]
    pos = [r for r in best if (r["ev"] or 0) > 0]
    out["sog"] = {"n": len(sog), "hit": round(sum(1 for r in best if r["outcome"] == 1) / max(1, sum(1 for r in best if r["outcome"] in (0.0, 1.0))) * 100, 1) if best else None,
                  "avg_p": round(sum(r["p"] for r in best) / len(best) * 100, 1) if best else None,
                  "profit_all": round(sum(profs), 2) if profs else 0.0,
                  "pos_n": len(pos), "pos_profit": round(sum(_profit(r) for r in pos if _profit(r) is not None), 2) if pos else 0.0}
    return out
