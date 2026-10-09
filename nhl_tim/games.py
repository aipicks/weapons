"""Game model: moneyline and total value for every game on the slate.

Expected goals for each team come from MoneyPuck 5v5 / power-play / penalty-kill expected-goal rates, how much
power-play time each side should get, the opposing starting goalie (goals saved above expected, shrunk), home ice and
back-to-backs. Goals are Poisson; that gives P(win) (with overtime) and P(over/under). The result is blended with the
de-vigged market and EV is computed at the best available price. Weights are starting priors (config.GAME), not fitted."""
import datetime as dt
import math
import statistics

from .config import CUR_SEASON, GAME, MARKET_BOOK, PREV_SEASON
from .model import logit, sigmoid
from .odds_math import american_to_prob
from .sources import nhl_api

MAXG = 16


def pois(lam, n=MAXG):
    p = [math.exp(-lam)]
    for k in range(1, n):
        p.append(p[-1] * lam / k)
    return p


def _dec(american):
    return 1 + (american / 100 if american > 0 else 100 / -american)


def _ev(p, american):
    return p * (_dec(american) - 1) - (1 - p)


def _fair2(a, b):
    pa, pb = american_to_prob(a), american_to_prob(b)
    return pa / (pa + pb)


def _rates(row, sit):
    """per-60 xGF / xGA and minutes per game for one situation of one team-season."""
    d = row.get(sit)
    if not d or not d["ice"]:
        return None
    hrs = d["ice"] / 3600
    return {"xgf60": d["xgf"] / hrs, "xga60": d["xga"] / hrs, "min": d["ice"] / 60 / d["gp"], "gp": d["gp"]}


def load_team_mp(con):
    out = {}
    for r in con.execute("SELECT * FROM team_mp"):
        out.setdefault((r["team"], r["season"]), {})[r["sit"]] = dict(r)
    return out


def _team_profile(tm, team):
    """Blend last season and this season (weight on this season grows with games played)."""
    prev, cur = tm.get((team, PREV_SEASON)), tm.get((team, CUR_SEASON))
    prof = {}
    for key, sit in (("ev", "5on5"), ("pp", "5on4"), ("pk", "4on5")):
        a = _rates(prev, sit) if prev else None
        b = _rates(cur, sit) if cur else None
        if a and b:
            w = b["gp"] / (b["gp"] + GAME["cur_k"])
            prof[key] = {k: (1 - w) * a[k] + w * b[k] for k in ("xgf60", "xga60", "min")}
        else:
            prof[key] = a or b
    return prof


def _goalie_factor(con, gid, status, lg_ratio):
    """Multiplier on opposing goals from the goalie: <1 stops more than expected, >1 less (shrunk, damped by status)."""
    if not gid:
        return 1.0
    rows = {r["season"]: r for r in con.execute("SELECT * FROM goalie_xg WHERE nhl_id=?", (gid,))}
    xg = sum(r["xg"] for r in rows.values())
    goals = sum(r["goals"] for r in rows.values())
    shots = sum(r["shots"] for r in rows.values())
    if xg <= 0:
        return 1.0
    rel = (goals - xg) / xg                         # +0.05 = allows 5% more goals than expected
    rel *= shots / (shots + GAME["goalie_shots_k"])  # small samples shrink to 0
    f = min(max(1 + rel, 0.88), 1.12)
    w = {"Confirmed": 1.0, "Likely": 0.8}.get(status, 0.5)
    return 1 + (f - 1) * w


