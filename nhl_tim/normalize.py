import re
import unicodedata

def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", s.replace("-", " "))).strip()

TEAM_ALIAS = {"MON": "MTL", "NAS": "NSH", "SJ": "SJS", "TB": "TBL", "NJ": "NJD", "LA": "LAK",
              "VEG": "VGK", "WAS": "WSH", "WIN": "WPG", "CLB": "CBJ", "UTAH": "UTA", "ARI": "UTA"}

def norm_team(t: str) -> str:
    t = t.upper()
    return TEAM_ALIAS.get(t, t)
