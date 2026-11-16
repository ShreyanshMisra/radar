"""Workday career sites through the /wday/cxs JSON endpoint.

Tenants hold thousands of postings, so each region is queried through the site's own
country facet (discovered from the first response) and paged newest-first only as far back
as the lookback window. Sites without a recognisable country facet fall back to an
unfiltered recent scan, with the location taken from the listing text and the URL slug.
GitHub-hosted runners are sometimes refused (403); that shows up in the digest's
limitations rather than failing the run.
"""
from __future__ import annotations

from typing import Optional

from ..models import Job
from ..util import age_days, get_json, post_json, relative_date, strip_html

PAGE = 20
MAX_PAGES = 15


def _url(wd: dict) -> str:
    return f"https://{wd['host']}/wday/cxs/{wd['tenant']}/{wd['site']}/jobs"


def _find_country_facet(facets: list, names: set[str], parent: Optional[str] = None) -> Optional[tuple[str, str]]:
    for f in facets or []:
        param = f.get("facetParameter") or parent
        if f.get("id") and param and (f.get("descriptor") or "").strip().lower() in names:
            return param, f["id"]
        hit = _find_country_facet(f.get("values") or [], names, param)
        if hit:
            return hit
    return None


def _scan(s, wd: dict, company: dict, ctx, facets: dict, hints: list[str]) -> list[Job]:
    out = []
    for page in range(MAX_PAGES):
        data = post_json(s, _url(wd), {"appliedFacets": facets, "limit": PAGE, "offset": page * PAGE,
                                       "searchText": ""}, timeout=30)
        postings = data.get("jobPostings", [])
        any_recent = False
        for p in postings:
            path = p.get("externalPath")
            if not path:
                continue
            posted = relative_date(p.get("postedOn"), ctx.today)
            age = age_days(posted, ctx.today)
            if age is not None and age > ctx.lookback_days:
                continue
            any_recent = True
            text = p.get("locationsText") or ""
            slug = path.split("/")[2].replace("-", " ") if path.count("/") >= 3 else ""
            out.append(Job.make(
                f"workday:{wd['tenant']}/{wd['site']}", path.rsplit("/", 1)[-1],
                company=company["name"], title=p.get("title", ""),
                url=f"https://{wd['host']}/en-US/{wd['site']}{path}",
                # "3 Locations" says nothing; the slug names the primary site.
                locations=[slug if "Locations" in text and slug else text],
                country_hints=hints,
                posted_at=posted, posted_source=f"Workday listing (\"{p.get('postedOn')}\")" if posted else None,
                detail={"kind": "workday", **wd, "path": path},
            ))
        # Newest-first is only approximate, so stop on a page with nothing recent at all.
        if len(postings) < PAGE or not any_recent:
            break
    return out


def fetch(s, company: dict, ctx) -> list[Job]:
    wd = company["workday"]
    first = post_json(s, _url(wd), {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, timeout=30)
    jobs: dict[str, Job] = {}
    # Facets list only countries with open roles, so a missing region facet means no roles there.
    # Only a site with no recognisable country facet at all needs the unfiltered scan.
    any_facet = False
    for region in ctx.regions.values():
        names = {n.lower() for n in region.raw.get("workday_countries", [])}
        facet = _find_country_facet(first.get("facets") or [], names)
        if not facet:
            continue
        any_facet = True
        for j in _scan(s, wd, company, ctx, {facet[0]: [facet[1]]}, sorted(region.countries)[:1]):
            if j.id in jobs:
                jobs[j.id].country_hints = sorted(set(jobs[j.id].country_hints) | set(j.country_hints))
            else:
                jobs[j.id] = j
    if not any_facet:
        for j in _scan(s, wd, company, ctx, {}, []):
            jobs.setdefault(j.id, j)
    return list(jobs.values())


def describe(s, host: str, tenant: str, site: str, path: str) -> dict:
    info = get_json(s, f"https://{host}/wday/cxs/{tenant}/{site}{path}", timeout=30).get("jobPostingInfo") or {}
    locs = [info.get("location")] + list(info.get("additionalLocations") or [])
    return {"description": strip_html(info.get("jobDescription")) or None,
            "posted_at": (info.get("startDate") or "")[:10] or None,
            "locations": [l for l in locs if l],
            "workplace": (info.get("remoteType") or "").lower() or None}
