"""Oracle Recruiting Cloud career sites (the /hcmRestApi candidate-experience endpoint).

Common among large Gulf employers and banks. Listings come newest first with a real posting
date and ISO country codes, so paging stops at the lookback window.
"""
from __future__ import annotations

from typing import Optional

from ..models import Job
from ..util import age_days, get_json, strip_html

PAGE = 50
MAX_PAGES = 12
_API = "https://{host}/hcmRestApi/resources/latest/"


def fetch(s, company: dict, ctx) -> list[Job]:
    o = company["oracle"]
    out = []
    for page in range(MAX_PAGES):
        url = (_API.format(host=o["host"]) + "recruitingCEJobRequisitions?onlyData=true"
               "&expand=requisitionList.secondaryLocations"
               f"&finder=findReqs;siteNumber={o['site']},limit={PAGE},offset={page * PAGE},sortBy=POSTING_DATES_DESC")
        rows = (get_json(s, url, timeout=45).get("items") or [{}])[0].get("requisitionList") or []
        recent = 0
        for r in rows:
            age = age_days(r.get("PostedDate"), ctx.today)
            if age is not None and age > ctx.lookback_days:
                continue
            recent += 1
            secondary = r.get("secondaryLocations") or []
            out.append(Job.make(
                f"oracle:{o['host'].split('.')[0]}/{o['site']}", str(r["Id"]),
                company=company["name"], title=r.get("Title", ""),
                url=f"https://{o['host']}/hcmUI/CandidateExperience/en/sites/{o['site']}/job/{r['Id']}",
                locations=[r.get("PrimaryLocation")] + [x.get("Name") for x in secondary],
                country_hints=[r.get("PrimaryLocationCountry")] + [x.get("CountryCode") for x in secondary],
                posted_at=r.get("PostedDate"), posted_source="Oracle Recruiting posted date",
                workplace=(r.get("WorkplaceType") or "").lower().replace("-", "") or None,
                detail={"kind": "oracle", "host": o["host"], "site": o["site"], "id": str(r["Id"])},
            ))
        if len(rows) < PAGE or not recent:
            break
    return out


def describe(s, host: str, site: str, job_id: str) -> Optional[str]:
    url = (_API.format(host=host) + "recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
           f'&finder=ById;Id="{job_id}",siteNumber={site}')
    item = (get_json(s, url, timeout=45).get("items") or [{}])[0]
    parts = [strip_html(item.get(k)) for k in
             ("ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")]
    return "\n\n".join(p for p in parts if p) or None
