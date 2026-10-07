"""Feature assembly + transparent baseline P(goal). Weights live in config.W (priors, not fitted)."""
import csv
import math
import statistics
from .config import PREV_SEASON, CUR_SEASON, MARKET_BOOK, POOLS_CSV, W
from .normalize import norm_name
from .odds_math import implied_team_totals


def logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1 / (1 + math.exp(-x))


def load_pools(con, date):
    """pools.csv -> {nhl_id: pool}. Names matched on normalized name (+team if given)."""
    if not POOLS_CSV.exists():
        return None
    out, missing = {}, []
    with open(POOLS_CSV, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            n = norm_name(r["player"])
            team = (r.get("team") or "").strip().upper()
            rows = con.execute("SELECT nhl_id FROM player WHERE name_norm=?" + (" AND team=?" if team else ""),
                               (n, team) if team else (n,)).fetchall()
            if len(rows) != 1 and team:  # nickname variants: same last name on that team
                rows = con.execute("SELECT nhl_id FROM player WHERE team=? AND name_norm LIKE ?",
                                   (team, "% " + n.split()[-1])).fetchall()
            if len(rows) == 1:
                out[rows[0]["nhl_id"]] = int(r["pool"])
            else:
                missing.append((r["player"], len(rows)))
    return out, missing


def _goalie_block(con, gid):
    if gid is None:
        return None
    p = con.execute("SELECT * FROM goalie_season WHERE nhl_id=? AND season=?", (gid, PREV_SEASON)).fetchone()
    c = con.execute("SELECT * FROM goalie_season WHERE nhl_id=? AND season=?", (gid, CUR_SEASON)).fetchone()
    return {"prev": p, "cur": c}


def _goalie_sv_eff(gb):
    """Current SV% shrunk toward prev season (or league avg) by sample size."""
    if not gb:
        return None
    prior = gb["prev"]["sv_pct"] if gb["prev"] and gb["prev"]["gp"] >= 10 else W["league_sv"]
    c = gb["cur"]
    if not c or not c["gp"]:
        return prior
    w = c["gp"] / (c["gp"] + W["goalie_gp_k"])
    return w * c["sv_pct"] + (1 - w) * prior


def build_rows(con, date):
    games = {g["event_id"]: g for g in con.execute("SELECT * FROM game WHERE date=?", (date,))}
    pools = load_pools(con, date)
    pool_map, missing = (pools if pools else (None, []))

    # game odds: prefer MARKET_BOOK, else any book
    godds = {}
    for eid in games:
        rows = con.execute("SELECT * FROM odds_game WHERE event_id=?", (eid,)).fetchall()
        pick = next((r for r in rows if r["book"] == MARKET_BOOK), rows[0] if rows else None)
        if pick and pick["ml_home"] is not None and pick["ml_away"] is not None:
            hx, ax = implied_team_totals(pick["total"], pick["over_price"], pick["under_price"],
                                         pick["ml_home"], pick["ml_away"])
        else:
            hx = ax = None
        godds[eid] = {"row": pick, "xg": {games[eid]["home"]: hx, games[eid]["away"]: ax}}

    gproj = {(r["event_id"], r["team"]): r for r in con.execute("SELECT * FROM goalie_proj")}
    teams = {r["team"]: r for r in con.execute("SELECT * FROM team_season WHERE season=?", (PREV_SEASON,))}

    # candidate players: pool members, else every player with anytime odds
    atg = {}
    for r in con.execute("SELECT * FROM odds_atg WHERE nhl_id IS NOT NULL"):
        atg.setdefault((r["event_id"], r["nhl_id"]), []).append(r)
    cands = []
    if pool_map is not None:
        for pid, pool in pool_map.items():
            p = con.execute("SELECT * FROM player WHERE nhl_id=?", (pid,)).fetchone()
            for eid, g in games.items():
                if p["team"] in (g["home"], g["away"]):
                    cands.append((eid, pid, pool))
    else:
        for (eid, pid) in atg:
            cands.append((eid, pid, 0))

    out = []
    for eid, pid, pool in cands:
        g = games[eid]
        p = con.execute("SELECT * FROM player WHERE nhl_id=?", (pid,)).fetchone()
        team = p["team"]
        opp = g["away"] if team == g["home"] else g["home"]
        prev = con.execute("SELECT * FROM player_season WHERE nhl_id=? AND season=?", (pid, PREV_SEASON)).fetchone()
        cur = con.execute("SELECT * FROM player_season WHERE nhl_id=? AND season=?", (pid, CUR_SEASON)).fetchone()
        xgp = con.execute("SELECT gp, xg FROM player_xg WHERE nhl_id=? AND season=?", (pid, PREV_SEASON)).fetchone()
        xgc = con.execute("SELECT gp, xg FROM player_xg WHERE nhl_id=? AND season=?", (pid, CUR_SEASON)).fetchone()
        pp = con.execute("SELECT unit FROM pp_unit WHERE nhl_id=?", (pid,)).fetchone()
        pp_unit = pp["unit"] if pp else 0
        books = atg.get((eid, pid), [])
        fd = next((b for b in books if b["book"] == MARKET_BOOK), None)
        cons = statistics.median([b["implied"] for b in books]) if books else None
        best = max((b["american"] for b in books), default=None)
        xg = godds[eid]["xg"].get(team)
        gp_row = gproj.get((eid, opp))
        gb = _goalie_block(con, gp_row["goalie_id"] if gp_row else None)
        opp_t = teams.get(opp)

        # --- fundamentals ---
        prior_pct = W["league_goal_game"]
        if prev and prev["gp"]:
            pg = W["prior_games"]
            prior_pct = (prev["goal_game_pct"] * prev["gp"] + W["league_goal_game"] * pg) / (prev["gp"] + pg)
        if xgp and xgp["gp"] >= 20:  # individual xG is less noisy than goals: blend its Poisson P(>=1)
            pg = W["prior_games"]
            p_xg = 1 - math.exp(-xgp["xg"] / xgp["gp"])
            p_xg = (p_xg * xgp["gp"] + W["league_goal_game"] * pg) / (xgp["gp"] + pg)
            prior_pct = (1 - W["xg_weight"]) * prior_pct + W["xg_weight"] * p_xg
        rate = prior_pct
        if cur and cur["gp"]:
            est = min(cur["g"], cur["gp"]) * 0.92  # goals -> approx goal-games
            rate = (prior_pct * W["cur_games_k"] + est) / (W["cur_games_k"] + cur["gp"])
        parts = {}
        z = logit(rate)
        if xg is not None:
            parts["team total"] = W["team_xg"] * (xg - 3.0)
        parts["power play"] = {1: W["pp1"], 2: W["pp2"]}.get(pp_unit, 0.0)
        if opp_t:
            parts["opp GA rank"] = W["opp_ga_rank"] * (opp_t["ga_rank"] - 16.5)
        sv = _goalie_sv_eff(gb) if gp_row and gp_row["status"] != "Unknown" else None
        if sv is not None:
            parts["goalie"] = -W["goalie_sv"] * (sv - W["league_sv"])
        z_fund = z + sum(parts.values())
        p_fund = sigmoid(z_fund)

        if cons is not None:
            p_mkt = cons * W["vig_factor"]
            pm = sigmoid(W["market_weight"] * logit(p_mkt) + (1 - W["market_weight"]) * z_fund)
        else:
            p_mkt, pm = None, p_fund

        # --- reasons: (sort weight, text, class p=positive / z=neutral / n=negative) ---
        def cls(x, band):
            return "p" if x > band else "n" if x < -band else "z"
        reasons = []
        if p_mkt is not None:
            m = "p" if p_mkt > 0.3 else "n" if p_mkt < 0.2 else "z"
            reasons.append((logit(p_mkt) - logit(W["league_goal_game"]), m,
                            ("Strong" if m == "p" else "Weak" if m == "n" else "Average") + " anytime-goal market", "mkt"))
        else:
            reasons.append((-0.4, "z", "No anytime-goal odds listed (fundamentals only)", "nomkt"))
        if prev and prev["gp"]:
            d = logit(prev["goal_game_pct"]) - logit(W["league_goal_game"])
            word = "High" if d > 0.15 else "Low" if d < -0.15 else "Average"
            reasons.append((d, cls(d, 0.15), f"{word} goal-game rate ({prev['goal_game_pct']*100:.0f}%)", "gg"))
        if pp_unit:
            reasons.append((parts["power play"] * 3, "p" if pp_unit == 1 else "z", f"PP{pp_unit} role", "pp"))
        else:
            reasons.append((-0.3, "n", "No PP unit", "pp"))
        if xg is not None:
            d = parts["team total"] * 2
            word = "High" if d > 0.12 else "Low" if d < -0.12 else "Average"
            reasons.append((d, cls(d, 0.12), f"{word} team implied total ({xg:.2f})", "xg"))
        if opp_t:
            rkv = opp_t["ga_rank"]
            c = "n" if rkv <= 10 else "p" if rkv >= 21 else "z"
            reasons.append(((rkv - 16.5) / 16.5, c,
                            f"{'Favorable' if c == 'p' else 'Tough' if c == 'n' else 'Average'} opponent GA/G (#{rkv})", "opp"))
        if sv is not None:
            d = parts["goalie"] * 2
            word = "Weak" if d > 0.1 else "Strong" if d < -0.1 else "Average"
            reasons.append((d, cls(d, 0.1), f"{word} opposing goalie ({gp_row['goalie_name']})", "gk"))
        reasons.sort(key=lambda r: -abs(r[0]))

        prev_team_changed = bool(prev and prev["team"] != team)
        out.append({
            "pool": pool, "event_id": eid, "start": g["start_utc"], "away": g["away"], "home": g["home"],
            "nhl_id": pid, "name": p["name"], "team": team, "opp": opp, "pos": p["pos"],
            "team_changed": prev_team_changed, "prev": prev, "cur": cur, "pp_unit": pp_unit,
            "game_odds": godds[eid]["row"], "xg": xg, "opp_team": opp_t,
            "goalie_proj": gp_row, "goalie": gb,
            "atg_display": fd or (books[0] if books else None), "atg_cons": cons, "atg_best": best,
            "p_fund": p_fund, "p_mkt": p_mkt, "p": pm, "all_reasons": reasons, "xg_prev": xgp, "xg_cur": xgc,
        })
    # rank within pool
    byp = {}
    for r in out:
        byp.setdefault(r["pool"], []).append(r)
    for rows in byp.values():
        rows.sort(key=lambda r: -r["p"])
        for i, r in enumerate(rows):
            r["rank"] = i + 1
            r["pool_n"] = len(rows)
        _relabel_relative(rows)
    for r in out:
        rs = sorted(r.pop("all_reasons"), key=lambda x: -abs(x[0]))
        r["reasons"] = [x[2] for x in rs[:5]]
        r["reasons_signed"] = [(x[1], x[2]) for x in rs[:5]]
    return out, missing


def _tier(value, values):
    """terciles within the pool: 'p' top third, 'n' bottom third, else 'z'."""
    srt = sorted(values)
    lo, hi = srt[len(srt) // 3], srt[(2 * len(srt)) // 3]
    return "p" if value >= hi and hi > lo else "n" if value <= lo and hi > lo else "z"


def _relabel_relative(rows):
    """Market and goal-game chips compare players with their own pool, not with league average."""
    mk = [r["p_mkt"] for r in rows if r["p_mkt"] is not None]
    gg = [r["prev"]["goal_game_pct"] for r in rows if r["prev"] and r["prev"]["gp"]]
    for r in rows:
        new = []
        for w, c, t, k in r["all_reasons"]:
            if k == "mkt" and len(mk) >= 3:
                c = _tier(r["p_mkt"], mk)
                t = ("Strong" if c == "p" else "Weak" if c == "n" else "Average") + " anytime-goal market"
            elif k == "gg" and len(gg) >= 3:
                c = _tier(r["prev"]["goal_game_pct"], gg)
                t = f"{'High' if c == 'p' else 'Low' if c == 'n' else 'Average'} goal-game rate ({r['prev']['goal_game_pct']*100:.0f}%)"
            new.append((w, c, t, k))
        r["all_reasons"] = new
