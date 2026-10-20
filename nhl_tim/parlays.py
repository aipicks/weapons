"""Daily two-leg shots-on-goal parlays.

Each leg is a main or alternate shots line (2+ shot overs, or unders) from FanDuel, DraftKings, BetMGM or Caesars, priced between
about -350 and -160, that the player has hit in at least 4 of his last 5 games and that our model puts at 60% or better.
Two legs from different games are paired so the parlay pays between about -120 and +112. Between 2 and 5 parlays are
shown, depending on the size of the slate and on how many pairs pass, best expected value first, no player twice.

Only games that start at or after config.MIN_START_HOUR_ET and have not started are used."""
import datetime as dt
import itertools

from .config import MIN_START_HOUR_ET, SOG
from .model import logit, sigmoid
from .sog import build_sog_rows, over_under_push

DEC_LO, DEC_HI = 1.28, 1.62        # leg price window: -350 .. -160
PAR_LO, PAR_HI = 1.83, 2.13        # parlay price window: -120 .. +113
MIN_P, MIN_HIT = 0.60, 4


def _dec(american):
    return 1 + (american / 100 if american > 0 else 100 / -american)


def american(d):
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def _count(n_games):
    return 2 if n_games <= 3 else 3 if n_games <= 6 else 4 if n_games <= 9 else 5


def _why(r, side, ln, p, hit, opp_goalie):
    """Plain-language reasons this leg should win, then the main risks."""
    over = side == "Over"
    l5 = r["last5"]
    why, risk = [], []
    word = f"{'over' if over else 'under'} {ln:g}"
    why.append(f"Hit {word} in {hit} of his last 5 games ({', '.join(str(x) for x in l5)} shots).")
    mu = r["mu"]
    why.append(f"Our projection is {mu:.1f} shots, {'above' if (mu > ln) == over else 'below'} the {ln:g} line"
               f" (the market implies {r['mu_market']:.1f}).")
    a = r.get("att")
    if a:
        why.append(f"Shot attempts: {a['rate']:.1f} a game this season at {a['ot']*100:.0f}% on target = {a['mu']:.1f} shots;"
                   f" last 5 games {', '.join(str(x) for x in r['att5'])}.")
    oc, pos = r.get("opp_cur"), r.get("opp_pos_cur")
    grp = "forwards" if r["pos_grp"] == "F" else "defensemen"
    if pos:
        k = "sa_f_pg" if r["pos_grp"] == "F" else "sa_d_pg"
        rk = pos["f_rank"] if r["pos_grp"] == "F" else pos["d_rank"]
        why.append(f"{r['opp']} allows {pos[k]:.1f} shots a game to {grp} this season (#{rk} of 32, 1 = fewest).")
    d = r.get("opp_def_cur")
    if d:
        why.append(f"{r['opp']}'s current defence ranks #{d['rank']} of 32 ({d['sa']:.1f} shots against a game).")
    toi = r.get("toi_cur") or r.get("toi_prev")
    role = []
    if toi:
        role.append(f"{toi/60:.0f} minutes a game")
    role.append("PP1" if r["pp_unit"] == 1 else "PP2" if r["pp_unit"] == 2 else "no power-play unit")
    why.append("Role: " + ", ".join(role) + ".")
    if r.get("sog_cur") is not None and r.get("sog_prev") is not None and r["gp_cur"] >= 5:
        why.append(f"This season {r['sog_cur']:.1f} shots a game, last season {r['sog_prev']:.1f}.")
    if opp_goalie:
        why.append(f"{r['opp']} goalie: {opp_goalie[0]} ({opp_goalie[1].lower()}).")
    # risks
    misses = [x for x in l5 if (x <= ln if over else x > ln)]
    if misses:
        risk.append(f"Missed this line in {len(misses)} of his last 5 ({', '.join(str(x) for x in misses)} shots).")
    if opp_goalie and opp_goalie[1] != "Confirmed":
        risk.append(f"{opp_goalie[0]} is not confirmed in net yet; recheck before the game.")
    risk.append(f"Our chance for this leg is {p*100:.0f}%, so about {100-p*100:.0f}% of the time it loses.")
    return why, risk


