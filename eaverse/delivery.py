"""Deliver committed outbox events through Telegram or a GitHub issue."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen



def gh(*args):
    proc = subprocess.run(["gh", *args], check=True, capture_output=True, text=True, timeout=60)
    return proc.stdout.strip()


def telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return None
    req = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                  data=urlencode({"chat_id": chat, "text": message}).encode())
    try:
        with urlopen(req, timeout=12) as response:
            sent = json.load(response)
        return f"telegram:{sent['result']['message_id']}" if sent["ok"] else None
    except (URLError, TimeoutError, ValueError, KeyError):
        return None


def issue(key, message):
    repo = os.environ["GITHUB_REPOSITORY"]
    title = f"[Eaverse] {key}"
    # Public repository fallback contains only market-data and simulated research status.
    prior = json.loads(gh("issue", "list", "-R", repo, "--state", "all", "--search",
                         f'"{title}" in:title', "--json", "title,url", "--limit", "40"))
    existing = next((item["url"] for item in prior if item["title"] == title), None)
    return existing or gh("issue", "create", "-R", repo, "--title", title, "--body", message)


def deliver(db):
    rows = list(db.execute("SELECT id,message FROM outbox WHERE receipt IS NULL ORDER BY rowid"))
    for row in rows[:20]:
        receipt = telegram(row["message"] + f"\nOlay: {row['id']}")
        if receipt is None:
            if not os.getenv("GITHUB_REPOSITORY"):
                continue
            receipt = issue(row["id"], row["message"])
        with db:
            db.execute("UPDATE outbox SET receipt=? WHERE id=?", (receipt, row["id"]))


if __name__ == "__main__":
    from .engine import connect
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="runtime/research.sqlite")
    args = parser.parse_args()
    with connect(args.db) as db:
        deliver(db)
