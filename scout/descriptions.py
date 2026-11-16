"""Get the full text of a posting for the judge, through the ATS API whenever one applies."""
from __future__ import annotations

import re
from typing import Optional

from .models import Job
from .sources import ashby, greenhouse, lever, oracle, smartrecruiters, workday
from .util import log, strip_html

_GH = re.compile(r"greenhouse\.io/([a-z0-9_-]+)/jobs/(\d+)", re.I)
_GH_EMBED = re.compile(r"greenhouse\.io/embed/job_app\?(?=.*?for=([a-z0-9_-]+))(?=.*?token=(\d+))", re.I)
_LEVER = re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_.-]+)/([0-9a-f-]{36})", re.I)
_ASHBY = re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", re.I)
_WD = re.compile(r"https://(([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)(/job/[^?#]+)", re.I)


def fetch_description(s, job: Job, max_chars: int) -> Optional[str]:
    try:
        text = _fetch(s, job)
    except Exception as e:  # noqa: BLE001 - a missing description is reported, never fatal
        log.info("description fetch failed for %s: %s", job.url, e)
        text = None
    return text.strip()[:max_chars] if text and text.strip() else None


def _apply_workday(job: Job, d: dict) -> Optional[str]:
    # The detail page is authoritative: exact posting date and the full list of sites.
    if d.get("posted_at"):
        job.posted_at, job.posted_source = d["posted_at"], "Workday posting start date"
    if d.get("locations"):
        job.locations = d["locations"]
    job.workplace = job.workplace or d.get("workplace")
    return d.get("description")


def _fetch(s, job: Job) -> Optional[str]:
    if job.description:
        return job.description
    k = job.detail or {}
    if k.get("kind") == "greenhouse":
        return greenhouse.describe(s, k["token"], k["id"])
    if k.get("kind") == "smartrecruiters" and k.get("ref"):
        return smartrecruiters.describe(s, k["ref"])
    if k.get("kind") == "oracle":
        return oracle.describe(s, k["host"], k["site"], k["id"])
    if k.get("kind") == "workday":
        return _apply_workday(job, workday.describe(s, k["host"], k["tenant"], k["site"], k["path"]))

    # Community-list postings carry only a URL; most point at an ATS we can query.
    url = job.url
    for rx, fn in ((_GH_EMBED, greenhouse.describe), (_GH, greenhouse.describe),
                   (_LEVER, lever.describe), (_ASHBY, ashby.describe)):
        m = rx.search(url)
        if m:
            return fn(s, m.group(1), m.group(2))
    m = _WD.search(url)
    if m:
        return _apply_workday(job, workday.describe(s, m.group(1), m.group(2), m.group(3), m.group(4)))

    r = s.get(url, timeout=15, headers={"Accept": "text/html"})
    if r.ok and "html" in r.headers.get("content-type", ""):
        text = strip_html(r.text)
        # Client-rendered pages come back as an empty shell; that is not a description.
        return text if len(text) > 600 else None
    return None
