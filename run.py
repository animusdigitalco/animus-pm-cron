#!/usr/bin/env python3
"""
Animus PM cron entrypoint.

Runs on a Fly scheduled machine — Fly fires this once a month (`monthly` schedule).
The script calls Pat's `create_next_month_marketing_cards()`, which:

  - Creates 8 monthly marketing cards on TEAM BOARD's MAIN CARDS list
  - Each card has per-service checklists with Kyle/Jon assigned per item
  - Each card has Figma + Frame links injected per client
  - Moves prior month's cards from MAIN CARDS → DONE

Idempotent: re-running won't recreate cards that already exist for the target
month. Safe to invoke manually via `fly machine run --command python run.py`.
"""
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("pm-cron")


def main() -> int:
    if not os.environ.get("TRELLO_KEY") or not os.environ.get("TRELLO_TOKEN"):
        log.error("TRELLO_KEY and TRELLO_TOKEN must be set as Fly secrets")
        return 2

    log.info("Animus PM cron firing — invoking monthly card creation")
    # Import after env check so module-level secret references resolve correctly
    from trello_monthly_cards import create_next_month_marketing_cards

    try:
        result = create_next_month_marketing_cards()
    except Exception:
        log.exception("Monthly card creation raised")
        return 1

    log.info("Result: month=%s | created=%d | skipped=%d | errors=%d | moved_to_done=%d",
             result.get("month"),
             len(result.get("created", [])),
             len(result.get("skipped", [])),
             len(result.get("errors", [])),
             result.get("moved_to_done", 0))

    for c in result.get("created", []):
        log.info("  ✓ %s → %s", c["client"], c["url"])
    for client in result.get("skipped", []):
        log.info("  · %s (already existed, skipped)", client)
    for client, kind, err in result.get("errors", []):
        log.error("  ✗ %s (%s): %s", client, kind, str(err)[:200])

    return 0 if not result.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
