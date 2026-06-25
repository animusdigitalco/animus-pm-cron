"""
Recurring billing reminder cards.

Fires alongside the monthly marketing card creation on the 1st of each month.
Each card is idempotent — re-runs in the same month skip without dupes.
"""
from __future__ import annotations
import calendar
import datetime
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("pat.billing_cards")

TRELLO_KEY = os.environ.get("TRELLO_KEY", "")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN", "")

TEAM_BOARD = "6a2c670ebdaa073b45f8733b"
LIST_NEW_TASKS = "6a2c670eae554c80c937f22e"
MEMBER_ID_JON = "65007a903b5acfc291e26a8e"

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _post(path: str, form: dict) -> dict:
    body = urllib.parse.urlencode({**form, "key": TRELLO_KEY, "token": TRELLO_TOKEN}).encode()
    req = urllib.request.Request(f"https://api.trello.com/1{path}", data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    for _ in range(3):
        try:
            r = urllib.request.urlopen(req, timeout=30).read()
            return json.loads(r) if r else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2); continue
            return {"error": e.code, "detail": e.read().decode()[:200]}
        except Exception:
            time.sleep(1)
    return {"error": "retries exhausted"}


def _get(path: str, params: dict | None = None):
    p = {"key": TRELLO_KEY, "token": TRELLO_TOKEN}
    if params:
        p.update({k: str(v) for k, v in params.items()})
    url = f"https://api.trello.com/1{path}?" + urllib.parse.urlencode(p)
    return json.loads(urllib.request.urlopen(url, timeout=30).read())


def _normalize_card_name(name: str) -> str:
    return "".join(name.lower().split())


def _label_id_for(name: str) -> str:
    labels = _get(f"/boards/{TEAM_BOARD}/labels") or []
    for l in labels:
        if l.get("name") == name:
            return l["id"]
    return ""


def create_ten_seven_academy_billing_card(year: int, month: int) -> dict:
    """Create the monthly Ten Seven Security academy hosting billing reminder.
    Idempotent — re-runs in the same month skip."""
    month_name = MONTH_NAMES[month - 1]
    last_day = calendar.monthrange(year, month)[1]
    due_day = min(5, last_day)
    title = f"💸 Bill Ten Seven Security — Academy hosting ({month_name} {year})"

    open_cards = _get(f"/boards/{TEAM_BOARD}/cards", {"fields": "name"}) or []
    closed_cards = _get(f"/boards/{TEAM_BOARD}/cards/closed", {"fields": "name"}) or []
    existing = ({_normalize_card_name(c["name"]) for c in open_cards}
                | {_normalize_card_name(c["name"]) for c in closed_cards})
    if _normalize_card_name(title) in existing:
        log.info("'%s' already exists, skipping", title)
        return {"created": False, "reason": "already_exists", "title": title}

    desc = (
        f"Monthly billing reminder — **Ten Seven Security academy hosting**.\n\n"
        f"Send the invoice for {month_name} {year} academy hosting via Stripe.\n\n"
        f"Standing details:\n"
        f"- Client: Ten Seven Security\n"
        f"- Service: Academy hosting (recurring)\n"
        f"- Due by {month_name} {due_day} so the invoice goes out early in the month.\n\n"
        f"Pat re-creates this card on the 1st of every month."
    )

    card = _post("/cards", {
        "idList": LIST_NEW_TASKS,
        "name": title,
        "desc": desc,
        "due": f"{year}-{month:02d}-{due_day:02d}T17:00:00.000Z",
        "idMembers": MEMBER_ID_JON,
        "idLabels": _label_id_for("Ten Seven Security"),
        "pos": "top",
    })
    if card.get("error") or not card.get("id"):
        return {"created": False, "error": card}
    log.info("✓ Created billing card: %s", card.get("shortUrl"))
    return {"created": True, "url": card.get("shortUrl"), "title": title}
