"""SmartRecruiters public postings API, queried per region country."""
from __future__ import annotations

from typing import Optional

from ..models import Job
from ..util import get_json, iso_date, strip_html

API = "https://api.smartrecruiters.com/v1/companies/{token}/postings"
MAX = 1000


def fetch(s, company: dict, ctx) -> list[Job]:
    token = company["token"]
    out: dict[str, Job] = {}
    for iso in ctx.iso2_countries:
        offset = 0
        while offset < MAX:
            data = get_json(s, API.format(token=token),
                            params={"limit": 100, "offset": offset, "country": iso.lower()}, timeout=60)
            items = data.get("content", [])
            for j in items:
                loc = j.get("location") or {}
                text = ", ".join(p for p in (loc.get("city"), loc.get("region"), (loc.get("country") or "").upper()) if p)
                out[j["id"]] = Job.make(
                    f"smartrecruiters:{token}", j["id"],
                    company=company["name"], title=j.get("name", ""),
                    url=f"https://jobs.smartrecruiters.com/{token}/{j['id']}",
                    locations=[text], country_hints=[loc.get("country") or iso],
                    posted_at=iso_date(j.get("releasedDate")), posted_source="SmartRecruiters release date",
                    workplace="remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else None),
                    detail={"kind": "smartrecruiters", "ref": j.get("ref")},
                    meta={"experience": (j.get("experienceLevel") or {}).get("label"),
                          "function": (j.get("function") or {}).get("label")},
                )
            offset += len(items)
            if not items or offset >= int(data.get("totalFound", 0)):
                break
    return list(out.values())


def describe(s, ref: str) -> Optional[str]:
    sections = (get_json(s, ref, timeout=30).get("jobAd") or {}).get("sections") or {}
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        sec = sections.get(key) or {}
        if sec.get("text"):
            parts.append(f"{sec.get('title') or key}\n{strip_html(sec['text'])}")
    return "\n\n".join(parts) or None
