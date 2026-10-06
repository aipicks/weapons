"""SportsGameOdds adapter. One slate-level request (paginated), raw response cached per run."""
import json
import os
import requests
from datetime import datetime, timedelta, timezone
from ..config import RAW, ROOT

URL = "https://api.sportsgameodds.com/v2/events"

def _key():
    if os.environ.get("SGO_API_KEY"):
        return os.environ["SGO_API_KEY"]
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SGO_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("SGO_API_KEY missing")

def fetch_slate(date, force=True):
    """Events whose start falls in the ET calendar day `date` (YYYY-MM-DD)."""
    f = RAW / f"sgo_{date}.json"
    if f.exists() and not force:
        return json.loads(f.read_text())
    d = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    params = {"leagueID": "NHL", "oddsAvailable": "true", "limit": 50,
              "startsAfter": (d + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "startsBefore": (d + timedelta(hours=33)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    events, cursor = [], None
    while True:
        if cursor: params["cursor"] = cursor
        r = requests.get(URL, headers={"X-Api-Key": _key()}, params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        events += j["data"]
        cursor = j.get("nextCursor")
        if not cursor: break
    f.write_text(json.dumps(events))
    return events
