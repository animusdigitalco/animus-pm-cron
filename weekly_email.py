"""
Weekly Animus marketing email reminder.

Creates a single Trello card on TEAM BOARD each Monday with the workflow for
sending the Animus weekly marketing email through Kit. The cron does NOT touch
Kit directly — humans draft + send. Upgrade path: replace the placeholder
checklist items with Kit API calls to pre-create a draft broadcast and inject
the broadcast URL into the card desc.

Triggered by: run_weekly.py
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

log = logging.getLogger("pat.weekly_email")

TRELLO_KEY = os.environ.get("TRELLO_KEY", "")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN", "")

TEAM_BOARD = "6a2c670ebdaa073b45f8733b"
LIST_IN_PRODUCTION = "6a2c670fa77180a3dc0c5d65"
MEMBER_ID_JON = "65007a903b5acfc291e26a8e"


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


def _get(path: str, params: dict | None = None) -> dict | list:
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


def _week_label(monday: datetime.date) -> str:
    """Human label for the week. e.g. 'week of Jun 22'."""
    return f"week of {monday.strftime('%b %-d')}"


def create_weekly_email_card(today: datetime.date | None = None) -> dict:
    """Create the weekly Animus marketing email card for the current week.
    Idempotent: re-runs in the same week skip without creating a duplicate."""
    today = today or datetime.date.today()
    # Snap to the Monday of this week
    monday = today - datetime.timedelta(days=today.weekday())
    friday = monday + datetime.timedelta(days=4)

    title = f"📧 Send weekly Animus marketing email ({_week_label(monday)})"

    # Idempotency: don't recreate if a card with this normalized name exists this week
    open_cards = _get(f"/boards/{TEAM_BOARD}/cards", {"fields": "name"}) or []
    closed_cards = _get(f"/boards/{TEAM_BOARD}/cards/closed", {"fields": "name"}) or []
    existing = ({_normalize_card_name(c["name"]) for c in open_cards}
                | {_normalize_card_name(c["name"]) for c in closed_cards})
    if _normalize_card_name(title) in existing:
        log.info("'%s' already exists, skipping", title)
        return {"created": False, "reason": "already_exists", "title": title}

    desc = (
        f"Weekly Animus marketing email — {_week_label(monday)}.\n\n"
        f"**Goal:** ship one email to the Kit list by EOD Friday.\n\n"
        f"**Where to draft:** https://app.kit.com/broadcasts/new\n"
    )

    card = _post("/cards", {
        "idList": LIST_IN_PRODUCTION,
        "name": title,
        "desc": desc,
        "due": f"{friday.isoformat()}T17:00:00.000Z",
        "idMembers": MEMBER_ID_JON,
        "idLabels": _label_id_for("Animus Internal"),
        "pos": "top",
    })
    if card.get("error") or not card.get("id"):
        return {"created": False, "error": card}

    # Workflow checklist
    cl = _post(f"/cards/{card['id']}/checklists", {"name": "Workflow"})
    if cl.get("id"):
        steps = [
            "Pick this week's hook / featured project / topic",
            "Pull a relevant Animus blog post, case study, or win to anchor the email",
            "Draft the email in Kit (subject + preview + body)",
            "Subject line: punchy, specific (not 'Animus Newsletter')",
            "Preview text set (not auto-pulled)",
            "Send or schedule for Wednesday (default)",
            "Log opens / clicks from last week's send",
        ]
        for s in steps:
            _post(f"/checklists/{cl['id']}/checkItems", {"name": s})
            time.sleep(0.04)

    log.info("✓ Created weekly email card: %s", card.get("shortUrl"))
    return {"created": True, "url": card.get("shortUrl"), "title": title}
