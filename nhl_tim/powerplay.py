from .db import connect
from .normalize import norm_name, norm_team
from .sources import dailyfaceoff

def _ensure(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS pp_unit(
      nhl_id INTEGER PRIMARY KEY, team TEXT, name TEXT, unit INTEGER, source_updated TEXT);
    CREATE TABLE IF NOT EXISTS pp_history(
      fetched_at TEXT, nhl_id INTEGER, team TEXT, name TEXT, unit INTEGER);
    """)

def _match(con, team, name, pos=None):
    n = norm_name(name)
    for sql, args in (
        ("SELECT nhl_id,pos FROM player WHERE name_norm=? AND team=?", (n, team)),
        ("SELECT nhl_id,pos FROM player WHERE name_norm=?", (n,)),
        # nickname/first-name variants: same last name on same team
        ("SELECT nhl_id,pos FROM player WHERE team=? AND name_norm LIKE ?", (team, "% " + n.split()[-1])),
    ):
        rows = con.execute(sql, args).fetchall()
        if len(rows) > 1 and pos:
            rows = [r for r in rows if r["pos"] == pos]
        if len(rows) == 1:
            return rows[0]["nhl_id"]
    return None

def update_power_play_units():
    con = connect(); _ensure(con)
    rows = dailyfaceoff.fetch_all()
    for r in rows: r['team'] = norm_team(r['team'])
    teams = {r["team"] for r in rows}
    unmatched = []
    con.execute("DELETE FROM pp_unit")
    for r in rows:
        pid = _match(con, r["team"], r["name"], r["pos"])
        if pid is None:
            unmatched.append((r["team"], r["name"])); continue
        con.execute("INSERT OR REPLACE INTO pp_unit VALUES(?,?,?,?,?)",
                    (pid, r["team"], r["name"], r["unit"], r["updated_at"]))
        con.execute("INSERT INTO pp_history VALUES(datetime('now'),?,?,?,?)",
                    (pid, r["team"], r["name"], r["unit"]))
    con.execute("INSERT OR REPLACE INTO meta VALUES('last_pp_update', datetime('now'))")
    con.commit()
    return len(rows), len(teams), unmatched

def pp_label(con, nhl_id):
    r = con.execute("SELECT unit FROM pp_unit WHERE nhl_id=?", (nhl_id,)).fetchone()
    return {1: "1st Unit ✅", 2: "2nd Unit ✅"}.get(r["unit"] if r else 0, "NONE ❌")
