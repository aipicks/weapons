"""Official NHL API adapter. All GETs cached on disk; refetch only with force=True."""
import json
import time
import requests
from ..config import RAW

STATS = "https://api.nhle.com/stats/rest/en"
WEB = "https://api-web.nhle.com/v1"

def get(url, cache_key, force=False):
    f = RAW / f"{cache_key}.json"
    if f.exists() and not force:
        return json.loads(f.read_text())
    f.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(6):
        r = requests.get(url, timeout=30)
        if r.ok:
            f.write_text(r.text)
            time.sleep(0.15)
            return r.json()
        time.sleep(5 * (attempt + 1) if r.status_code == 429 else 2 * (attempt + 1))
    r.raise_for_status()

def _q(season, gt=2):
    return f"limit=-1&cayenneExp=seasonId={season}%20and%20gameTypeId={gt}"

def skaters(season, force=False):
    return get(f"{STATS}/skater/summary?{_q(season)}", f"skaters_{season}", force)["data"]

def goalies(season, force=False):
    return get(f"{STATS}/goalie/summary?{_q(season)}", f"goalies_{season}", force)["data"]

def teams(season, force=False):
    return get(f"{STATS}/team/summary?{_q(season)}", f"teams_{season}", force)["data"]

def game_log(nhl_id, season, force=False):
    return get(f"{WEB}/player/{nhl_id}/game-log/{season}/2", f"gl_{season}/{nhl_id}", force)["gameLog"]

def team_abbrevs(date="2026-04-16", force=False):
    """full team name -> abbrev, from standings snapshot."""
    st = get(f"{WEB}/standings/{date}", f"standings_{date}", force)["standings"]
    return {r["teamName"]["default"]: r["teamAbbrev"]["default"] for r in st}
