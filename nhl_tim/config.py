from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "nhl.db"
RAW = DATA / "raw"

PREV_SEASON = 20252026
CUR_SEASON = 20262027
GAME_TYPE_REGULAR = 2
GOALIE_MIN_GP = 20  # qualified goalie threshold, configurable
