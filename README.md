# Animus PM Cron

Lightweight, no-LLM Fly.io app that runs Pat's monthly Trello marketing card
creation on a schedule. No Slack, no Anthropic, no watchers — just the cron.

## What it does

Once a month, fires `create_next_month_marketing_cards()` from
`trello_monthly_cards.py`:

- Creates 8 client monthly marketing cards on TEAM BOARD's MAIN CARDS list
- Each card has per-service checklists with Kyle/Jon assigned per item (real
  Trello member chips, since the workspace is on Premium)
- Each card has Figma + Frame links injected per client
- Moves the prior month's cards from MAIN CARDS → DONE

Idempotent — re-runs skip cards that already exist for the target month.

## Cost

Effectively $0/mo. The scheduled machine only runs ~30 seconds per month
on a shared-cpu-1x with 256MB RAM, well inside Fly's free tier.

## Files

- `run.py` — entrypoint, called by the scheduled machine
- `trello_monthly_cards.py` — the canonical template + create logic (shared with
  the full Pat codebase; edit there OR here, then sync)
- `Dockerfile` — minimal Python 3.12 slim, no pip deps (pure stdlib)
- `fly.toml` — Fly app config

## Setup (one-time)

```bash
fly launch --copy-config --no-deploy
fly secrets set TRELLO_KEY=<key> TRELLO_TOKEN=<token>
fly deploy
fly machine run --schedule monthly --region ord registry.fly.io/animus-pm-cron:latest
```

## Manual fire (for a specific month)

```bash
fly machine run --rm registry.fly.io/animus-pm-cron:latest --region ord
```

Or run locally:

```bash
TRELLO_KEY=<key> TRELLO_TOKEN=<token> python3 run.py
```

## When the template changes

Edit `trello_monthly_cards.py` here, push to GitHub, redeploy. (Or copy the
updated file from the `animus-pm-slack` repo if you've made the change there.)
