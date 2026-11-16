"""Post digests as GitHub Issues. The token comes from the environment (Actions) or `gh`."""
from __future__ import annotations

import os
import subprocess
from typing import Optional

import requests

from .util import log

API = "https://api.github.com"


def token() -> Optional[str]:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def repo(cfg: dict) -> Optional[str]:
    return os.environ.get("GITHUB_REPOSITORY") or (cfg.get("github") or {}).get("repo") or None


class Issues:
    def __init__(self, repo_name: str, tok: str):
        self.repo = repo_name
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def _call(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{API}/repos/{self.repo}{path}", timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    def create(self, title: str, body: str, labels: list[str]) -> str:
        # GitHub caps issue bodies at 65536 characters.
        if len(body) > 65000:
            body = body[:64900] + "\n\n_…truncated: the digest exceeded GitHub's issue size limit._"
        return self._call("POST", "/issues", json={"title": title, "body": body, "labels": labels})["html_url"]

    def close_older(self, label: str, keep_days: int, today) -> int:
        """Close digest issues older than keep_days so the open list stays an inbox."""
        from datetime import date
        closed = 0
        for it in self._call("GET", "/issues", params={"labels": label, "state": "open", "per_page": 100}):
            if "pull_request" in it:
                continue
            if (today - date.fromisoformat(it["created_at"][:10])).days > keep_days:
                try:
                    self._call("PATCH", f"/issues/{it['number']}", json={"state": "closed", "state_reason": "completed"})
                    closed += 1
                except requests.HTTPError as e:
                    log.warning("could not close issue #%s: %s", it["number"], e)
        return closed
