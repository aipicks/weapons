"""Player shots-on-goal over/under model.

Projects each lined player's shots for tonight, turns that into P(over)/P(under) at the sportsbook line with a
negative-binomial distribution, blends with the de-vigged market, and reports the best side with its EV.
Weights are starting priors (config.SOG), not fitted."""
import math
import statistics

from .config import CUR_SEASON, MARKET_BOOK, PREV_SEASON, SOG
from .model import logit, sigmoid
from .odds_math import american_to_prob, implied_team_totals
from .sources import nhl_api


def nb_pmf(k, mu, r):
    """Negative binomial with mean mu and variance mu + mu^2/r."""
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    p = r / (r + mu)
    return math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))


def over_under_push(mu, line, r):
    """(P(over), P(under), P(push)) for a sportsbook line."""
    top = int(mu * 6 + 25)
    pm = [nb_pmf(k, mu, r) for k in range(top)]
    over = sum(v for k, v in enumerate(pm) if k > line)
    under = sum(v for k, v in enumerate(pm) if k < line)
    push = sum(v for k, v in enumerate(pm) if k == line)
    s = over + under + push
    return over / s, under / s, push / s


def _dec(american):
    return 1 + (american / 100 if american > 0 else 100 / -american)


def _ev(p_win, p_lose, american):
    """EV per $1 staked; a push refunds the stake."""
    return p_win * (_dec(american) - 1) - p_lose


def _toi_sec(s):
    try:
        m, sec = s.split(":")
        return int(m) * 60 + int(sec)
    except Exception:
        return None


def _series(pid):
    """Shots per game newest first: (this season, last season). Current season is refreshed every run."""
    cur = nhl_api.game_log_gt(pid, CUR_SEASON, 2, force=True)
    prev = nhl_api.game_log_gt(pid, PREV_SEASON, 2)
    return [g["shots"] for g in cur], [g["shots"] for g in prev]


def _one(con, sql, *a):
    r = con.execute(sql, a).fetchone()
    return r[0] if r else None