def b2b_teams(date):
    """Teams that played yesterday (second night of a back-to-back today)."""
    y = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    try:
        j = nhl_api.get(f"{nhl_api.WEB}/schedule/{y}", f"schedule_{y}", force=False)
        teams = set()
        for day in (j or {}).get("gameWeek", []):
            if day.get("date") == y:
                for g in day.get("games", []):
                    teams |= {g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"]}
        return teams
    except Exception:
        return set()


def expected_goals(tm, A, B, con, goalie_B, status_B, venue, a_b2b, b_b2b, lg):
    pa, pb = _team_profile(tm, A), _team_profile(tm, B)
    if not (pa.get("ev") and pb.get("ev") and pa.get("pp") and pb.get("pk") and pa.get("pk") and pb.get("pp")):
        return None
    pp_min_A = pa["pp"]["min"] * (pb["pk"]["min"] / lg["pk_min"])   # A's expected PP minutes
    pp_min_B = pb["pp"]["min"] * (pa["pk"]["min"] / lg["pk_min"])   # = A's expected shorthanded minutes
    ev_min = max(30.0, 60.0 - pp_min_A - pp_min_B)
    ev = ev_min / 60 * pa["ev"]["xgf60"] * pb["ev"]["xga60"] / lg["ev60"]
    pp = pp_min_A / 60 * pa["pp"]["xgf60"] * pb["pk"]["xga60"] / lg["pk_xga60"]
    sh = pp_min_B / 60 * pa["pk"]["xgf60"] * pb["pp"]["xga60"] / lg["pp_xga60"]
    xg = ev + pp + sh
    lam = xg * lg["goals_per_xg"] * _goalie_factor(con, goalie_B, status_B, lg["goals_per_xg"])
    lam *= GAME["home"] if venue == "home" else GAME["away"]
    if a_b2b:
        lam *= GAME["b2b_off"]
    if b_b2b:
        lam *= GAME["b2b_def"]
    return {"lam": lam, "xg": xg, "ev": ev, "pp": pp, "sh": sh, "pp_min": pp_min_A, "ev_min": ev_min}


def league_constants(tm):
    ev, pp, pk, xg, g = [], [], [], 0.0, 0.0
    pk_min, pp_xga = [], []
    for (team, season), sits in tm.items():
        if season != PREV_SEASON:
            continue
        a, b, c = _rates(sits, "5on5"), _rates(sits, "5on4"), _rates(sits, "4on5")
        if a and b and c:
            ev.append(a["xgf60"]); pp.append(b["xgf60"]); pk.append(c["xga60"]); pk_min.append(c["min"]); pp_xga.append(b["xga60"])
        al = sits.get("all")
        if al:
            xg += al["xgf"]; g += al["gf"]
    lg = {"ev60": statistics.mean(ev), "pp_xgf60": statistics.mean(pp), "pk_xga60": statistics.mean(pk),
          "pk_min": statistics.mean(pk_min), "pp_xga60": statistics.mean(pp_xga), "goals_per_xg": 1.0}
    # level anchor: scale so an average-vs-average game produces last season's real league scoring per team
    # (MoneyPuck 5v5/PP/PK xG leaves out empty-net, 3-on-3 and other situations)
    gf_pg = [s["all"]["gf"] / s["all"]["gp"] for (t_, season), s in tm.items() if season == PREV_SEASON and s.get("all")]
    pp_min = statistics.mean([_rates(s, "5on4")["min"] for (t_, season), s in tm.items() if season == PREV_SEASON and _rates(s, "5on4")])
    modeled = ((60 - 2 * pp_min) / 60 * lg["ev60"] + pp_min / 60 * lg["pp_xgf60"]
               + pp_min / 60 * statistics.mean([_rates(s, "4on5")["xgf60"] for (t_, season), s in tm.items()
                                                 if season == PREV_SEASON and _rates(s, "4on5")]))
    lg["goals_per_xg"] = statistics.mean(gf_pg) / modeled
    return lg


def outcome_probs(lamA, lamB, total_lines):
    """P(A wins incl. OT/SO), plus a function P(over line) that accounts for overtime goals."""
    pa, pb = pois(lamA), pois(lamB)
    win = sum(pa[a] * pb[b] for a in range(MAXG) for b in range(a))
    lose = sum(pa[a] * pb[b] for a in range(MAXG) for b in range(a + 1, MAXG))
    tie = sum(pa[k] * pb[k] for k in range(MAXG))
    share = lamA / (lamA + lamB)
    ot_a = 0.5 + GAME["ot_skill"] * (share - 0.5)
    p_a = win + tie * ot_a
    # total goals pmf: regulation sum, plus one goal when a tied game is decided by an overtime goal
    pmf = [0.0] * (2 * MAXG + 2)
    for a in range(MAXG):
        for b in range(MAXG):
            pr = pa[a] * pb[b]
            if a == b:
                pmf[a + b] += pr * (1 - GAME["ot_goal"])
                pmf[a + b + 1] += pr * GAME["ot_goal"]
            else:
                pmf[a + b] += pr
    s = sum(pmf)
    pmf = [x / s for x in pmf]

    def over(line):
        o = sum(v for k, v in enumerate(pmf) if k > line)
        u = sum(v for k, v in enumerate(pmf) if k < line)
        return o, u, 1 - o - u
    return p_a, over, {"win": win, "tie": tie, "lose": lose}


def _label(ev):
    return "BET" if ev >= GAME["bet_ev"] else "LEAN" if ev >= GAME["lean_ev"] else "PASS"


def safest_pick(rows):
    """The single most likely winning selection (moneyline side or total side, any of the five books) that pays -142 or better.
    Used when no game offers a +EV bet. Ranked by our final win probability."""
    floor = GAME["safe_min_price"]
    cands = []
    for g in rows:
        if g["ml"]:
            for c in g["ml"]["cands"]:
                cands.append({**c, "game": f"{g['away']} @ {g['home']}", "eid": g["eid"], "market_name": "Moneyline", "label_sel": f"{c['team']} ML"})
        if g["total"]:
            for c in g["total"]["_all"]:
                cands.append({**c, "game": f"{g['away']} @ {g['home']}", "eid": g["eid"], "market_name": "Total", "label_sel": f"{c['side']} {c['line']:g}"})
    ok = [c for c in cands if c["price"] >= floor]
    if not ok:
        return None
    return max(ok, key=lambda c: (c["p"], c["ev"]))


def build_game_rows(con, date):
    games = con.execute("SELECT * FROM game WHERE date=? ORDER BY start_utc, event_id", (date,)).fetchall()
    tm = load_team_mp(con)
    if not tm:
        return []
    lg = league_constants(tm)
    b2b = b2b_teams(date)
    nhl_prev = {r["teamFullName"]: r for r in nhl_api.teams(PREV_SEASON, False)}
    nhl_cur = {r["teamFullName"]: r for r in nhl_api.teams(CUR_SEASON, False)}
    abbr = nhl_api.team_abbrevs(force=False)
    nhl_by_ab_prev = {abbr[n]: r for n, r in nhl_prev.items() if n in abbr}
    nhl_by_ab_cur = {abbr[n]: r for n, r in nhl_cur.items() if n in abbr}

    out = []
    for g in games:
        eid, home, away = g["event_id"], g["home"], g["away"]
        books = con.execute("SELECT * FROM odds_game WHERE event_id=?", (eid,)).fetchall()
        ref = next((b for b in books if b["book"] == MARKET_BOOK), books[0] if books else None)
        gp = {r["team"]: r for r in con.execute("SELECT * FROM goalie_proj WHERE event_id=?", (eid,))}
        gh, ga_ = gp.get(home), gp.get(away)
        # the home team's goals are scored against the AWAY goalie (and vice versa)
        h_calc = expected_goals(tm, home, away, con, ga_["goalie_id"] if ga_ else None, ga_["status"] if ga_ else None,
                                "home", home in b2b, away in b2b, lg)
        a_calc = expected_goals(tm, away, home, con, gh["goalie_id"] if gh else None, gh["status"] if gh else None,
                                "away", away in b2b, home in b2b, lg)
        if not (h_calc and a_calc):
            continue
        lamH, lamA = h_calc["lam"], a_calc["lam"]
        p_home_model, over_fn, reg = outcome_probs(lamH, lamA, None)
        p_away_model = 1 - p_home_model

        # ---------- moneyline ----------
        ml_rows = [b for b in books if b["ml_home"] is not None and b["ml_away"] is not None]
        ml = None
        if ml_rows:
            fairs = [_fair2(b["ml_home"], b["ml_away"]) for b in ml_rows]
            fair_h = statistics.median(fairs)
            pH = sigmoid(GAME["market_w"] * logit(fair_h) + (1 - GAME["market_w"]) * logit(p_home_model))
            best_h = max(ml_rows, key=lambda b: b["ml_home"])
            best_a = max(ml_rows, key=lambda b: b["ml_away"])
            sel = [("home", home, pH, best_h["ml_home"], best_h["book"]), ("away", away, 1 - pH, best_a["ml_away"], best_a["book"])]
            cand = [{"side": s, "team": t, "p": p, "price": pr, "book": bk, "ev": _ev(p, pr), "market": (fair_h if s == "home" else 1 - fair_h)}
                    for s, t, p, pr, bk in sel]
            best = max(cand, key=lambda c: c["ev"])
            ml = {"home_price": ref["ml_home"] if ref and ref["ml_home"] is not None else None,
                  "away_price": ref["ml_away"] if ref and ref["ml_away"] is not None else None,
                  "fair_home": fair_h, "model_home": p_home_model, "final_home": pH, "cands": cand, "best": best,
                  "label": _label(best["ev"])}

        # ---------- total ----------
        tot = None
        t_rows = [b for b in books if b["total"] is not None and b["over_price"] is not None and b["under_price"] is not None]
        if t_rows:
            cands = []
            for b in t_rows:
                o, u, push = over_fn(b["total"])
                m_over = o / (o + u) if (o + u) else 0.5
                fair_o = _fair2(b["over_price"], b["under_price"])
                p_over = sigmoid(GAME["market_w"] * logit(fair_o) + (1 - GAME["market_w"]) * logit(m_over))
                p_o, p_u = p_over * (1 - push), (1 - p_over) * (1 - push)
                for side, p_win, p_lose, price in (("Over", p_o, p_u, b["over_price"]), ("Under", p_u, p_o, b["under_price"])):
                    cands.append({"side": side, "line": b["total"], "price": price, "book": b["book"], "p": p_win,
                                  "ev": p_win * (_dec(price) - 1) - p_lose, "market": fair_o if side == "Over" else 1 - fair_o,
                                  "model": m_over if side == "Over" else 1 - m_over})
            best = max(cands, key=lambda c: c["ev"])
            ref_t = ref if ref and ref["total"] is not None else t_rows[0]
            o_ref, u_ref, _ = over_fn(ref_t["total"])
            tot = {"_all": cands, "line": ref_t["total"], "over_price": ref_t["over_price"], "under_price": ref_t["under_price"],
                   "model_over": o_ref / (o_ref + u_ref), "model_total": lamH + lamA, "best": best, "label": _label(best["ev"]),
                   "fair_over": _fair2(ref_t["over_price"], ref_t["under_price"])}

        def stats(team):
            sp, sc = tm.get((team, PREV_SEASON), {}), tm.get((team, CUR_SEASON), {})
            def pct(sits):
                a = sits.get("all")
                return a["xgf"] / (a["xgf"] + a["xga"]) * 100 if a and (a["xgf"] + a["xga"]) else None
            def per_game(sits, k):
                a = sits.get("all")
                return a[k] / a["gp"] if a and a["gp"] else None
            def ev_pct(sits):
                a = sits.get("5on5")
                return a["xgf"] / (a["xgf"] + a["xga"]) * 100 if a and (a["xgf"] + a["xga"]) else None
            npv, ncu = nhl_by_ab_prev.get(team), nhl_by_ab_cur.get(team)
            return {"xgp": pct(sp), "xgc": pct(sc), "ev_xgp": ev_pct(sp), "ev_xgc": ev_pct(sc),
                    "gf_p": per_game(sp, "gf"), "gf_c": per_game(sc, "gf"), "ga_p": per_game(sp, "ga"), "ga_c": per_game(sc, "ga"),
                    "sf_p": per_game(sp, "sf"), "sf_c": per_game(sc, "sf"), "sa_p": per_game(sp, "sa"), "sa_c": per_game(sc, "sa"),
                    "pp_p": npv["powerPlayPct"] * 100 if npv and npv.get("powerPlayPct") is not None else None,
                    "pp_c": ncu["powerPlayPct"] * 100 if ncu and ncu.get("powerPlayPct") is not None else None,
                    "pk_p": npv["penaltyKillPct"] * 100 if npv and npv.get("penaltyKillPct") is not None else None,
                    "pk_c": ncu["penaltyKillPct"] * 100 if ncu and ncu.get("penaltyKillPct") is not None else None,
                    "gp_c": (sc.get("all") or {}).get("gp", 0)}

        out.append({"eid": eid, "start": g["start_utc"], "home": home, "away": away, "lamH": lamH, "lamA": lamA,
                    "h": h_calc, "a": a_calc, "p_home_model": p_home_model, "reg": reg, "ml": ml, "total": tot,
                    "b2b": {"home": home in b2b, "away": away in b2b},
                    "goalie_home": gh, "goalie_away": ga_, "stats": {"home": stats(home), "away": stats(away)},
                    "books": [{"book": b["book"], "ml_home": b["ml_home"], "ml_away": b["ml_away"], "total": b["total"],
                               "over": b["over_price"], "under": b["under_price"]} for b in books]})
    return out
