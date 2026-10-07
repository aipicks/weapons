"""Official NHL API adapter. All GETs cached on disk; refetch only with force=True."""
import json
import time
import requests
from ..config import RAW

STATS = "https://api.nhle.com/stats/rest/en"
WEB = "https://api-web.nhle.com/v1"

def _read(f):
    b = f.read_bytes()
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:  # caches written before utf-8 was enforced
        return b.decode("cp1252")


def get(url, cache_key, force=False):
    f = RAW / f"{cache_key}.json"
    if f.exists() and not force:
        try:
            return json.loads(_read(f))
        except ValueError:  # corrupt/partial cache file: refetch
            pass
    f.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(6):
        r = requests.get(url, timeout=30)
        if r.status_code == 404:  # e.g. no playoff log for that season: cache the miss
            f.write_text("null", encoding="utf-8")
            return None
        if r.ok:
            f.write_text(r.text, encoding="utf-8")
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

def skater_powerplay(season, force=False):
    return get(f"{STATS}/skater/powerplay?{_q(season)}", f"skater_pp_{season}", force)["data"]

def team_penaltykill(season, force=False):
    return get(f"{STATS}/team/penaltykill?{_q(season)}", f"team_pk_{season}", force)["data"]

def team_abbrevs(date="2026-04-16", force=False):
    """full team name -> abbrev, from standings snapshot."""
    st = get(f"{WEB}/standings/{date}", f"standings_{date}", force)["standings"]
    return {r["teamName"]["default"]: r["teamAbbrev"]["default"] for r in st}


def game_log_gt(nhl_id, season, game_type, force=False):
    """Player game log for any game type (2 regular, 3 playoffs); [] when none exists."""
    j = get(f"{WEB}/player/{nhl_id}/game-log/{season}/{game_type}", f"gl{game_type}_{season}/{nhl_id}", force)
    return (j or {}).get("gameLog", [])

def play_by_play(game_id, force=False):
    return get(f"{WEB}/gamecenter/{game_id}/play-by-play", f"pbp/{game_id}", force)
