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
    "market_weight": 0.50,     # logit-blend weight on market (vig-adjusted consensus) when available; 0.5 = model and market equal
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
    "cur_k": 14,          # games before this season's rate outweighs last season's (roles change, but early samples are noisy)
    "role_exp": 0.8,      # exponent on (current TOI / regulars' TOI) that scales the position-average prior
    "min_games": 3,       # career games needed before a pick is rated (fewer: shown but not ranked as a pick)
    "att_recent_w": 0.20, # share of the attempts/game rate taken from the last 5 games' attempts (needs 3+ games)
    "last20_w": 0.5,      # share of last season's rate taken from its final 20 games (recent form) vs the full season
    "last20_k": 10,       # shrinkage games for that last-20 rate toward the position average
    "ot_opp_exp": 0.5,    # exponent on the opponent's on-target share of attempts allowed vs league (blocks/misses)
    "att_w": 0.5,         # weight on the shot-attempt projection (attempts/G x regressed on-target rate) vs shots-based
    "att_prior_games": 20,  # shrink attempts/game toward the position average
    "ot_k": 150,          # attempts of prior added when regressing a player's on-target rate toward the position average
    "form_w": 0.15,       # weight on the last 10 games (small: a short window is volatile)
    "opp_k": 15,          # opponent games before this season's shots-against outweighs last season's
    "opp_exp": 0.7,       # exponent on opponent shots-against vs league average
    "toi_exp": 0.5,       # exponent on this season's TOI vs last season's (scaled by games played)
    "pace_exp": 0.25,     # exponent on team implied goal total vs 3.0
    "pp_mult": 1.8,       # a PP minute produces this many times the shots of an even-strength minute (for the shorthanded-time adjustment)
    "nb_r": 8.0,          # negative binomial dispersion (variance = mu + mu^2 / r)
    "market_weight": 0.5, # logit blend weight on the de-vigged market vs the model; 0.5 = model and market equal
}

# Game (moneyline / total) model priors (starting values, not fitted)
GAME = {
    "cur_k": 20,           # games before this season's team rates outweigh last season's
    "goalie_shots_k": 900, # shots faced before a goalie's goals-vs-expected is trusted half-way
    "home": 1.035, "away": 0.975,   # home ice scoring multipliers
    "b2b_off": 0.97, "b2b_def": 1.03,  # second night of a back-to-back: fewer goals for, more against
    "ot_skill": 0.5,       # how much better teams win overtime/shootout (0 = coin flip, 1 = proportional to goal share)
    "ot_goal": 0.55,       # chance a tied game ends on an overtime goal (adds one goal to the total)
    "market_w": 0.5,       # weight on the de-vigged market in the final probability (logit blend); 0.5 = model and market equal
    # empty-net transitions fitted to the 1,312 regular-season games of 2025-26 (share of regulation margins that grow):
    "en_1to2": 0.116, "en_2to3": 0.394, "en_3to4": 0.067,
    "bet_ev": 0.03, "lean_ev": 0.01,  # EV thresholds for BET / LEAN (below LEAN = PASS)
}

# Only these five sportsbooks are ever used (stored, displayed, or priced). Everything else (ESPN BET, Unibet, PointsBet, ...) is dropped at ingest.
ALLOWED_BOOKS = ("fanduel", "draftkings", "betmgm", "caesars")  # Bovada removed on request 2026-10-16

# Team matchup ranking for the Tim Hortons tab: weights on the three ranks, and on the pieces of the defence rank
MATCHUP = {"goalie": 1 / 3, "xg": 1 / 3, "defense": 1 / 3, "def_cur": 0.4, "def_prev": 0.3, "def_pre": 0.3}

# Shots matchup score: weights on the ranks of the opponent's attempts allowed, shots allowed, shots allowed to the
# player's position group, blocked share (fewer = better) and on-target share (more = better)
SHOTMATCH = {"att": 0.20, "sog": 0.20, "pos": 0.30, "blk": 0.15, "ot": 0.15}

# Books to ignore for player props (shots, anytime goal) on a given date, e.g. when a book's prop prices look unusable
PLAYER_PROP_EXCLUDE = {}  # {"YYYY-MM-DD": ("book", ...)}; Bovada is removed everywhere now
