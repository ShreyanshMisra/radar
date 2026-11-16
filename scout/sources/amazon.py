"""amazon.jobs search JSON, newest first, per region country."""
from __future__ import annotations

from ..models import Job
from ..util import age_days, get_json, long_date, strip_html

API = "https://www.amazon.jobs/en/search.json"
PAGE = 100
MAX = 600


def fetch(s, company: dict, ctx) -> list[Job]:
    out: dict[str, Job] = {}
    for region in ctx.regions.values():
        code = region.raw.get("amazon_country")
        if not code:
            continue
        for offset in range(0, MAX, PAGE):
            data = get_json(s, API, params={"normalized_country_code[]": code, "result_limit": PAGE,
                                            "offset": offset, "sort": "recent"}, timeout=60)
            rows = data.get("jobs", [])
            recent = 0
            for j in rows:
                posted = long_date(j.get("posted_date"))
                age = age_days(posted, ctx.today)
                if age is not None and age > ctx.lookback_days:
                    continue
                recent += 1
                desc = "\n\n".join(p for p in (
                    strip_html(j.get("description")),
                    "Basic qualifications:\n" + strip_html(j.get("basic_qualifications")),
                    "Preferred qualifications:\n" + strip_html(j.get("preferred_qualifications")),
                ) if p.strip())
                out[j["job_path"]] = Job.make(
                    "amazon", str(j.get("id_icims") or j["job_path"]),
                    company=company["name"], title=j.get("title", ""),
                    url="https://www.amazon.jobs" + j["job_path"],
                    locations=[j.get("normalized_location") or j.get("location") or ""],
                    country_hints=sorted(region.countries)[:1],
                    posted_at=posted, posted_source="amazon.jobs posted date",
                    description=desc,
                    meta={"entity": j.get("company_name"), "category": j.get("job_category")},
                )
            if len(rows) < PAGE or not recent:
                break
    return list(out.values())
