#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Like-Zaehler — GitHub-Actions-taugliche Version
mit Threading, atomarem State-Handling und adaptivem Backoff.
"""

import os
import json
import time
import re
import urllib.request
import urllib.error
import tempfile
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

AJAX = "https://mammut.gymvhh.de/wp-admin/admin-ajax.php"
REST = "https://mammut.gymvhh.de/wp-json/wp/v2/posts/28966"
PID = "28966"

TARGET = int(os.environ.get("TARGET", "1000000"))
TH = int(os.environ.get("TH", "10"))
BUDGET = int(os.environ.get("BUDGET", "600"))

STATE = os.environ.get("STATE_FILE", "like2_state.json")
STATE_DIR = os.path.dirname(os.path.abspath(STATE))

st = {"n": 0, "likes": 0, "ok": 0, "c508": 0, "other": 0}
state_lock = Lock()


def load_state():
    global st
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        if os.path.exists(STATE):
            with open(STATE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for key in st:
                    if key in data:
                        st[key] = data[key]
            print(f"State geladen aus {STATE}: n={st['n']:,}, likes={st['likes']:,}", flush=True)
        else:
            print("Kein alter State gefunden, starte frisch.", flush=True)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Warnung beim Laden des States: {e}", flush=True)


def save_state():
    with state_lock:
        temp_file = None
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            fd, temp_file = tempfile.mkstemp(
                prefix="like2_",
                suffix=".tmp",
                dir=STATE_DIR
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(st, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_file, STATE)
            return True
        except OSError as e:
            print(f"Fehler beim Speichern des States: {e}", flush=True)
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
            return False


def real_likes():
    try:
        req = urllib.request.Request(
            REST,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Cache-Control": "no-cache"
            }
        )
        with urllib.request.urlopen(req, timeout=12) as response:
            html = response.read().decode("utf-8", errors="ignore")
            patterns = [
                r'sv-num-like\\?"?>\\?"?(\d+)',
                r'sv-num-like[^0-9]{0,20}(\d+)',
                r'"like_count"\s*:\s*(\d+)',
                r'"likes"\s*:\s*(\d+)'
            ]
            for pattern in patterns:
                m = re.search(pattern, html)
                if m:
                    return int(m.group(1))
        return None
    except Exception:
        return None


def vote(i):
    ip = f"10.{(i>>16)%250}.{(i>>8)%254+1}.{i%254+1}"
    data = f"action=sv_manage_votes&pid={PID}&vote_action=like&user_ip={ip}".encode("utf-8")
    req = urllib.request.Request(
        AJAX,
        data=data,
        method="POST",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return str(response.status)
    except urllib.error.HTTPError as e:
        return str(e.code)
    except Exception:
        return "error"


def main():
    global st
    load_state()

    print("=" * 60, flush=True)
    print("LIKE-ZAEHLER RUNNER", flush=True)
    print("=" * 60, flush=True)
    print(f"Ziel: {TARGET:,} | Start-n: {st['n']:,} | Threads: {TH} | Budget: {BUDGET}s", flush=True)

    t0 = time.time()
    cur = real_likes()
    if cur is not None:
        st["likes"] = cur
        print(f"  Live-Zaehler Startwert: {cur:,}", flush=True)

    current_sleep = 3.0

    try:
        with ThreadPoolExecutor(max_workers=TH) as ex:
            while True:
                if st["likes"] >= TARGET:
                    print(f"  ZIEL ERREICHT: {st['likes']:,}", flush=True)
                    break

                elapsed = time.time() - t0
                if elapsed >= BUDGET:
                    print(f"  Zeitbudget von {BUDGET}s erreicht.", flush=True)
                    break

                batch = list(range(st["n"], st["n"] + TH))
                codes = list(ex.map(vote, batch))

                with state_lock:
                    st["n"] += TH
                    for c in codes:
                        if c == "200":
                            st["ok"] += 1
                        elif c == "508":
                            st["c508"] += 1
                        else:
                            st["other"] += 1

                if (st["n"] // TH) % 10 == 0:
                    lv = real_likes()
                    if lv is not None:
                        st["likes"] = lv
                    save_state()
                    el = time.time() - t0
                    rate = st["ok"] / el if el else 0
                    print(
                        f"  n={st['n']:,} | likes={st['likes']:,} | "
                        f"ok={st['ok']} 508={st['c508']} other={st['other']} | "
                        f"{rate:.1f}/s | {el:.0f}s",
                        flush=True
                    )

                if codes.count("508") > TH // 2:
                    current_sleep = min(current_sleep * 1.5, 15.0)
                    time.sleep(current_sleep)
                else:
                    current_sleep = max(1.0, current_sleep * 0.8)

    except KeyboardInterrupt:
        print("\nProgramm manuell unterbrochen.", flush=True)

    finally:
        lv = real_likes()
        if lv is not None:
            st["likes"] = lv
        save_state()

        el_total = time.time() - t0
        print("\n" + "=" * 60, flush=True)
        print("LAUF BEENDET", flush=True)
        print(f"Likes: {st['likes']:,} | OK: {st['ok']:,} | 508: {st['c508']:,} | Dauer: {el_total:.0f}s", flush=True)
        print("=" * 60, flush=True)


if __name__ == "__main__":
    main()
