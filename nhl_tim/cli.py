import argparse
from .history import update_history, validate_history

def main():
    import sys; sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="nhl_tim")
    ap.add_argument("cmd", choices=["update_history", "validate", "update_power_play_units"])
    ap.add_argument("--force", action="store_true", help="refetch cached history")
    a = ap.parse_args()
    if a.cmd == "update_power_play_units":
        from .powerplay import update_power_play_units
        n, nt, un = update_power_play_units()
        print(f"{n} PP entries, {nt} teams, {len(un)} unmatched: {un}")
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
