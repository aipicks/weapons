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
