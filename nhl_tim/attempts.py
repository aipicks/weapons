"""Shot attempts per game for a player, from the official play-by-play.

An attempt is a shot on goal, a goal, a missed shot or a shot that was blocked (credited to the shooter). Shootout
attempts are ignored. Finished games never change, so each play-by-play is cached on disk once."""
from .sources import nhl_api

_ATTEMPT = {"shot-on-goal": "shootingPlayerId", "missed-shot": "shootingPlayerId", "blocked-shot": "shootingPlayerId",
            "goal": "scoringPlayerId"}
_cache = {}


def game_attempts(game_id):
    """{player id: attempts} for one finished game."""
    if game_id in _cache:
        return _cache[game_id]
    try:
        pb = nhl_api.play_by_play(game_id, force=False) or {}
    except Exception:
        return {}
    out = {}
    for e in pb.get("plays", []):
        key = _ATTEMPT.get(e.get("typeDescKey"))
        if not key or (e.get("periodDescriptor") or {}).get("periodType") == "SO":
            continue
        pid = (e.get("details") or {}).get(key)
        if pid:
            out[pid] = out.get(pid, 0) + 1
    _cache[game_id] = out
    return out


def last_attempts(pid, game_ids, n=5):
    """Attempts in the player's last n games (newest first); game_ids are the games he played, newest first."""
    return [game_attempts(g).get(pid, 0) for g in game_ids[:n]]
