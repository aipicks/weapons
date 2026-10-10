import math
from nhl_tim.normalize import norm_name, norm_team
from nhl_tim.odds_math import american_to_prob, devig2, fmt_american, implied_team_totals
from nhl_tim.history import _ranks
from nhl_tim.output.table import team_color, third_color


def test_american_prob():
    assert math.isclose(american_to_prob(100), 0.5)
    assert math.isclose(american_to_prob(-200), 2 / 3)
    assert math.isclose(american_to_prob(150), 0.4)


def test_devig_symmetric():
    assert math.isclose(devig2(-110, -110), 0.5)


def test_fmt_american():
    assert fmt_american(140) == "+140" and fmt_american(-155) == "-155"


def test_implied_totals_favorite_gets_more():
    home, away = implied_team_totals(6.5, -110, -110, -220, 185)
    assert home > away and math.isclose(home + away, 6.5, abs_tol=1e-9)


def test_names_and_teams():
    assert norm_name("Jiříček, Jr.".replace(",", "")) == "jiricek"
    assert norm_name("T.J. Hughes") == norm_name("TJ Hughes")
    assert norm_team("NAS") == "NSH" and norm_team("MON") == "MTL" and norm_team("TOR") == "TOR"


def test_ranks_direction():
    low_best = _ranks([("a", 2.5), ("b", 3.0), ("c", 3.5)], "x", True)
    high_best = _ranks([("a", .91), ("b", .90), ("c", .89)], "x", False)
    assert low_best["a"] == 1 and high_best["a"] == 1 and low_best["c"] == 3


def test_color_bands():
    assert [team_color(r) for r in (1, 10, 11, 20, 21, 32)] == ["red", "red", "gray", "gray", "green", "green"]
    assert third_color(5, 66) == "red" and third_color(30, 66) == "gray" and third_color(60, 66) == "green"


def test_preseason_defense_ranks():
    from nhl_tim.config import PRESEASON_D, PRESEASON_D_TIES
    assert sorted(PRESEASON_D.values()) == list(range(1, 33)) and len(PRESEASON_D) == 32
    assert PRESEASON_D["COL"] == 1 and PRESEASON_D["VAN"] == 32 and "UTA" in PRESEASON_D_TIES


def test_update_daily_steps_exist():
    """Guards against deleting an updater that update_daily calls (happened once)."""
    import inspect
    from nhl_tim import daily
    src = inspect.getsource(daily.update_daily)
    import re
    for name in re.findall(r"\b(update_\w+|ingest_\w+)\(", src):
        assert callable(getattr(daily, name, None)), name


def test_game_outcome_probs():
    from nhl_tim.games import outcome_probs
    p, over, reg = outcome_probs(3.0, 3.0, None)
    assert abs(p - 0.5) < 1e-5                      # equal teams: coin flip
    p2, _, _ = outcome_probs(3.5, 2.5, None)
    assert p2 > 0.5
    o, u, push = over(6.0)
    assert abs(o + u + push - 1) < 1e-6 and 0 < o < 1
    assert over(5.5)[0] > over(6.5)[0]               # higher line, lower P(over)


def test_only_five_books_allowed():
    from nhl_tim.config import ALLOWED_BOOKS
    assert set(ALLOWED_BOOKS) == {"fanduel", "draftkings", "betmgm", "espnbet", "bovada"}
    from nhl_tim.db import connect
    con = connect()
    for tbl in ("odds_game", "odds_atg", "odds_sog"):
        assert not con.execute(f"SELECT 1 FROM {tbl} WHERE book NOT IN ({','.join('?' * 5)}) LIMIT 1", ALLOWED_BOOKS).fetchone()


def test_team_season_insert_keeps_extra_columns():
    """Regression: update_team_stats must work after sa_pg/sa_rank columns were added to team_season."""
    import inspect
    from nhl_tim import daily, history
    for mod in (daily, history):
        src = inspect.getsource(mod)
        assert "INSERT OR REPLACE INTO team_season VALUES" not in src
