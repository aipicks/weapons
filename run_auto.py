"""Unattended board refresh, run by Windows Task Scheduler at 7:00, 13:00 and 18:00 local (Eastern) time.

Same steps as the manual routine, minus the Tim Hortons pool screenshots (data/pools.csv is left as it is):
update_daily, drop today's unsettled not-yet-started predictions, update_daily again, build_site, tests, commit, push.
Writes logs/auto-YYYY-MM-DD.log. A lock file stops two runs from overlapping.
"""
import datetime
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
LOG = LOGS / f"auto-{datetime.date.today().isoformat()}.log"
LOCK = ROOT / "data" / "auto_run.lock"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashes, nothing to Ctrl+C


def log(msg):
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    if sys.stdout:
        print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(*cmd, timeout=1500):
    log("$ " + " ".join(cmd))
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    tail = (p.stdout + p.stderr).strip().splitlines()[-6:]
    for t in tail:
        log("   " + t)
    return p.returncode


def main():
    log("run started")
    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3600:
        log("another run is in progress (lock file < 1h old); exiting")
        return 1
    LOCK.write_text(str(os.getpid()))
    try:
        py = sys.executable.replace("pythonw.exe", "python.exe")
        if run(py, "-m", "nhl_tim.cli", "update_daily") != 0:
            log("update_daily failed; stopping")
            return 2
        con = sqlite3.connect("data/nhl.db")
        day = datetime.date.today().isoformat()  # the board date (this PC runs on Eastern time)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        n = con.execute("DELETE FROM pred_log WHERE date=? AND settled=0 AND start>?", (day, now)).rowcount
        con.commit()
        con.close()
        log(f"removed {n} unstarted unsettled predictions for {day}")
        if run(py, "-m", "nhl_tim.cli", "update_daily") != 0:
            log("second update_daily failed; stopping")
            return 3
        if run(py, "-m", "nhl_tim.cli", "build_site") != 0:
            log("build_site failed; stopping")
            return 4
        if run(py, "-m", "pytest", "-q", "tests") != 0:
            log("tests failed; not publishing")
            return 5
        run("git", "add", "-A")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], creationflags=NO_WINDOW, stdin=subprocess.DEVNULL).returncode == 0:
            log("nothing changed; done")
            return 0
        run("git", "commit", "-q", "-m", f"Auto board update {datetime.datetime.now():%Y-%m-%d %H:%M}")
        rc = run("git", "push")
        log("pushed" if rc == 0 else "push failed")
        return 0 if rc == 0 else 6
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
