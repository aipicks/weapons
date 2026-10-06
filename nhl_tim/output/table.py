"""Daily table: HTML (colour-coded) + CSV. Pools separate; within pool, games chronological, players grouped by game."""
import csv
import datetime as dt
import html
import zoneinfo

from ..config import DATA, PREV_SEASON, CUR_SEASON
from ..db import connect
from ..model import build_rows
from ..odds_math import fmt_american

ET = zoneinfo.ZoneInfo("America/New_York")
COLOR = {"red": "#f8b4b4", "gray": "#e5e7eb", "green": "#b7ebc6", None: "transparent"}


def _et(start_utc):
    d = dt.datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(ET)
    return d.strftime("%I:%M %p").lstrip("0"), d


def team_color(rank):
    return "red" if rank <= 10 else "gray" if rank <= 20 else "green"


def third_color(rank, n):
    if rank is None or not n:
        return None
    return "red" if rank <= n / 3 else "green" if rank > 2 * n / 3 else "gray"


def _stat(row, na="N/A"):
    return f"{row['gp']}GP — {row['g']}G — {row['sog']} SOG" if row else na


def _goalie_cell(con, gb, name, season_key, status):
    """returns list of (text, colour) segments"""
    if not name:
        return [("N/A", None)]
    s = gb[season_key] if gb else None
    tag = {"Confirmed": "", "Expected": " (exp)", "Unknown": " (unk)"}.get(status, "")
    if not s or not s["gp"]:
        return [(f"{name}{tag} — N/A", None)]
    n_g = con.execute("SELECT COUNT(gaa_rank) FROM goalie_season WHERE season=?", (s["season"],)).fetchone()[0]
    n_s = con.execute("SELECT COUNT(sv_rank) FROM goalie_season WHERE season=?", (s["season"],)).fetchone()[0]
    gr = f"#{s['gaa_rank']}" if s["gaa_rank"] else "unq"
    sr = f"#{s['sv_rank']}" if s["sv_rank"] else "unq"
    return [(f"{name}{tag} — {s['gaa']:.2f} GAA ", None),
            (f"({gr})", third_color(s["gaa_rank"], n_g)), (" / ", None),
            (f"{s['sv_pct']:.3f} SV% ".lstrip("0"), None), (f"({sr})", third_color(s["sv_rank"], n_s))]


def _odds_text(r):
    g = r["game_odds"]
    if not g:
        return "N/A"
    t = f"Total {g['total']:g} (O {fmt_american(g['over_price'])})"
    if g["ml_home"] is not None and g["ml_away"] is not None:
        home_fav = g["ml_home"] < g["ml_away"]
        fav, ml = (r["home"], g["ml_home"]) if home_fav else (r["away"], g["ml_away"])
        t += f" // {fav} {fmt_american(ml)}"
    return t


def _anytime_text(r):
    a = r["atg_display"]
    return "N/A" if not a else f"{fmt_american(a['american'])} / {a['implied']*100:.1f}%"


