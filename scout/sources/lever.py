"""Lever postings (api.lever.co, with the EU instance for companies hosted there)."""
from __future__ import annotations

from typing import Optional

from ..models import Job
from ..util import get_json, iso_date, strip_html

HOSTS = {"global": "https://api.lever.co", "eu": "https://api.eu.lever.co"}


def _text(j: dict) -> str:
    parts = [j.get("descriptionPlain") or ""]
    for block in j.get("lists") or []:
        parts.append(f"{block.get('text', '')}\n{strip_html(block.get('content'))}")
    parts.append(j.get("additionalPlain") or "")
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _salary(j: dict) -> Optional[str]:
    r = j.get("salaryRange") or {}
    if r.get("min") and r.get("max"):
        return f"{r.get('currency', '')} {r['min']:,.0f}–{r['max']:,.0f} {r.get('interval', '')}".strip()
    return None


def fetch(s, company: dict, ctx) -> list[Job]:
    token = company["token"]
    base = HOSTS[company.get("instance", "global")]
    data = get_json(s, f"{base}/v0/postings/{token}", params={"mode": "json"}, timeout=60)
    out = []
    for j in data:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location")]
        out.append(Job.make(
            f"lever:{token}", j["id"],
            company=company["name"], title=j.get("text", ""), url=j.get("hostedUrl", ""),
            locations=locs,
            # Lever's `country` is the primary site only, so it is a hint for single-site jobs.
            country_hints=[j["country"]] if j.get("country") and len(locs) <= 1 else [],
            posted_at=iso_date(j.get("createdAt")), posted_source="Lever created date",
            workplace={"on-site": "onsite"}.get(j.get("workplaceType"), j.get("workplaceType")) or None,
            salary=_salary(j), description=_text(j),
            meta={"commitment": cat.get("commitment"), "team": cat.get("team")},
        ))
    return out


def describe(s, token: str, job_id: str) -> Optional[str]:
    for base in HOSTS.values():
        try:
            return _text(get_json(s, f"{base}/v0/postings/{token}/{job_id}", retries=0)) or None
        except Exception:  # noqa: BLE001 - try the other instance
            continue
    return None
