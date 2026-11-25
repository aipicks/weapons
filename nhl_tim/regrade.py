"""Short-list record: past days re-graded as if the 2026-11-25 card rules had been used (shown first on the Results tab).

Chosen only from what was logged before puck drop (pred_log), graded at the logged price, 1 unit each:
  goal scorers  the 3 likeliest per day, using the recalibrated goal probability (W["market_weight"], W["calib_b"])
  shots         +4% EV or better, one per game, at most 5 per day (site.SOG_MIN_EV / SOG_TOP_N)
  parlays       the first 2 parlays of each day (a parlay wins only if both legs win)
  game bets     moneyline / puck line / total at +3% EV or better, at most 2 per day
"""
import json
import math

from .config import W


def _units(win, price):
    if win is None or price is None:
        return 0.0
    return (price / 100 if price > 0 else 100 / -price) if win else -1.0


def _logit(p):
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


def _summ(ps):
    w = sum(1 for p in ps if p["win"])
    u = round(sum(p["u"] for p in ps), 2)
    return {"n": len(ps), "w": w, "l": len(ps) - w, "units": u, "roi": round(u / len(ps), 3) if ps else None}


def card_history(con):
    rows = [dict(r) for r in con.execute("SELECT * FROM pred_log WHERE settled=1 AND outcome IN (0, 1)")]
    by = {}
    for r in rows:
        by.setdefault(r["kind"], []).append(r)
    picks = []
    days = sorted({r["date"] for r in rows})
    mw, cb = W["market_weight"], W.get("calib_b", 1.0)
    for d in days:
        g = [r for r in by.get("goal", []) if r["date"] == d and r["p_mkt"] is not None and r["p_model"] is not None]
        seen, gl = set(), []
        for r in g:
            q = 1 / (1 + math.exp(-cb * (mw * _logit(r["p_mkt"]) + (1 - mw) * _logit(r["p_model"]))))
            if q >= 0.15 and r["name"] not in seen:
                seen.add(r["name"]); gl.append((q, r))
        for q, r in sorted(gl, key=lambda t: -t[0])[:3]:
            picks.append({"kind": "goal", "date": d, "win": bool(r["outcome"]), "u": _units(r["outcome"], r["price"])})
        s = sorted([r for r in by.get("sog", []) if r["date"] == d and r["ev"] is not None and r["ev"] >= 0.04], key=lambda r: -r["ev"])
        gm, n = set(), 0
        for r in s:
            if r["game"] in gm:
                continue
            gm.add(r["game"]); n += 1
            picks.append({"kind": "sog", "date": d, "win": bool(r["outcome"]), "u": _units(r["outcome"], r["price"])})
            if n == 5:
                break
        par = {}
        for r in by.get("parlay_leg", []):
            if r["date"] != d:
                continue
            x = json.loads(r["extra"] or "{}")
            if (x.get("parlay") or 99) <= 2:
                par.setdefault(x["parlay"], {"price": x.get("parlay_price"), "legs": []})["legs"].append(r["outcome"])
        for p in par.values():
            if len(p["legs"]) == 2:
                win = all(p["legs"])
                picks.append({"kind": "parlay", "date": d, "win": win, "u": _units(win, p["price"])})
        gb = sorted([r for k in ("ml_bet", "spread_bet", "total_bet") for r in by.get(k, []) if r["date"] == d and r["ev"] is not None and r["ev"] >= 0.03],
                    key=lambda r: -r["ev"])[:2]
        for r in gb:
            picks.append({"kind": "game", "date": d, "win": bool(r["outcome"]), "u": _units(r["outcome"], r["price"])})
    kinds = {"goal": "Goal scorers", "sog": "Shots on goal", "parlay": "Shots parlays", "game": "Game bets"}
    return {"total": _summ(picks), "by_kind": {lab: _summ([p for p in picks if p["kind"] == k]) for k, lab in kinds.items()},
            "days": len(days), "per_day": round(len(picks) / len(days), 1) if days else 0}
