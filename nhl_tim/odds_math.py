def american_to_prob(a):
    a = float(a)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)

def devig2(a, b):
    pa, pb = american_to_prob(a), american_to_prob(b)
    return pa / (pa + pb)

def fmt_american(a):
    a = int(round(float(a)))
    return f"+{a}" if a > 0 else str(a)

def implied_team_totals(total, over_price, under_price, ml_home, ml_away,
                        total_k=2.0, margin_k=4.0):
    """Returns (home_xg, away_xg). Total shifted by over/under lean; split by de-vigged win prob.
    Uses total + ML jointly instead of adding them as independent signals."""
    t = float(total)
    if over_price is not None and under_price is not None:
        t += total_k * (devig2(over_price, under_price) - 0.5)
    pw = devig2(ml_home, ml_away) if ml_home is not None and ml_away is not None else 0.5
    diff = margin_k * (pw - 0.5)
    return (t + diff) / 2, (t - diff) / 2
