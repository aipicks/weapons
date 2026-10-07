"""Skater vs. goalie history: games both appeared in (on opposite teams), shots on goal and goals
by the skater while that goalie was in net (from play-by-play goalieInNetId)."""
from .config import PREV_SEASON, CUR_SEASON
from .normalize import norm_name
from .sources import nhl_api

SEASONS = [y * 10000 + y + 1 for y in range(2019, 2027)]  # 20192020 .. 20262027


def _shared_games(skater_id, goalie_id, seasons, game_types):
    shared = []
    for season in seasons:
        for gt in game_types:
            sk = {g["gameId"]: g for g in nhl_api.game_log_gt(skater_id, season, gt)}
            if not sk:
                continue
            for g in nhl_api.game_log_gt(goalie_id, season, gt):
                s = sk.get(g["gameId"])
                if s and s["opponentAbbrev"] == g["teamAbbrev"]:  # opposite sides of the ice
                    shared.append((season, gt, s["gameDate"], s["teamAbbrev"], g["teamAbbrev"], g["gameId"]))
    return shared


def skater_vs_goalie(skater_id, goalie_id, seasons=SEASONS, game_types=(2, 3)):
    games = _shared_games(skater_id, goalie_id, seasons, game_types)
    detail, tot_sog, tot_g = [], 0, 0
    for season, gt, date, team, opp, gid in games:
        pbp = nhl_api.play_by_play(gid) or {"plays": []}
        sog = goals = 0
        for e in pbp["plays"]:
            d = e.get("details") or {}
            if e["typeDescKey"] == "shot-on-goal" and d.get("shootingPlayerId") == skater_id and d.get("goalieInNetId") == goalie_id:
                sog += 1
            elif e["typeDescKey"] == "goal" and d.get("scoringPlayerId") == skater_id and d.get("goalieInNetId") == goalie_id:
                sog += 1  # goals count as shots on goal
                goals += 1
        detail.append({"date": date, "season": season, "type": "playoffs" if gt == 3 else "regular", "team": team,
                       "opp": opp, "sog": sog, "g": goals})
        tot_sog += sog
        tot_g += goals
    scored = sum(1 for d in detail if d["g"] > 0)
    return {"gp": len(detail), "sog": tot_sog, "g": tot_g, "goal_games": scored, "games": detail}


def find_goalie(con, text):
    t = norm_name(text)
    rows = {r["nhl_id"]: r for r in con.execute("SELECT nhl_id, name, team FROM goalie_season ORDER BY season")}
    hits = [r for r in rows.values() if t == norm_name(r["name"]) or t == norm_name(r["name"]).split()[-1]]
    return hits


def find_skater(con, text):
    t = norm_name(text)
    return [r for r in con.execute("SELECT nhl_id, name, team FROM player")
            if t == r["name_norm"] if False] or [r for r in con.execute("SELECT nhl_id, name, team, name_norm FROM player")
                                                  if t == r["name_norm"] or t == r["name_norm"].split()[-1]]
