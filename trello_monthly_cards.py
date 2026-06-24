"""
Trello monthly marketing cards — Pat's job that fires on the 1st of every month.

Creates ONE "{Client} — {Month} {Year} Marketing" card per retainer client on
TEAM BOARD → MAIN CARDS list, with one checklist per service line. Each checklist
item carries the same day-of-month due date as the previous month's template.

Animus works one month ahead: a job running 2026-07-01 creates the AUGUST 2026
cards. The August 1 run creates September. And so on.

The TEMPLATE dict below is the source of truth, frozen from the cleaned-up
July 2026 cards (after dedupe). Edit it here if cadence/items change — Pat
re-reads it on the next monthly run.
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

log = logging.getLogger("pat.trello_monthly")

TRELLO_KEY = os.environ.get("TRELLO_KEY", "")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN", "")

TEAM_BOARD = "6a2c670ebdaa073b45f8733b"
LIST_MAIN_CARDS = "6a2c670ed17f298ac9b5b1ce"
LIST_DONE = "6a2c671089ad31be45777fc8"

# Label IDs are looked up dynamically by name on first call and cached for
# subsequent runs (since they don't change month-to-month).
_LABEL_CACHE: dict[str, str] | None = None

CLIENT_LABEL = {
    "AYS Rentals": "AYS Rentals",
    "Bixby Funeral Services": "Bixby",
    "D&L Oil Tools": "D&L Oil Tools",
    "Eggert Law": "Eggert Law",
    "Oklahomans for Immigrants": "Oklahomans for Immigrants",
    "Rivercrest Cremation": "Rivercrest Cremation",
    "Ten Seven Security": "Ten Seven Security",
    # R&R Roofing removed 2026-06-24 — no longer a recurring client
}

# Service line ordering on each client's card
SERVICE_ORDER = ["Blogs", "SEO", "GMB Management", "GMB Management — Tulsa",
                 "GMB Management — Oklahoma City", "Social Posts", "Reels", "Ads"]

# Trello member IDs — every monthly marketing card gets both Kyle and Jon assigned
# so each shows up in both their "my cards" filters.
MEMBER_ID_KYLE = "696e733530da2d91e9d9cd0a"
MEMBER_ID_JON  = "65007a903b5acfc291e26a8e"
DEFAULT_CARD_MEMBERS = [MEMBER_ID_KYLE, MEMBER_ID_JON]

# Per-service ownership map. Trello free plan doesn't support per-checkItem
# member assignment, so we append (Kyle) / (Jon) to the item name instead.
# Derived from canonical Asana subtask assignees (June 2026).
SERVICE_OWNER: dict[str, dict[str, str]] = {
    "Blogs": {
        "Content written": "Jon",
        "Delivered": "Jon",
        "Scheduled": "Kyle",
    },
    "SEO": {
        "Pull keyword rankings + traffic snapshot for prior month": "Jon",
        "Technical health audit (broken links, page speed, schema, index)": "Jon",
        "On-page optimization (titles, meta, internal links on top pages)": "Jon",
        "Backlink monitoring + outreach (new opportunities)": "Jon",
        "Send monthly SEO report to client": "Jon",
    },
    "GMB Management": {
        "Post weekly GMB updates (events, offers, photos)": "Kyle",
        "Refresh GMB Q&A + photos / business info": "Kyle",
        "Reply to all new reviews + monitor Q&A": "Kyle",
        "Monthly insights pull (calls, direction requests, searches) for client report": "Kyle",
        # R&R Roofing short-name variants
        "Post weekly GMB updates": "Kyle",
        "Refresh GMB Q&A + photos": "Kyle",
        "Reply to all new reviews": "Kyle",
        "Monthly insights pull for client report": "Kyle",
    },
    "Social Posts": {
        "Content written": "Kyle",  # D&L override below
        "Designs done": "Jon",
        "Final review & send": "Jon",
        "Revisions made": "Kyle",
        "Posts scheduled": "Kyle",
    },
    "Reels": {
        "Schedule shoot": "Kyle",
        "Caption written": "Kyle",
        "Video shot and edited": "Kyle",
        "Final review & send": "Jon",
        "Revisions made": "Kyle",
        "Posts scheduled": "Kyle",
    },
}

# Per-client overrides for items that deviate from the canonical map.
PER_CLIENT_OVERRIDES: dict[tuple[str, str, str], str] = {
    # D&L Oil Tools — Jon writes D&L copy himself
    ("D&L Oil Tools", "Social Posts", "Content written"): "Jon",
}


def _resolve_owner(client: str, service: str, item_name: str) -> str | None:
    """Return 'Kyle' / 'Jon' / None for a given item. GMB variants normalize to GMB Management."""
    canonical_service = "GMB Management" if service.startswith("GMB Management") else service
    override = PER_CLIENT_OVERRIDES.get((client, canonical_service, item_name))
    if override:
        return override
    return SERVICE_OWNER.get(canonical_service, {}).get(item_name)


# Per-client design + asset links. Injected into every monthly marketing card
# Pat creates. Edit when a client's Figma / Frame URLs change.
CLIENT_LINKS: dict[str, dict[str, str]] = {
    "AYS Rentals": {
        "figma_main":  "https://www.figma.com/design/JpBvpztrcK0lbf2i59n3cx/%5BAD%5D-AYS-Rentals",
        "figma_focus": "https://www.figma.com/design/JpBvpztrcK0lbf2i59n3cx/-AD--AYS-Rentals?node-id=4292-10",
    },
    "Bixby Funeral Services": {
        "figma_main":  "https://www.figma.com/design/Dy5NHZPPEmHQ8MGG9CLRvj/%5BAD%5D-Bixby-Funeral-Home",
        "figma_focus": "https://www.figma.com/design/Dy5NHZPPEmHQ8MGG9CLRvj/-AD--Bixby-Funeral-Home?node-id=4139-2",
    },
    "D&L Oil Tools": {
        "figma_main":  "https://www.figma.com/design/lV7ILeRet4rS5p1KjiD5P2/%5BAD%5D-D%26L-Oil-Tools---Socials",
        "figma_focus": "https://www.figma.com/design/lV7ILeRet4rS5p1KjiD5P2/-AD--D-L-Oil-Tools---Socials?node-id=1735-2",
    },
    "Eggert Law": {
        "figma_main":  "https://www.figma.com/design/wnQEhGi4j4xjPAWSSiJaKE/%5BAD%5D-Eggert-Family-Law---Socials",
        "figma_focus": "https://www.figma.com/proto/wnQEhGi4j4xjPAWSSiJaKE/-AD--Eggert-Family-Law---Socials?page-id=2248%3A2&node-id=2248-32",
    },
    "Oklahomans for Immigrants": {
        "figma_main":  "https://www.figma.com/design/ZW6BorHtnwXjLSf1vaEjfQ/%5BAD%5D-Oklahomans-for-Immigrants",
        "figma_focus": "https://www.figma.com/design/ZW6BorHtnwXjLSf1vaEjfQ/-AD--Oklahomans-for-Immigrants?node-id=7436-2",
    },
    # R&R Roofing removed 2026-06-24 — no longer a recurring client
    "Rivercrest Cremation": {
        "figma_main":  "https://www.figma.com/design/fF55BHE1qAfnw4AV3fJM08/%5BAD%5D-Rivercrest-Cremation---Socials",
        "figma_focus": "https://www.figma.com/design/fF55BHE1qAfnw4AV3fJM08/-AD--Rivercrest-Cremation---Socials?node-id=4711-2",
    },
    "Ten Seven Security": {
        "figma_main":  "https://www.figma.com/design/UjiZqZiYrnR1dLSuTA2dRC/%5BAD%5D-TenSeven-Security-%7C-2025",
        "figma_focus": "https://www.figma.com/design/UjiZqZiYrnR1dLSuTA2dRC/-AD--Ten-Seven-Security-%7C-2025?node-id=40000223-2",
    },
}


def _client_link_section(client: str) -> str:
    """Format the design + asset link block injected into each monthly card's desc."""
    links = CLIENT_LINKS.get(client)
    if not links:
        return ""
    lines = ["## 🔗 Design + asset links", ""]
    if links.get("figma_main"):
        lines.append(f"🎨 **Figma (main):** {links['figma_main']}")
    if links.get("figma_focus"):
        lines.append(f"🎨 **Figma (focus / current month):** {links['figma_focus']}")
    if links.get("frame"):
        lines.append(f"🎬 **Frame.io:** {links['frame']}")
    return "\n".join(lines)

