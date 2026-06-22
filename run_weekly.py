#!/usr/bin/env python3
"""Weekly cron entrypoint — creates the Animus marketing email card for this week.

Fly fires this every Monday morning (weekly schedule). Idempotent — re-runs in
the same week skip without creating a duplicate.
"""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("weekly-cron")


def main() -> int:
    if not os.environ.get("TRELLO_KEY") or not os.environ.get("TRELLO_TOKEN"):
        log.error("TRELLO_KEY and TRELLO_TOKEN must be set")
        return 2

    from weekly_email import create_weekly_email_card
    log.info("Weekly cron firing — creating Animus marketing email card")
    try:
        result = create_weekly_email_card()
    except Exception:
        log.exception("Weekly email card creation raised")
        return 1

    if result.get("created"):
        log.info("✓ Created: %s → %s", result.get("title"), result.get("url"))
    else:
        log.info("· Skipped: %s (%s)", result.get("title"), result.get("reason"))
    return 0