def build_parlays(con, date):
    now = dt.datetime.now(dt.timezone.utc)
    et = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")) - dt.timedelta(hours=4)
    rows = {}
    for r in build_sog_rows(con, date):
        st = dt.datetime.fromisoformat(r["start"].replace("Z", "+00:00"))
        if st <= now or et(r["start"]).hour < MIN_START_HOUR_ET:
            continue
        if (r["gp_cur"] or 0) + (r["gp_prev"] or 0) < 3:
            continue
        rows[r["nhl_id"]] = r
    if not rows:
        return {"parlays": [], "n_games": 0, "n_legs": 0}
    n_games = len({r["event_id"] for r in rows.values()})
    goalies = {}
    for g, t, n, s in con.execute("SELECT g.away||' @ '||g.home, p.team, p.goalie_name, p.status FROM goalie_proj p "
                                  "JOIN game g ON g.event_id=p.event_id WHERE g.date=?", (date,)):
        goalies[(g, t)] = (n, s)

    best = {}
    for a in con.execute("SELECT nhl_id, book, side, line, price FROM odds_sog_alt WHERE event_id IN (%s)" %
                         ",".join("?" * len({r["event_id"] for r in rows.values()})), list({r["event_id"] for r in rows.values()})):
        if a["nhl_id"] in rows:
            k = (a["nhl_id"], a["side"], a["line"])
            best.setdefault(k, []).append((a["price"], a["book"]))
    legs = []
    for (pid, side, ln), plist in best.items():
        plist = sorted(plist, reverse=True)  # best price first
        price, book = plist[0]
        r = rows[pid]
        d = _dec(price)
        # the user's style: 2+ shots overs (over 1.5), or unders on 1.5 / 2.5 / 3.5. No 1+ shot (0.5) overs, no 3+ shot overs.
        if not ((side == "Over" and ln == 1.5) or (side == "Under" and ln in (1.5, 2.5, 3.5))):
            continue
        if not DEC_LO <= d <= DEC_HI:
            continue
        mo = over_under_push(r["mu"], ln, SOG["nb_r"])
        mk = over_under_push(r["mu_market"], ln, SOG["nb_r"])
        mod_over, mkt_over = mo[0] / (mo[0] + mo[1]), mk[0] / (mk[0] + mk[1])
        p_over = sigmoid(0.5 * logit(mkt_over) + 0.5 * logit(mod_over))
        push = mo[2]
        p = (p_over if side == "Over" else 1 - p_over) * (1 - push)
        hit = sum(1 for s in r["last5"] if (s > ln if side == "Over" else s < ln))
        if p < MIN_P or hit < MIN_HIT:
            continue
        game = f"{r['away']} @ {r['home']}"
        legs.append({"pid": pid, "r": r, "side": side, "line": ln, "price": price, "book": book, "p": p, "d": d,
                     "hit": hit, "game": game, "goalie": goalies.get((game, r["opp"])), "books": plist})
    pairs = []
    for a, b in itertools.combinations(legs, 2):
        if a["game"] == b["game"] or a["pid"] == b["pid"]:
            continue
        d = a["d"] * b["d"]
        if PAR_LO <= d <= PAR_HI:
            p = a["p"] * b["p"]
            pairs.append((p * d - 1, p, d, a, b))
    pairs.sort(key=lambda x: -x[0])
    chosen, used = [], set()
    for ev, p, d, a, b in pairs:
        if a["pid"] in used or b["pid"] in used:
            continue
        chosen.append((ev, p, d, a, b))
        used.update((a["pid"], b["pid"]))
        if len(chosen) >= _count(n_games):
            break
    out = []
    for i, (ev, p, d, a, b) in enumerate(chosen, 1):
        legs_out = []
        for x in (a, b):
            r = x["r"]
            why, risk = _why(r, x["side"], x["line"], x["p"], x["hit"], x["goalie"])
            t = et(r["start"])
            legs_out.append({"pid": x["pid"], "name": r["name"], "team": r["team"], "opp": r["opp"], "game": x["game"],
                             "time": t.strftime("%I:%M %p").lstrip("0"), "side": x["side"], "line": x["line"],
                             "price": x["price"], "book": x["book"], "books": [{"book": b, "price": pr} for pr, b in x["books"]],
                             "p": round(x["p"] * 100), "hit": x["hit"],
                             "last5": r["last5"], "why": why, "risk": risk, "start": r["start"]})
        out.append({"n": i, "price": american(d), "p": round(p * 100), "ev": round(ev * 100, 1), "legs": legs_out})
    return {"parlays": out, "n_games": n_games, "n_legs": len(legs)}
