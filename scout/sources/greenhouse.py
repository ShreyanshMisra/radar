"""Greenhouse job boards (boards-api.greenhouse.io)."""
from __future__ import annotations

from typing import Optional

from ..models import Job
from ..util import get_json, iso_date, strip_html

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def fetch(s, company: dict, ctx) -> list[Job]:
    token = company["token"]
    data = get_json(s, API.format(token=token), timeout=60)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        out.append(Job.make(
            f"greenhouse:{token}", str(j["id"]),
            company=company["name"], title=j.get("title", ""), url=j.get("absolute_url", ""),
            locations=[loc],
            posted_at=iso_date(j.get("first_published")), posted_source="Greenhouse first-published date",
            detail={"kind": "greenhouse", "token": token, "id": str(j["id"])},
        ))
    return out


def describe(s, token: str, job_id: str) -> Optional[str]:
    data = get_json(s, f"{API.format(token=token)}/{job_id}", timeout=30)
    return strip_html(data.get("content")) or None
