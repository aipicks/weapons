from .db import connect
from .normalize import norm_name, norm_team
from .sources import dailyfaceoff, nhl_api, powerplayunits

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

def _prefer_forward(con, team, name, pos=None):
    """Same as _match, but when two players share a name on a team (e.g. the two Elias Petterssons) take the forward."""
    pid = _match(con, team, name, pos)
    if pid is None:
        rows = con.execute("SELECT nhl_id, pos FROM player WHERE name_norm=? AND team=?", (norm_name(name), team)).fetchall()
        f = [r for r in rows if r["pos"] == "F"]
        pid = f[0]["nhl_id"] if len(f) == 1 else None
    return pid


def update_power_play_units():
    """Primary source: powerplayunits.com (measured from NHL shift charts). Falls back to Daily Faceoff if it is down."""
    try:
        return update_from_ppu()
    except Exception as e:
        print("powerplayunits.com failed, using Daily Faceoff:", e)
        return update_from_dailyfaceoff()


def update_from_ppu():
    con = connect(); _ensure(con)
    teams = powerplayunits.fetch_all()
    abbr = {norm_name(k): v for k, v in nhl_api.team_abbrevs(force=False).items()}
    changed, unmatched, n = 0, [], 0
    for t in teams:
        ab = abbr.get(norm_name(t["title"]))
        if not ab or len(t["first"]) < 3:  # incomplete page: keep what we have for this team
            unmatched.append(("team", t["title"]))
            continue
        new = {}
        for unit, names in ((1, t["first"]), (2, t["second"])):
            for name in names:
                pid = _prefer_forward(con, ab, name)
                if pid is None:
                    unmatched.append((ab, name))
                elif pid not in new:  # a player listed on both units counts as PP1
                    new[pid] = unit
        old = {r["nhl_id"]: r["unit"] for r in con.execute("SELECT nhl_id, unit FROM pp_unit WHERE team=?", (ab,))}
        changed += sum(1 for pid in set(new) | set(old) if new.get(pid, 0) != old.get(pid, 0))
        con.execute("DELETE FROM pp_unit WHERE team=?", (ab,))
        for pid, unit in new.items():
            nm = con.execute("SELECT name FROM player WHERE nhl_id=?", (pid,)).fetchone()["name"]
            con.execute("INSERT OR REPLACE INTO pp_unit VALUES(?,?,?,?,?)", (pid, ab, nm, unit, t["measured"]))
            con.execute("INSERT INTO pp_history VALUES(datetime('now'),?,?,?,?)", (pid, ab, nm, unit))
            n += 1
    con.execute("INSERT OR REPLACE INTO meta VALUES('last_pp_update', datetime('now'))")
    con.execute("INSERT OR REPLACE INTO meta VALUES('pp_source', 'powerplayunits.com')")
    con.commit()
    return n, len(teams), unmatched, changed


def update_lines(con=None):
    """Forward lines and defense pairs from Daily Faceoff -> player_line (refreshed daily; lines change often)."""
    con = con or connect()
    rows = dailyfaceoff.fetch_lines()
    con.execute("DELETE FROM player_line")
    n, unmatched = 0, []
    for r in rows:
        team = norm_team(r["team"])
        pos = "D" if r["group"].startswith("d") else "F"
        pid = _match(con, team, r["name"], pos) or _prefer_forward(con, team, r["name"], pos)
        if pid is None:
            unmatched.append((team, r["name"]))
            continue
        con.execute("INSERT OR REPLACE INTO player_line VALUES(?,?,?,?)", (pid, team, r["group"].upper(), r["name"]))
        n += 1
    con.execute("INSERT OR REPLACE INTO meta VALUES('lines_updated', datetime('now'))")
    con.commit()
    return n, unmatched


def update_from_dailyfaceoff():
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
