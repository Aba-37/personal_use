#!/usr/bin/env python3
"""
Sha7en charger watcher.

Polls the Sha7en (Ampeco) public app endpoint for one or more charging
stations and pushes an ntfy.sh notification to your phone the moment a
charger transitions from occupied -> available.

No authentication is required by the endpoint; it only wants the x-* headers
the mobile app sends. We discovered these by intercepting the app's traffic.

Designed to run inside a single GitHub Actions job: it checks every
CHECK_INTERVAL seconds for RUN_DURATION seconds, then exits. The workflow's
cron schedule starts it again, giving near-continuous coverage while staying
within GitHub's 5-minute minimum scheduling interval.
"""

import os
import time
import json
import urllib.request
import urllib.error

# --- CONFIG -----------------------------------------------------------------

API_URL = "https://sha7en.eu.charge.ampeco.tech/api/v1/app/locations?operatorCountry=EG"

# The station(s) you want to watch, by their pin/location id.
# 77 = "Madinaty B11" (from your capture). Add your second station's id here,
# e.g. STATION_IDS = ["77", "123"]. Find it by tapping the other station in the
# app while HTTP Toolkit is running and reading the /api/v1/app/pins/<id> call.
STATION_IDS = ["77"]

# ntfy.sh topic to publish to. Set as a GitHub Actions secret named NTFY_TOPIC.
# Anyone who knows the topic name can read/send, so make it long and random.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

CHECK_INTERVAL = 60          # seconds between checks
RUN_DURATION = 14 * 60       # how long this single run keeps checking (seconds)

# Headers copied from the intercepted request. The x-device-id is just an
# identifier the API echoes; reusing it is harmless since the endpoint is
# unauthenticated.
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en",
    "content-type": "application/json",
    "user-agent": "okhttp/4.12.0",
    "x-device-id": "964e1bc7556b1e36",
    "x-internal-app-version": "3.182.0",
    "x-mobile-app-bundle-id": "tech.ampeco.charge.eu.sha7en.app",
    "x-operator-country": "EG",
    "x-platform": "android",
}

# --- CORE -------------------------------------------------------------------


def fetch_status():
    """POST to the locations endpoint and return the parsed JSON payload."""
    body = json.dumps({"locations": {sid: "" for sid in STATION_IDS}}).encode()
    req = urllib.request.Request(API_URL, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def available_chargers(payload):
    """
    Return {station_name: set(of available charger identifiers)}.
    A charger counts as available when its EVSE has isAvailable == True.
    """
    result = {}
    for loc in payload.get("locations", []):
        name = loc.get("name") or str(loc.get("id"))
        free = set()
        for zone in loc.get("zones", []):
            for evse in zone.get("evses", []):
                if evse.get("isAvailable") is True:
                    free.add(str(evse.get("identifier") or evse.get("id")))
        result[name] = free
    return result


def notify(title, message):
    """Send a push notification via ntfy.sh."""
    if not NTFY_TOPIC:
        print(f"[no NTFY_TOPIC set] would notify -> {title}: {message}")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "urgent",
            "Tags": "zap",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=30)
        print(f"NOTIFIED -> {title}: {message}")
    except urllib.error.URLError as e:
        print(f"notify failed: {e}")


def main():
    deadline = time.time() + RUN_DURATION
    prev_free = None  # set on first successful check (silent baseline)

    while time.time() < deadline:
        try:
            free = available_chargers(fetch_status())
        except Exception as e:  # network hiccup, bad JSON, etc.
            print(f"check failed: {e}")
            time.sleep(CHECK_INTERVAL)
            continue

        snapshot = {k: sorted(v) for k, v in free.items()}
        print(f"{time.strftime('%H:%M:%S')} status: {snapshot}")

        if prev_free is None:
            # First reading of the run: record it, don't alert. We only want to
            # be told about NEW openings, not chargers that were already free.
            prev_free = {k: set(v) for k, v in free.items()}
        else:
            for station, now_free in free.items():
                newly_free = now_free - prev_free.get(station, set())
                if newly_free:
                    msg = (
                        f"{station}: {len(now_free)} charger(s) free now "
                        f"({', '.join(sorted(now_free))})"
                    )
                    notify("\u26a1 Charger available!", msg)
                prev_free[station] = now_free

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