def _implied_mu(fair_over, line):
    """Mean shots whose negative-binomial P(over) equals the market's de-vigged P(over) at this line."""
    lo, hi = 0.05, 12.0
    for _ in range(45):
        mid = (lo + hi) / 2
        if over_under_push(mid, line, SOG["nb_r"])[0] < fair_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def build_sog_rows(con, date):
    games = {g["event_id"]: g for g in con.execute("SELECT * FROM game WHERE date=?", (date,))}
    lines = {}
    for r in con.execute("SELECT * FROM odds_sog WHERE nhl_id IS NOT NULL"):
        if r["event_id"] in games:
            lines.setdefault((r["event_id"], r["nhl_id"]), []).append(r)

    # shrinkage target: shots/game of regulars (lined players are regulars, so an all-player average is too low)
    pos_avg = {r["pos"]: r["v"] for r in con.execute(
        """SELECT p.pos pos, SUM(s.sog) * 1.0 / SUM(s.gp) v FROM player_season s JOIN player p USING(nhl_id)
           JOIN player_toi t ON t.nhl_id=s.nhl_id AND t.season=s.season
           WHERE s.season=? AND s.gp>=40 AND t.toi_pg>=(CASE p.pos WHEN 'D' THEN 1080 ELSE 840 END)
           GROUP BY p.pos""", (PREV_SEASON,))}
    teams_prev = {r["team"]: r for r in con.execute("SELECT * FROM team_season WHERE season=?", (PREV_SEASON,))}
    teams_cur = {r["team"]: r for r in con.execute("SELECT * FROM team_season WHERE season=?", (CUR_SEASON,))}
    sa_all = [t["sa_pg"] for t in teams_prev.values() if t["sa_pg"]]
    league_sa = sum(sa_all) / len(sa_all) if sa_all else 30.0
    pk_prev = [r[0] for r in con.execute("SELECT pk_toi_pg FROM team_pk_toi WHERE season=?", (PREV_SEASON,))]
    league_pk = sum(pk_prev) / len(pk_prev) if pk_prev else None

    team_xg = {}
    for eid, g in games.items():
        rows = con.execute("SELECT * FROM odds_game WHERE event_id=?", (eid,)).fetchall()
        pick = next((r for r in rows if r["book"] == MARKET_BOOK), rows[0] if rows else None)
        if pick and pick["ml_home"] is not None and pick["ml_away"] is not None:
            hx, ax = implied_team_totals(pick["total"], pick["over_price"], pick["under_price"], pick["ml_home"], pick["ml_away"])
            team_xg[(eid, g["home"])], team_xg[(eid, g["away"])] = hx, ax

    pre = []
    for (eid, pid), books in lines.items():
        g = games[eid]
        p = con.execute("SELECT * FROM player WHERE nhl_id=?", (pid,)).fetchone()
        team = p["team"]
        opp = g["away"] if team == g["home"] else g["home"]
        cur_s, prev_s = _series(pid)
        pos_base = pos_avg.get(p["pos"], 2.0)

        # --- projection ---
        k = SOG["prior_games"]
        prev_rate = (sum(prev_s) + pos_base * k) / (len(prev_s) + k)
        wc = len(cur_s) / (len(cur_s) + SOG["cur_k"])
        season_rate = (1 - wc) * prev_rate + wc * (sum(cur_s) / len(cur_s)) if cur_s else prev_rate
        recent = (cur_s + prev_s)[:10]
        form = sum(recent) / len(recent) if recent else season_rate
        mu0 = (1 - SOG["form_w"]) * season_rate + SOG["form_w"] * form

        oc, op = teams_cur.get(opp), teams_prev.get(opp)
        wo = (oc["gp"] / (oc["gp"] + SOG["opp_k"])) if oc and oc["sa_pg"] else 0.0
        sa_eff = (1 - wo) * op["sa_pg"] + wo * oc["sa_pg"] if op and op["sa_pg"] and wo else (op["sa_pg"] if op and op["sa_pg"] else league_sa)
        f_opp = (sa_eff / league_sa) ** SOG["opp_exp"]

        toi_p = _one(con, "SELECT toi_pg FROM player_toi WHERE nhl_id=? AND season=?", pid, PREV_SEASON)
        toi_c = _one(con, "SELECT toi_pg FROM player_toi WHERE nhl_id=? AND season=?", pid, CUR_SEASON)
        f_toi = (toi_c / toi_p) ** (SOG["toi_exp"] * wc) if toi_p and toi_c else 1.0
        xg_t = team_xg.get((eid, team))
        f_pace = (xg_t / 3.0) ** SOG["pace_exp"] if xg_t else 1.0
        pp_p = _one(con, "SELECT pp_toi_pg FROM player_pp_toi WHERE nhl_id=? AND season=?", pid, PREV_SEASON) or 0.0
        pk_p = _one(con, "SELECT pk_toi_pg FROM team_pk_toi WHERE team=? AND season=?", opp, PREV_SEASON)
        f_pp = 1 + SOG["pp_scale"] * (pp_p / 60) * ((pk_p / league_pk - 1) if pk_p and league_pk else 0)
        mu = max(0.05, mu0 * f_opp * f_toi * f_pace * f_pp)

        # market reference: FanDuel if listed, else the first book
        ref = next((b for b in books if b["book"] == MARKET_BOOK), books[0])
        o_im, u_im = american_to_prob(ref["over_price"]), american_to_prob(ref["under_price"])
        pre.append(dict(eid=eid, g=g, pid=pid, p=p, team=team, opp=opp, books=books, ref=ref, mu=mu,
                        fair_over=o_im / (o_im + u_im), cur_s=cur_s, prev_s=prev_s, recent=recent, form=form,
                        season_rate=season_rate, toi_c=toi_c, toi_p=toi_p, pp_p=pp_p, oc=oc, op=op, wo=wo,
                        sa_eff=sa_eff, xg_t=xg_t))

    # the model's own probabilities are compared with the market's as-is (no forced level match); the shots the
    # market implies at each line are kept for display and for monitoring bias
    for r_ in pre:
        r_["mu_market"] = _implied_mu(r_["fair_over"], r_["ref"]["line"])
    ratio = 1.0

    out = []
    for q in pre:
        eid, g, pid, p, team, opp, books, ref = (q[k] for k in ("eid", "g", "pid", "p", "team", "opp", "books", "ref"))
        cur_s, prev_s, recent, form, season_rate = q["cur_s"], q["prev_s"], q["recent"], q["form"], q["season_rate"]
        toi_c, toi_p, pp_p, oc, op, wo, sa_eff, xg_t = (q[k] for k in ("toi_c", "toi_p", "pp_p", "oc", "op", "wo", "sa_eff", "xg_t"))
        mu, fair_over = q["mu"] * ratio, q["fair_over"]
        line = ref["line"]
        m_over, m_under, m_push = over_under_push(mu, line, SOG["nb_r"])
        p_push = m_push
        mod_over = m_over / (m_over + m_under) if (m_over + m_under) else 0.5
        p_over_nopush = sigmoid(SOG["market_weight"] * logit(fair_over) + (1 - SOG["market_weight"]) * logit(mod_over))
        p_over, p_under = p_over_nopush * (1 - p_push), (1 - p_over_nopush) * (1 - p_push)
        ev_o, ev_u = _ev(p_over, p_under, ref["over_price"]), _ev(p_under, p_over, ref["under_price"])
        side, p_side, ev, price = ("Over", p_over, ev_o, ref["over_price"]) if ev_o >= ev_u else ("Under", p_under, ev_u, ref["under_price"])

        # --- hit rates at today's line ---
        def hit(ser):
            return (sum(1 for x in ser if x > line) / len(ser)) if ser else None
        all_s = cur_s + prev_s
        hit_l10, hit_season = hit(all_s[:10]), hit(all_s[:max(len(cur_s), 1) + len(prev_s)])

        # --- reasons ---
        reasons = []
        edge_mu = mu - line
        reasons.append((abs(edge_mu), "p" if (edge_mu > 0) == (side == "Over") else "n",
                        f"Projection {mu:.1f} vs line {line:g}"))
        if oc or op:
            rk_ = (oc["sa_rank"] if oc and oc["sa_rank"] and wo > 0.5 else op["sa_rank"]) if op else None
            if rk_:
                allow = "high" if rk_ >= 21 else "low" if rk_ <= 10 else "average"
                good_for_over = rk_ >= 21
                cls_ = "z" if allow == "average" else ("p" if (good_for_over == (side == "Over")) else "n")
                reasons.append((abs(rk_ - 16.5) / 8, cls_, f"Opponent allows {allow} shot volume (#{int(rk_)}, {sa_eff:.1f}/G)"))
        if recent:
            hot = form - season_rate
            if abs(hot) > 0.4:
                reasons.append((abs(hot), "p" if (hot > 0) == (side == "Over") else "n",
                                f"{'Hot' if hot > 0 else 'Cold'} recent form ({form:.1f}/G last {len(recent)})"))
        if toi_c or toi_p:
            toi = toi_c or toi_p
            reasons.append((0.3, "z", f"{toi/60:.1f} min TOI/G"))
        reasons.sort(key=lambda r: -r[0])

        out.append({
            "event_id": eid, "start": g["start_utc"], "away": g["away"], "home": g["home"], "nhl_id": pid,
            "name": p["name"], "team": team, "opp": opp, "pos": p["pos"], "side": side, "line": line,
            "price": price, "p": p_side, "ev": ev, "mu": mu, "p_over": p_over, "p_under": p_under,
            "over_price": ref["over_price"], "under_price": ref["under_price"], "fair_over": fair_over,
            "books": [{"book": b["book"], "line": b["line"], "o": b["over_price"], "u": b["under_price"]} for b in books],
            "sog_prev": sum(prev_s) / len(prev_s) if prev_s else None, "gp_prev": len(prev_s),
            "sog_cur": sum(cur_s) / len(cur_s) if cur_s else None, "gp_cur": len(cur_s),
            "form": form if recent else None, "n_form": len(recent), "last5": [x for x in (cur_s + prev_s)[:5]],
            "hit_l10": hit_l10, "hit_season": hit_season,
            "toi_prev": toi_p, "toi_cur": toi_c, "pp_prev": pp_p,
            "pp_cur": _one(con, "SELECT pp_toi_pg FROM player_pp_toi WHERE nhl_id=? AND season=?", pid, CUR_SEASON),
            "mu_market": q["mu_market"], "opp_prev": op, "opp_cur": oc, "sa_eff": sa_eff, "xg_t": xg_t,
            "pp_unit": _one(con, "SELECT unit FROM pp_unit WHERE nhl_id=?", pid) or 0,
            "reasons": [(c, t) for _, c, t in reasons[:5]],
        })
    out.sort(key=lambda r: -r["ev"])
    for i, r in enumerate(out):
        r["rank"], r["n"] = i + 1, len(out)
    return out