# Frozen template extracted from the deduped July 2026 cards. day = day-of-month
# the item is due in the production month (clamped to last day if month is shorter).
# day = None → checkItem created without a due date.
TEMPLATE: dict[str, dict[str, list[dict]]] = {
    "AYS Rentals": {
        "Blogs": [
            {"name": "Content written", "day": 15},
            {"name": "Delivered", "day": 20},
            {"name": "Scheduled", "day": 30},
        ],
        "SEO": [
            {"name": "Pull keyword rankings + traffic snapshot for prior month", "day": None},
            {"name": "Technical health audit (broken links, page speed, schema, index)", "day": None},
            {"name": "On-page optimization (titles, meta, internal links on top pages)", "day": None},
            {"name": "Backlink monitoring + outreach (new opportunities)", "day": None},
            {"name": "Send monthly SEO report to client", "day": None},
        ],
        "GMB Management — Tulsa": [
            {"name": "Post weekly GMB updates (events, offers, photos)", "day": 7},
            {"name": "Refresh GMB Q&A + photos / business info", "day": 22},
            {"name": "Reply to all new reviews + monitor Q&A", "day": 26},
            {"name": "Monthly insights pull (calls, direction requests, searches) for client report", "day": 28},
        ],
        "GMB Management — Oklahoma City": [
            {"name": "Post weekly GMB updates (events, offers, photos)", "day": 7},
            {"name": "Refresh GMB Q&A + photos / business info", "day": 22},
            {"name": "Reply to all new reviews + monitor Q&A", "day": 26},
            {"name": "Monthly insights pull (calls, direction requests, searches) for client report", "day": 28},
        ],
        "Social Posts": [
            {"name": "Content written", "day": 10},
            {"name": "Designs done", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
        "Reels": [
            {"name": "Schedule shoot", "day": 3},
            {"name": "Caption written", "day": 12},
            {"name": "Video shot and edited", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
    },
    "Bixby Funeral Services": {
        "Blogs": [
            {"name": "Content written", "day": 15},
            {"name": "Delivered", "day": 20},
            {"name": "Scheduled", "day": 30},
        ],
        "SEO": [
            {"name": "Pull keyword rankings + traffic snapshot for prior month", "day": None},
            {"name": "Technical health audit (broken links, page speed, schema, index)", "day": None},
            {"name": "On-page optimization (titles, meta, internal links on top pages)", "day": None},
            {"name": "Backlink monitoring + outreach (new opportunities)", "day": None},
            {"name": "Send monthly SEO report to client", "day": None},
        ],
        "GMB Management": [
            {"name": "Post weekly GMB updates (events, offers, photos)", "day": 7},
            {"name": "Refresh GMB Q&A + photos / business info", "day": 22},
            {"name": "Reply to all new reviews + monitor Q&A", "day": 26},
            {"name": "Monthly insights pull (calls, direction requests, searches) for client report", "day": 28},
        ],
        "Social Posts": [
            {"name": "Content written", "day": 10},
            {"name": "Designs done", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
        # Bixby Reels removed 2026-06-24 — recurring video discontinued
    },
    "D&L Oil Tools": {
        "Social Posts": [
            {"name": "Content written", "day": 10},
            {"name": "Designs done", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
    },
    "Eggert Law": {
        "Blogs": [
            {"name": "Content written", "day": 15},
            {"name": "Delivered", "day": 20},
            {"name": "Scheduled", "day": 30},
        ],
        "SEO": [
            {"name": "Pull keyword rankings + traffic snapshot for prior month", "day": None},
            {"name": "Technical health audit (broken links, page speed, schema, index)", "day": None},
            {"name": "On-page optimization (titles, meta, internal links on top pages)", "day": None},
            {"name": "Backlink monitoring + outreach (new opportunities)", "day": None},
            {"name": "Send monthly SEO report to client", "day": None},
        ],
        "GMB Management": [
            {"name": "Post weekly GMB updates (events, offers, photos)", "day": 7},
            {"name": "Refresh GMB Q&A + photos / business info", "day": 22},
            {"name": "Reply to all new reviews + monitor Q&A", "day": 26},
            {"name": "Monthly insights pull (calls, direction requests, searches) for client report", "day": 28},
        ],
        "Social Posts": [
            {"name": "Content written", "day": 10},
            {"name": "Designs done", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
        "Reels": [
            {"name": "Schedule shoot", "day": 3},
            {"name": "Caption written", "day": 12},
            {"name": "Video shot and edited", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
    },
    "Oklahomans for Immigrants": {
        "Social Posts": [
            {"name": "Content written", "day": 10},
            {"name": "Designs done", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
        "Reels": [
            {"name": "Schedule shoot", "day": 3},
            {"name": "Caption written", "day": 12},
            {"name": "Video shot and edited", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
    },
    # R&R Roofing removed 2026-06-24 — no longer a recurring client
    "Rivercrest Cremation": {
        "Social Posts": [
            {"name": "Content written", "day": 10},
            {"name": "Designs done", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
    },
    "Ten Seven Security": {
        "Blogs": [
            {"name": "Content written", "day": 15},
            {"name": "Delivered", "day": 20},
            {"name": "Scheduled", "day": 30},
        ],
        "SEO": [
            {"name": "Pull keyword rankings + traffic snapshot for prior month", "day": None},
            {"name": "Technical health audit (broken links, page speed, schema, index)", "day": None},
            {"name": "On-page optimization (titles, meta, internal links on top pages)", "day": None},
            {"name": "Backlink monitoring + outreach (new opportunities)", "day": None},
            {"name": "Send monthly SEO report to client", "day": None},
        ],
        "GMB Management": [
            {"name": "Post weekly GMB updates (events, offers, photos)", "day": 7},
            {"name": "Refresh GMB Q&A + photos / business info", "day": 22},
            {"name": "Reply to all new reviews + monitor Q&A", "day": 26},
            {"name": "Monthly insights pull (calls, direction requests, searches) for client report", "day": 28},
        ],
        "Social Posts": [
            {"name": "Content written", "day": 10},
            {"name": "Designs done", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
        "Reels": [
            {"name": "Schedule shoot", "day": 3},
            {"name": "Caption written", "day": 12},
            {"name": "Video shot and edited", "day": 17},
            {"name": "Final review & send", "day": 20},
            {"name": "Revisions made", "day": 28},
            {"name": "Posts scheduled", "day": 30},
        ],
    },
}


# ─── Trello HTTP helpers ──────────────────────────────────────────────────────

def _trello_get(path: str, params: dict | None = None):
    base = {"key": TRELLO_KEY, "token": TRELLO_TOKEN}
    if params:
        base.update({k: str(v) for k, v in params.items()})
    url = f"https://api.trello.com/1{path}?" + urllib.parse.urlencode(base)
    for _ in range(3):
        try:
            return json.loads(urllib.request.urlopen(url, timeout=30).read())
        except Exception:
            time.sleep(1)
    return None


def _trello_post_form(path: str, form: dict):
    form = dict(form)
    form["key"] = TRELLO_KEY
    form["token"] = TRELLO_TOKEN
    body = urllib.parse.urlencode({k: str(v) for k, v in form.items()}).encode()
    req = urllib.request.Request(
        f"https://api.trello.com/1{path}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    for _ in range(3):
        try:
            r = urllib.request.urlopen(req, timeout=30).read()
            return json.loads(r) if r else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2)
                continue
            return {"error": e.code, "detail": e.read().decode()[:200]}
        except Exception:
            time.sleep(1)
    return {"error": "retry exhausted"}


def _label_id_for(client_name: str) -> str:
    global _LABEL_CACHE
    if _LABEL_CACHE is None:
        labels = _trello_get(f"/boards/{TEAM_BOARD}/labels") or []
        _LABEL_CACHE = {l["name"]: l["id"] for l in labels if l.get("name")}
    label_name = CLIENT_LABEL.get(client_name, client_name)
    return _LABEL_CACHE.get(label_name, "")


# ─── Date helpers ─────────────────────────────────────────────────────────────

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _next_month_after(today: datetime.date) -> tuple[int, int]:
    """Returns (year, month) for the month AFTER `today`.
    Animus works one month ahead — running on day-1 of month N produces cards for month N+1."""
    if today.month == 12:
        return today.year + 1, 1
    return today.year, today.month + 1


def _clamp_day(year: int, month: int, day: int) -> int:
    """Some months don't have 30/31 days. Clamp to the last valid day."""
    last = calendar.monthrange(year, month)[1]
    return min(day, last)


# GMB tasks are WEEKLY (every Friday). Each Friday, Kyle gets one checklist
# item per work area — that way every Friday spells out exactly what to do.
# Plus a single end-of-month item for the monthly insights pull.
GMB_WEEKLY_ITEMS = [
    "Post GMB updates (events, offers, photos)",
    "Reply to all new reviews + monitor Q&A",
    "Refresh GMB Q&A + photos / business info if needed",
]
GMB_MONTHLY_END_ITEM = (
    "Monthly insights pull (calls, direction requests, searches) for client report"
)
_MONTH_ABBREV = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _production_month(publish_year: int, publish_month: int) -> tuple[int, int]:
    """Animus works one month ahead. The CARD is named for the publish month;
    the WORK happens in the prior month. e.g., the 'August 2026 Marketing' card
    has subtasks dated in July 2026."""
    if publish_month == 1:
        return publish_year - 1, 12
    return publish_year, publish_month - 1


def _gmb_friday_items(publish_year: int, publish_month: int) -> list[dict]:
    """Generate the full GMB checklist for the PRODUCTION month (one month before
    the publish month):
      - N Fridays × len(GMB_WEEKLY_ITEMS) weekly items
      - + 1 monthly insights pull on the last day of the production month
    Always Kyle. Each item gets its own due date."""
    prod_year, prod_month = _production_month(publish_year, publish_month)
    last_day = calendar.monthrange(prod_year, prod_month)[1]
    abbrev = _MONTH_ABBREV[prod_month - 1]
    items = []
    for day in range(1, last_day + 1):
        if datetime.date(prod_year, prod_month, day).weekday() == 4:  # 4 = Friday
            for wk_item in GMB_WEEKLY_ITEMS:
                items.append({
                    "name": f"{day} {abbrev} — {wk_item}",
                    "day": day,
                    "_prod_year": prod_year,
                    "_prod_month": prod_month,
                    "_gmb_kyle": True,
                })
    # End-of-production-month monthly insights pull
    items.append({
        "name": f"{last_day} {abbrev} — {GMB_MONTHLY_END_ITEM}",
        "day": last_day,
        "_prod_year": prod_year,
        "_prod_month": prod_month,
        "_gmb_kyle": True,
    })
    return items


def _normalize_card_name(name: str) -> str:
    """For idempotency: lowercase + collapse whitespace so 'Ten Seven Security'
    matches 'TenSeven Security' (the kind of human/migration spacing drift
    that produced a dupe in June 2026)."""
    return "".join(name.lower().split())


# ─── Main entry point ─────────────────────────────────────────────────────────

def _move_prior_month_to_done(year: int, month: int) -> int:
    """Move any cards on MAIN CARDS named for an EARLIER month than (year, month)
    to the DONE list. Preserves the card with all checklist history.
    Returns the count of cards moved."""
    moved = 0
    existing = _trello_get(f"/lists/{LIST_MAIN_CARDS}/cards", {"fields": "name"}) or []
    # Card name pattern: "Client — Month YYYY Marketing"
    for c in existing:
        name = c["name"]
        if "Marketing" not in name:
            continue
        # Parse YYYY out of the name
        for token in name.split():
            if token.isdigit() and len(token) == 4:
                card_year = int(token)
                break
        else:
            continue
        # Parse month
        card_month = None
        for i, m in enumerate(MONTH_NAMES, 1):
            if f" {m} " in name or name.endswith(m):
                card_month = i; break
        if not card_month:
            continue
        if (card_year, card_month) < (year, month):
            r = _trello_post_form(f"/cards/{c['id']}", {"idList": LIST_DONE})
            if r and not r.get("error"):
                moved += 1
                log.info("  → moved '%s' to DONE", name)
    return moved


def create_marketing_cards_for(year: int, month: int) -> dict:
    """Create the 8 monthly marketing cards for the given year+month.
    Also moves any prior-month marketing cards from MAIN CARDS to DONE.
    Idempotent: if a card with the same name already exists active on MAIN CARDS,
    it's left alone and skipped (NOT recreated).
    """
    month_name = MONTH_NAMES[month - 1]
    log.info("Creating marketing cards for %s %d", month_name, year)

    # First: sweep prior-month cards to DONE so MAIN CARDS only shows the active month.
    moved_to_done = _move_prior_month_to_done(year, month)
    if moved_to_done:
        log.info("Moved %d prior-month cards to DONE", moved_to_done)

    # Idempotency: check ALL cards on TEAM BOARD (every list + archived) for the
    # target card name. Marketing cards may have been moved by humans into
    # IN PRODUCTION THIS WEEK / WITH CLIENT / DONE — don't recreate a duplicate.
    # Compare on NORMALIZED names so 'Ten Seven' and 'TenSeven' don't dupe.
    open_cards = _trello_get(f"/boards/{TEAM_BOARD}/cards", {"fields": "name"}) or []
    closed_cards = _trello_get(f"/boards/{TEAM_BOARD}/cards/closed", {"fields": "name"}) or []
    existing_names = ({_normalize_card_name(c["name"]) for c in open_cards}
                      | {_normalize_card_name(c["name"]) for c in closed_cards})

    last_day = calendar.monthrange(year, month)[1]
    card_due_iso = f"{year}-{month:02d}-{last_day:02d}T17:00:00.000Z"

    created, skipped, errors = [], [], []

    for client, services in TEMPLATE.items():
        title = f"{client} — {month_name} {year} Marketing"
        if _normalize_card_name(title) in existing_names:
            log.info("  · '%s' already exists (matched by normalized name), skipping", title)
            skipped.append(client)
            continue

        prod_y, prod_m = _production_month(year, month)
        prod_month_name = MONTH_NAMES[prod_m - 1]
        desc_parts = [
            f"Monthly marketing — **{client}**",
            f"📤 **Publish month:** {month_name} {year}",
            f"🔨 **Production month:** {prod_month_name} {prod_y} *(when the work happens — checklist due dates live here)*",
            "",
            f"Each checklist below = one service line. Check items off as the work ships.",
            f"Pat auto-creates next month's card on the 1st at 6 AM CT.",
        ]
        link_block = _client_link_section(client)
        if link_block:
            desc_parts.extend(["", link_block])
        desc = "\n".join(desc_parts)
        new_card = _trello_post_form("/cards", {
            "idList": LIST_MAIN_CARDS,
            "name": title,
            "desc": desc,
            "due": card_due_iso,
            "idLabels": _label_id_for(client),
            "idMembers": ",".join(DEFAULT_CARD_MEMBERS),  # Kyle + Jon
            "pos": "top",
        })
        if new_card.get("error") or not new_card.get("id"):
            errors.append((client, "create card", new_card))
            continue
        card_id = new_card["id"]
        log.info("  ✓ %s → %s", client, new_card.get("shortUrl", card_id))

        ordered_services = sorted(services.keys(),
            key=lambda s: SERVICE_ORDER.index(s) if s in SERVICE_ORDER else 99)
        for service in ordered_services:
            # GMB Management (including Tulsa / Oklahoma City variants) → weekly Fridays.
            if service.startswith("GMB Management"):
                items = _gmb_friday_items(year, month)
            else:
                items = services[service]
            if not items:
                continue
            cl = _trello_post_form(f"/cards/{card_id}/checklists", {"name": service})
            if not cl.get("id"):
                errors.append((client, f"checklist {service}", cl))
                continue
            cl_id = cl["id"]
            for it in items:
                # Append (Kyle) / (Jon) to item name AND assign as a real member
                # (works on Standard/Premium plans — silently no-ops on Free).
                base_name = it["name"]
                if it.get("_gmb_kyle"):
                    # GMB items already have date prefix baked into name; just tag owner
                    owner = "Kyle"
                    display_name = f"{base_name} (Kyle)"
                else:
                    owner = _resolve_owner(client, service, base_name)
                    display_name = f"{base_name} ({owner})" if owner else base_name
                ci_params = {"name": display_name[:500]}
                if it.get("day"):
                    # Subtask due dates live in the PRODUCTION month (one before
                    # the publish month the card is named for). GMB items pre-
                    # compute the production month; non-GMB items derive it here.
                    if it.get("_gmb_kyle"):
                        due_y, due_m = it["_prod_year"], it["_prod_month"]
                    else:
                        due_y, due_m = _production_month(year, month)
                    due_day = _clamp_day(due_y, due_m, it["day"])
                    ci_params["due"] = f"{due_y}-{due_m:02d}-{due_day:02d}T17:00:00.000Z"
                if owner == "Kyle":
                    ci_params["idMember"] = MEMBER_ID_KYLE
                elif owner == "Jon":
                    ci_params["idMember"] = MEMBER_ID_JON
                _trello_post_form(f"/checklists/{cl_id}/checkItems", ci_params)
                time.sleep(0.05)
        created.append({"client": client, "card_id": card_id,
                        "url": new_card.get("shortUrl", "")})
        time.sleep(0.3)

    return {"month": f"{month_name} {year}", "created": created,
            "skipped": skipped, "errors": errors, "moved_to_done": moved_to_done}


def create_next_month_marketing_cards() -> dict:
    """Run on the 1st of each month. Creates cards for the FOLLOWING month
    (Animus works one month ahead)."""
    today = datetime.date.today()
    year, month = _next_month_after(today)
    return create_marketing_cards_for(year, month)
