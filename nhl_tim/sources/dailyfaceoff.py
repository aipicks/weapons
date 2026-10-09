"""Daily Faceoff adapter: PP1/PP2 per team from embedded Next.js JSON. Run only via update_power_play_units."""
import json
import re
import time
import requests

BASE = "https://www.dailyfaceoff.com/teams"
HDR = {"User-Agent": "Mozilla/5.0"}

def _next_data(url):
    for attempt in range(4):
        r = requests.get(url, headers=HDR, timeout=30)
        if r.ok:
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
            return json.loads(m.group(1))["props"]["pageProps"]
        time.sleep(3 * (attempt + 1))
    r.raise_for_status()

def fetch_all():
    """Returns list of dicts {team, name, unit(1|2), updated_at}."""
    first = _next_data(f"{BASE}/toronto-maple-leafs/line-combinations")
    teams = first["sortedTeams"]
    out = []
    for t in teams:
        pp = _next_data(f"{BASE}/{t['slug']}/line-combinations")
        combos = pp["combinations"]
        for p in combos["players"]:
            if p["groupIdentifier"] in ("pp1", "pp2"):
                out.append({"team": t["shortName"], "name": p["name"],
                            "pos": "D" if p["positionIdentifier"].startswith("d") else "F", "unit": int(p["groupIdentifier"][-1]),
                            "updated_at": combos.get("updatedAt")})
        time.sleep(0.5)
    return out


STATUS = {"Confirmed": "Confirmed", "Likely": "Likely"}  # anything else (no news yet) -> Unconfirmed


def starting_goalies(date, today):
    """Daily Faceoff starting goalies for one slate. Returns [{team, opp, goalie, status, note}] (team = goalie's team,
    as full names; caller maps to abbreviations). `today` is the ET date the unparameterised page shows."""
    url = "https://www.dailyfaceoff.com/starting-goalies" + ("" if date == today else f"/{date}")
    pp = _next_data(url)
    if pp.get("date") != date:
        return []
    out = []
    for g in pp["data"]:
        for side, other in (("home", "away"), ("away", "home")):
            name = g.get(f"{side}GoalieName")
            if not name:
                continue
            out.append({"team": g[f"{side}TeamName"], "opp": g[f"{other}TeamName"], "goalie": name,
                        "status": STATUS.get(g.get(f"{side}NewsStrengthName"), "Unconfirmed"),
                        "note": (g.get(f"{side}NewsDetails") or "").strip()})
    return out


def fetch_lines():
    """Forward lines (f1-f4) and defense pairs (d1-d3) per team from Daily Faceoff's line combinations.
    Returns [{team, name, group, pos}] with team = DF short code (caller normalizes)."""
    first = _next_data(f"{BASE}/toronto-maple-leafs/line-combinations")
    out = []
    for t in first["sortedTeams"]:
        pp = _next_data(f"{BASE}/{t['slug']}/line-combinations")
        for p in pp["combinations"]["players"]:
            if p["groupIdentifier"] in ("f1", "f2", "f3", "f4", "d1", "d2", "d3"):
                out.append({"team": t["shortName"], "name": p["name"], "group": p["groupIdentifier"],
                            "pos": p["positionIdentifier"]})
        time.sleep(0.5)
    return out
