import argparse
import datetime as dt
import sys
import zoneinfo

from .history import update_history, validate_history


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="nhl_tim")
    ap.add_argument("cmd", choices=["update_history", "validate", "update_power_play_units",
                                    "update_daily", "build_table", "build_site", "settle", "results"])
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (ET); default today")
    ap.add_argument("--force", action="store_true", help="refetch cached history")
    a = ap.parse_args()
    date = a.date or dt.datetime.now(zoneinfo.ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    if a.cmd == "update_power_play_units":
        from .powerplay import update_power_play_units
        res = update_power_play_units()
        n, nt, un = res[:3]
        print(f"{n} PP entries, {nt} teams, {len(un)} unmatched: {un}" + (f", {res[3]} changes" if len(res) > 3 else ""))
        return
    if a.cmd == "update_daily":
        from .daily import update_daily
        s, n, um = update_daily(date)
        from .db import connect as _c
        from .tracking import settle as _settle
        print('settled', _settle(_c()), 'earlier predictions')
        print(f"{date}: games={s['games']} atg_rows={s['atg']} atg_unmatched={len(s['atg_unmatched'])} goalie_rows={n}")
        print("atg unmatched:", sorted(set(s["atg_unmatched"]))[:40])
        print("goalie unmatched:", um)
        return
    if a.cmd == "build_site":
        from .output.site import build_site
        print("%s (%d rows)" % build_site(date))
        from .db import connect as _c
        from .tracking import snapshot as _snap
        print("logged", _snap(_c(), date), "predictions for", date)
        return
    if a.cmd == "settle":
        from .db import connect as _c
        from .tracking import settle as _settle
        print("settled", _settle(_c()))
        return
    if a.cmd == "results":
        import json
        from .db import connect as _c
        from .tracking import results as _res
        print(json.dumps(_res(_c()), indent=1))
        return
    if a.cmd == "build_table":
        from .output.table import build_table
        print(build_table(date))
        return
    if a.cmd == "update_history":
        con = update_history(force=a.force)
    else:
        from .db import connect
        con = connect()
    errs = validate_history(con)
    print("OK" if not errs else "\n".join(errs[:50]))
    for tbl in ("player", "player_season", "team_season", "goalie_season"):
        print(tbl, con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0])


if __name__ == "__main__":
    main()