def build_table(date):
    con = connect()
    rows, missing = build_rows(con, date)
    last_pp = (con.execute("SELECT value FROM meta WHERE key='last_pp_update'").fetchone() or [None])[0]
    daily = (con.execute("SELECT value FROM meta WHERE key='daily_updated'").fetchone() or [None])[0]
    pools = sorted({r["pool"] for r in rows})
    esc = html.escape
    parts = [f"""<!doctype html><meta charset="utf-8"><title>NHL Tim Hortons {date}</title>
<style>body{{font:13px system-ui;margin:16px;background:#fff;color:#111}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:4px 6px;white-space:nowrap}}th{{background:#f3f4f6;position:sticky;top:0}}
tr.game td{{background:#1f2937;color:#fff;font-weight:600}}h2{{margin:24px 0 6px}}small{{color:#555}}</style>
<h1>NHL — Tim Hortons Hockey Challenge — {date}</h1>
<small>Daily data updated {esc(str(daily))} UTC · PP units last updated {esc(str(last_pp))} UTC ·
Red = tough matchup, green = favorable. Model weights are unfitted priors.</small>"""]
    if missing:
        parts.append(f"<p><b>Pool names not matched:</b> {esc(str(missing))}</p>")
    csv_rows = []
    for pool in pools:
        title = f"Pick #{pool}" if pool else "All priced players (no pools.csv found)"
        parts.append(f"<h2>{title}</h2><table><tr><th>Game / Time</th><th>Player / Team / Pos</th><th>2025–26</th>"
                     "<th>2026–27</th><th>PP Unit</th><th>Odds</th><th>Opp 25–26 GA/G</th><th>Goalie 25–26</th>"
                     "<th>Goalie 26–27</th><th>Anytime Goal</th><th>Goal-game % 25–26</th><th>Model P(Goal)</th>"
                     "<th>Rank</th><th>Why</th></tr>")
        prows = [r for r in rows if r["pool"] == pool]
        prows.sort(key=lambda r: (r["start"], r["event_id"], -r["p"]))
        last = None
        for r in prows:
            tm, _ = _et(r["start"])
            gtxt = f"{r['away']} @ {r['home']} — {tm}"
            if r["event_id"] != last:
                parts.append(f"<tr class='game'><td colspan='14'>{esc(gtxt)}</td></tr>")
                last = r["event_id"]
            nm = f"{r['name'].split()[-1] if ' ' in r['name'] else r['name']}{'*' if r['team_changed'] else ''} / {r['team']} / {r['pos']}"
            prev, cur = r["prev"], r["cur"]
            c26 = _stat(cur) if cur else "0GP — 0G — 0 SOG"
            ot = r["opp_team"]
            ga = f"{ot['ga_pg']:.2f} GA/G (#{ot['ga_rank']})" if ot else "N/A"
            gp = r["goalie_proj"]
            gname = gp["goalie_name"].split()[-1] if gp and gp["goalie_name"] else None
            gstat = gp["status"] if gp else "Unknown"
            gp_prev = _goalie_cell(con, r["goalie"], gname, "prev", gstat)
            gp_cur = _goalie_cell(con, r["goalie"], gname, "cur", gstat)
            pp = {1: "1st Unit ✅", 2: "2nd Unit ✅"}.get(r["pp_unit"], "NONE ❌")

            def seg(parts_):
                return "".join(f"<span style='background:{COLOR[c]}'>{esc(t)}</span>" for t, c in parts_)
            gg = f"{prev['goal_games']}/{prev['gp']} = {prev['goal_game_pct']*100:.1f}%" if prev else "N/A"
            why = "; ".join(r["reasons"])
            parts.append(
                f"<tr><td>{esc(gtxt)}</td><td>{esc(nm)}</td><td>{esc(_stat(prev))}</td><td>{esc(c26)}</td>"
                f"<td>{pp}</td><td>{esc(_odds_text(r))}</td>"
                f"<td style='background:{COLOR[team_color(ot['ga_rank']) if ot else None]}'>{esc(ga)}</td>"
                f"<td>{seg(gp_prev)}</td><td>{seg(gp_cur)}</td><td>{esc(_anytime_text(r))}</td><td>{gg}</td>"
                f"<td><b>{r['p']*100:.1f}%</b></td><td>#{r['rank']}/{r['pool_n']}</td><td>{esc(why)}</td></tr>")
            csv_rows.append([pool, gtxt, nm, _stat(prev), c26, pp, _odds_text(r), ga,
                             "".join(t for t, _ in gp_prev), "".join(t for t, _ in gp_cur),
                             _anytime_text(r), gg, f"{r['p']*100:.1f}%", r["rank"], why])
        parts.append("</table>")
    out = DATA / f"table_{date}"
    out.with_suffix(".html").write_text("".join(parts), encoding="utf-8")
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["pool", "game", "player", "2025-26", "2026-27", "pp", "odds", "opp_ga", "goalie_25_26",
                    "goalie_26_27", "anytime", "goal_game_rate", "model_p", "rank", "why"])
        w.writerows(csv_rows)
    return f"{len(rows)} rows -> {out.with_suffix('.html')}"
