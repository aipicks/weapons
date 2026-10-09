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


def _csv(kind, start_year, force):
    f = RAW / f"moneypuck_{kind}_{start_year}.csv"
    if f.exists() and not force:
        return f.read_text(encoding="utf-8")
    r = requests.get(f"https://moneypuck.com/moneypuck/playerData/seasonSummary/{start_year}/regular/{kind}.csv",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    f.write_text(r.text, encoding="utf-8")
    return r.text


def fetch_teams(start_year, force=False):
    """{team: {situation: {gp, ice, xgf, xga, gf, ga, sf, sa}}} for situations 5on5, 5on4 (PP), 4on5 (PK), all."""
    out = {}
    for row in csv.DictReader(io.StringIO(_csv("teams", start_year, force))):
        out.setdefault(row["team"], {})[row["situation"]] = {
            "gp": int(row["games_played"]), "ice": float(row["iceTime"]), "xgf": float(row["xGoalsFor"]),
            "xga": float(row["xGoalsAgainst"]), "gf": float(row["goalsFor"]), "ga": float(row["goalsAgainst"]),
            "sf": float(row["shotsOnGoalFor"]), "sa": float(row["shotsOnGoalAgainst"])}
    return out


def fetch_goalies(start_year, force=False):
    """{nhl_id: {gp, xg, goals, shots}} - expected goals faced vs goals allowed (all situations)."""
    out = {}
    for row in csv.DictReader(io.StringIO(_csv("goalies", start_year, force))):
        if row["situation"] == "all":
            out[int(row["playerId"])] = {"gp": int(row["games_played"]), "xg": float(row["xGoals"]),
                                         "goals": float(row["goals"]), "shots": float(row["ongoal"])}
    return out
