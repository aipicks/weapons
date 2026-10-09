"""powerplayunits.com adapter: measured PP1/PP2 per team (from NHL shift charts). Used by update_power_play_units."""
import html
import re
import time
import requests

BASE = "https://powerplayunits.com"
HDR = {"User-Agent": "Mozilla/5.0"}


def _get(path):
    for attempt in range(4):
        try:
            r = requests.get(BASE + path, headers=HDR, timeout=30)
            if r.ok:
                return r.text
        except requests.RequestException:
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"powerplayunits.com {path} unavailable")


def _text(page):
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))


def _names(chunk):
    return [n.strip() for n in re.split(r"\s*,\s*|\s+and\s+", chunk) if n.strip()]


_ROW = re.compile(r"([A-ZÀ-ſ][^\d]*?) (?:C|L|R|D|W|F|LW|RW) (\d+:\d\d) (\d+) (\d+)% (\d+)")


def _unit_table(t, title):
    """{name: share%} from the 'First/Second power-play unit — the five' table on a team page."""
    i = t.find(title)
    if i < 0:
        return {}
    j = t.find("What PP", i)
    chunk = t[i:j if j > 0 else i + 800]
    chunk = chunk.split("PP G", 1)[-1]
    return {m.group(1).strip(): int(m.group(4)) for m in _ROW.finditer(chunk)}


def fetch_all():
    """[{slug, title, measured, first:[names], second:[names]}] for all clubs (units missing -> empty list)."""
    slugs = sorted(set(re.findall(r'href="/team/([a-z0-9-]+)/"', _get("/teams/"))))
    out = []
    for slug in slugs:
        t = _text(_get(f"/team/{slug}/"))
        first = re.search(r"most-used first unit is (.+?) [—–-] together", t)
        second = re.search(r"most-used second unit is (.+?) [—–-] together", t)
        meas = re.search(r"Measured through (\d+ \w+ \d{4})", t)
        title = re.match(r"\s*(.+?) power play units", t)
        p1 = _unit_table(t, "First power-play unit — the five")
        p2 = _unit_table(t, "Second power-play unit — the five")
        # a player listed in both tables goes to the unit he spends more time on (ties -> PP1)
        first = [n for n in p1 if n not in p2 or p1[n] >= p2[n]]
        second = [n for n in p2 if n not in p1 or p2[n] > p1[n]]
        if len(first) >= 3:
            out.append({"slug": slug, "title": (re.match(r"\s*(.+?) power play units", t).group(1) if re.match(r"\s*(.+?) power play units", t) else slug),
                        "measured": meas.group(1) if meas else None, "first": first, "second": second})
            time.sleep(0.4)
            continue
        out.append({"slug": slug, "title": title.group(1) if title else slug, "measured": meas.group(1) if meas else None,
                    "first": _names(first.group(1)) if first else [], "second": _names(second.group(1)) if second else []})
        time.sleep(0.4)
    return out
