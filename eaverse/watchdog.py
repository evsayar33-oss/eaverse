"""Alert on persistent Actions failures or a missing research heartbeat."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from .delivery import gh, issue, telegram


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    endpoint = f"repos/{repo}/actions/workflows/research.yml/runs?branch=main&per_page=30"
    runs = json.loads(gh("api", endpoint))["workflow_runs"]
    completed = [run for run in runs if run["status"] == "completed"]
    last_good = next((run for run in completed if run["conclusion"] == "success"), None)
    now = datetime.now(UTC)
    failed_twice = len(completed) >= 2 and all(run["conclusion"] in {"failure", "timed_out"}
                                              for run in completed[:2])
    stale = last_good and now - datetime.fromisoformat(last_good["updated_at"]) > timedelta(hours=20)
    initialized = runs and now - datetime.fromisoformat(runs[-1]["created_at"]) > timedelta(hours=24)
    if not (failed_twice or stale or (initialized and not last_good)):
        return
    key = f"workflow-health:{last_good['id'] if last_good else 'bootstrap'}"
    title = f"[Eaverse] {key}"
    existing = json.loads(gh("issue", "list", "-R", repo, "--state", "all", "--search",
                            f'"{title}" in:title', "--json", "title", "--limit", "40"))
    if any(item["title"] == title for item in existing):
        return
    message = ("⚠️ Eaverse · araştırma çalışması kesintisi\n"
               "Ardışık iş akışı hatası veya 20 saati aşan başarılı çalışma gecikmesi.\n"
               f"https://github.com/{repo}/actions/workflows/research.yml")
    receipt = telegram(message)
    issue(key, message + ("\nTelegram bildirimi gönderildi." if receipt else ""))


if __name__ == "__main__":
    main()
