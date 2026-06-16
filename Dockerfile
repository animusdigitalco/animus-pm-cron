FROM python:3.12-slim

WORKDIR /app

# CA bundles for HTTPS to Trello API
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pure-stdlib script — no pip install needed
COPY trello_monthly_cards.py run.py ./

CMD ["python", "-u", "run.py"]
