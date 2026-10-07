import sqlite3
from .config import DB_PATH, DATA, RAW

SCHEMA = """
CREATE TABLE IF NOT EXISTS player(
  nhl_id INTEGER PRIMARY KEY, name TEXT, name_norm TEXT, pos TEXT, team TEXT);
CREATE TABLE IF NOT EXISTS player_season(
  nhl_id INTEGER, season INTEGER, team TEXT, gp INTEGER, g INTEGER, sog INTEGER,
  goal_games INTEGER, goal_game_pct REAL,
  PRIMARY KEY(nhl_id, season));
CREATE TABLE IF NOT EXISTS team_season(
  team TEXT, season INTEGER, gp INTEGER, ga_pg REAL, ga_rank INTEGER,
  PRIMARY KEY(team, season));
CREATE TABLE IF NOT EXISTS goalie_season(
  nhl_id INTEGER, season INTEGER, name TEXT, team TEXT, gp INTEGER, starts INTEGER,
  gaa REAL, sv_pct REAL, qualified INTEGER, gaa_rank INTEGER, sv_rank INTEGER,
  PRIMARY KEY(nhl_id, season));
CREATE TABLE IF NOT EXISTS game(
  date TEXT, event_id TEXT PRIMARY KEY, start_utc TEXT, away TEXT, home TEXT);
CREATE TABLE IF NOT EXISTS goalie_proj(
  event_id TEXT, team TEXT, opp TEXT, goalie_name TEXT, goalie_id INTEGER, status TEXT, is_fallback INTEGER,
  PRIMARY KEY(event_id, team));
CREATE TABLE IF NOT EXISTS odds_game(
  event_id TEXT, book TEXT, ts TEXT, total REAL, over_price REAL, under_price REAL, ml_home REAL, ml_away REAL,
  PRIMARY KEY(event_id, book));
CREATE TABLE IF NOT EXISTS odds_atg(
  event_id TEXT, nhl_id INTEGER, player TEXT, team TEXT, opp TEXT, book TEXT, american REAL, implied REAL, ts TEXT,
  PRIMARY KEY(event_id, player, book));
CREATE TABLE IF NOT EXISTS player_xg(
  nhl_id INTEGER, season INTEGER, gp INTEGER, xg REAL, goals REAL, PRIMARY KEY(nhl_id, season));
CREATE TABLE IF NOT EXISTS player_toi(
  nhl_id INTEGER, season INTEGER, toi_pg REAL, PRIMARY KEY(nhl_id, season));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

def connect():
    DATA.mkdir(exist_ok=True); RAW.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con
