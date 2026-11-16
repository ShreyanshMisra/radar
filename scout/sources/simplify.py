"""Community-maintained new-grad lists published in the SimplifyJobs listings.json format."""
from __future__ import annotations

from ..models import Job
from ..util import age_days, get_json, iso_date

DEFAULT_URL = "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"


def fetch(s, company: dict, ctx) -> list[Job]:
    data = get_json(s, company.get("url") or DEFAULT_URL, timeout=90)
    out = []
    for j in data:
        if not j.get("active") or j.get("is_visible") is False or not j.get("url"):
            continue
        posted = iso_date(j.get("date_posted"))
        age = age_days(posted, ctx.today)
        if age is None or age > ctx.lookback_days:
            continue
        out.append(Job.make(
            "simplify", j["id"],
            company=j.get("company_name", ""), title=j.get("title", ""), url=j["url"].split("?utm_")[0],
            locations=j.get("locations") or [],
            posted_at=posted, posted_source="Simplify new-grad list date",
            meta={"list_sponsorship": j.get("sponsorship"), "category": j.get("category")},
        ))
    return out
