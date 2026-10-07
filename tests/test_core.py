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
