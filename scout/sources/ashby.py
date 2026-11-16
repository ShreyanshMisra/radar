"""Ashby job boards (api.ashbyhq.com/posting-api)."""
from __future__ import annotations

from typing import Optional

from ..models import Job
from ..util import get_json, iso_date

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


def _country(addr: Optional[dict]) -> Optional[str]:
    return ((addr or {}).get("postalAddress") or {}).get("addressCountry")


def fetch(s, company: dict, ctx) -> list[Job]:
    token = company["token"]
    data = get_json(s, API.format(token=token), params={"includeCompensation": "true"}, timeout=60)
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        secondary = j.get("secondaryLocations") or []
        hints = [_country(j.get("address"))] + [_country(x.get("address")) for x in secondary]
        hints = [h for h in hints if h]
        # A partial set of countries would wrongly rule regions out, so use hints only when
        # every listed location carries one.
        if len(hints) != 1 + len(secondary):
            hints = []
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        out.append(Job.make(
            f"ashby:{token}", j["id"],
            company=company["name"], title=j.get("title", ""), url=j.get("jobUrl", ""),
            locations=[j.get("location")] + [x.get("location") for x in secondary],
            country_hints=hints,
            posted_at=iso_date(j.get("publishedAt")), posted_source="Ashby published date",
            workplace=(j.get("workplaceType") or "").lower() or None,
            salary=comp if j.get("shouldDisplayCompensationOnJobPostings", True) else None,
            description=j.get("descriptionPlain"),
            meta={"employment": j.get("employmentType"), "department": j.get("department")},
        ))
    return out


def describe(s, token: str, job_id: str) -> Optional[str]:
    data = get_json(s, API.format(token=token), timeout=60)
    for j in data.get("jobs", []):
        if j.get("id") == job_id:
            return j.get("descriptionPlain")
    return None
