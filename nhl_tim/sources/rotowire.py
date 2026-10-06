"""RotoWire projected goalies (JSON table endpoint behind the starting-goalies page)."""
import requests

URL = "https://www.rotowire.com/hockey/tables/projected-goalies.php"

def fetch(date):
    r = requests.get(URL, params={"date": date}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    out = []
    for g in r.json():
        for side, opp in (("home", "visit"), ("visit", "home")):
            name = g.get(f"{side}Player") or ""
            st = g.get(f"{side}Status") or "Unknown"
            out.append({"team": g[f"{side}team"], "opp": g[f"{opp}team"], "goalie": name,
                        "status": st if st in ("Confirmed", "Expected") else "Unknown"})
    return out
