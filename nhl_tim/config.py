from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DB_PATH = DATA / "nhl.db"
RAW = DATA / "raw"

PREV_SEASON = 20252026
CUR_SEASON = 20262027
GAME_TYPE_REGULAR = 2
GOALIE_MIN_GP = 20  # qualified goalie threshold, configurable

MARKET_BOOK = "fanduel"
POOLS_CSV = DATA / "pools.csv"  # columns: pool,player[,team]  (pool = 1|2|3)

# Transparent baseline priors (NOT fitted). Replace via backtest/logistic regression later.
W = {
    "market_weight": 0.70,     # logit-blend weight on market (vig-adjusted consensus) when available
    "vig_factor": 0.94,        # fallback vig strip when a book has no No-side price (otherwise Yes/No devig)
    "league_goal_game": 0.21,  # per-skater-game scoring rate prior
    "prior_games": 25,         # shrink prev-season goal-game rate toward league
    "cur_games_k": 30,         # games of weight on prev-season rate when blending current season
    "team_xg": 0.10,           # per goal of team implied total above 3.0
    "pp_min": 0.15,            # logit per expected PP minute above the role average (PP time x opponent PK time)
    "pp1": 0.15, "pp2": 0.03,  # flat role bonus: PP1 counts for a lot more than PP2
    "toi_min": 0.05,           # logit per minute of TOI/G above the position average
    "sog_per": 0.12,           # logit per shot/game above the position average
    "opp_ga": 0.35,            # logit per goal of opponent GA/G above league average (25-26 blended with 26-27)
    "opp_k": 15,               # opponent games of weight on last season before the current season takes over
    "role_k": 10,              # games before current-season usage (TOI, PP time) outweighs last season
    "goalie_sv": 12.0,         # logit per 1.0 of SV% below league avg (.015 -> ~0.18)
    "goalie_gp_k": 10,         # shrink current goalie SV% toward prev season by GP/(GP+k)
    "league_sv": 0.898,
    "xg_weight": 0.6,          # share of last-season rate taken from individual xG (Poisson P(>=1)) vs goal-game rate
}


# Preseason projected defensive-unit ranking (1 = best), supplied by the user. Display only, NOT used in the model.
PRESEASON_D_ORDER = ["COL", "DAL", "MIN", "CAR", "BUF", "FLA", "NYR", "OTT", "MTL", "VGK", "CBJ", "TBL", "NYI",
                     "NJD", "PHI", "UTA", "EDM", "WSH", "BOS", "WPG", "LAK", "DET", "STL", "TOR", "NSH", "SEA",
                     "PIT", "ANA", "CHI", "CGY", "SJS", "VAN"]
PRESEASON_D_TIES = {"NJD", "PHI", "UTA", "CGY", "SJS"}
PRESEASON_D = {t: i + 1 for i, t in enumerate(PRESEASON_D_ORDER)}

# Shots-on-goal model priors (starting values, not fitted)
SOG = {
    "prior_games": 20,    # shrink last-season shots/game toward the position average
    "cur_k": 12,          # games before this season's rate outweighs last season's
    "form_w": 0.25,       # weight on the last 10 games
    "opp_k": 15,          # opponent games before this season's shots-against outweighs last season's
    "opp_exp": 0.7,       # exponent on opponent shots-against vs league average
    "toi_exp": 0.5,       # exponent on this season's TOI vs last season's (scaled by games played)
    "pace_exp": 0.25,     # exponent on team implied goal total vs 3.0
    "pp_scale": 0.35,     # extra shots for PP players when the opponent takes many penalties
    "nb_r": 8.0,          # negative binomial dispersion (variance = mu + mu^2 / r)
    "market_weight": 0.6, # logit blend weight on the de-vigged market vs the model
}
