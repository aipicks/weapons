"""MoneyPuck skater season summaries: individual expected goals (I_F_xGoals). playerId == NHL id.
MoneyPuck 'season' = start year (2025 = 2025-26)."""
import csv
import io
import requests
from ..config import RAW

URL = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{y}/regular/skaters.csv"


def fetch(start_year, force=False):
    f = RAW / f"moneypuck_{start_year}.csv"
    if f.exists() and not force:
        text = f.read_text(encoding="utf-8")
    else:
        r = requests.get(URL.format(y=start_year), headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        r.raise_for_status()
        text = r.text
        f.write_text(text, encoding="utf-8")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row["situation"] == "all":
            out[int(row["playerId"])] = {"gp": int(row["games_played"]), "xg": float(row["I_F_xGoals"]),
                                         "goals": float(row["I_F_goals"])}
    return out
