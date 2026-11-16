"""Choose what goes in a region's digest and render it as issue Markdown."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from .models import Job
from .regions import Region
from .util import age_days, norm_key

AUTH_RANK = {"favorable": 0, "unknown": 1, "excluded": 2}
AUTH_LABEL = {"favorable": "Favorable", "unknown": "Unknown", "excluded": "Excluded"}


@dataclass
class Selection:
    main: list[Job] = field(default_factory=list)
    unverified: list[Job] = field(default_factory=list)   # good match, posting date unknown
    window_days: int = 1
    considered: int = 0


def dedup_key(job: Job) -> tuple[str, str]:
    """Same company + same title = same requisition, whatever the location or repost."""
    return norm_key(job.company), norm_key(job.title)


def window_days(last_digest: Optional[str], today: date, max_catchup: int) -> int:
    """Normally 'posted today or yesterday'. After missed runs, stretch to cover the gap.

    A region's very first digest has no history to lean on, so it opens with the whole
    catch-up window: everything still fresh, once, instead of an empty first issue.
    """
    if not last_digest:
        return max_catchup
    return max(1, min(max_catchup, (today - date.fromisoformat(last_digest)).days))


def select(jobs: list[Job], region: Region, today: date, cfg: dict, last_digest: Optional[str],
           run_stamp: str) -> Selection:
    """`run_stamp` is the last_seen value of this run: only postings confirmed open just now qualify."""
    d = cfg["digest"]
    sel = Selection(window_days=window_days(last_digest, today, d["max_catchup_days"]))
    horizon = (today - timedelta(days=d["repost_memory_days"])).isoformat()
    already = {dedup_key(j) for j in jobs if j.digested.get(region.key, "") >= horizon}

    best: dict[tuple, Job] = {}
    for j in jobs:
        v = j.verdicts.get(region.key)
        if not v or region.key in j.digested or j.last_seen != run_stamp:
            continue
        sel.considered += 1
        if not v["in_scope"] or v["authorization"] == "excluded" or v["fit"] < d["min_fit"]:
            continue
        age = age_days(j.posted_at, today)
        if age is not None and age > sel.window_days and not (
                age <= d["exceptional_window_days"] and v["fit"] >= d["exceptional_fit"]):
            continue
        key = dedup_key(j)
        if key in already:
            continue
        cur = best.get(key)
        if cur is None:
            best[key] = j
            continue
        # The same requisition listed per location, or via both a list and the employer's board.
        keep, drop = (j, cur) if _prefer(j, cur, region) else (cur, j)
        keep.locations = list(dict.fromkeys(keep.locations + drop.locations))
        keep.meta.setdefault("duplicates", []).append(drop.id)
        best[key] = keep

    ranked = sorted(best.values(), key=lambda j: _rank(j, region, today))
    dated = [j for j in ranked if j.posted_at]
    sel.main = dated[: d["max_items"]]
    sel.unverified = [j for j in ranked if not j.posted_at][: d["max_unverified"]]
    return sel


def _prefer(a: Job, b: Job, region: Region) -> bool:
    """Prefer the employer's own board over a community list, then the better verdict."""
    ka = (a.source == "simplify", -a.verdicts[region.key]["fit"])
    kb = (b.source == "simplify", -b.verdicts[region.key]["fit"])
    return ka < kb


def _rank(j: Job, region: Region, today: date):
    v = j.verdicts[region.key]
    # Fit first, in 5-point bands so near-ties fall through to authorization evidence,
    # then the preferred location, then freshness.
    return (-(v["fit"] // 5), AUTH_RANK[v["authorization"]], not region.is_preferred(j),
            age_days(j.posted_at, today) if j.posted_at else 99, -v["fit"], j.company.lower())


# ---------------------------------------------------------------- rendering

def _cell(text: str) -> str:
    return " ".join(str(text or "").split()).replace("|", "\\|")


def _short_date(iso: Optional[str]) -> str:
    if not iso:
        return "unknown"
    return date.fromisoformat(iso).strftime("%b %-d")


def _where(j: Job, v: dict) -> str:
    locs = j.locations[:3] + ([f"+{len(j.locations) - 3} more"] if len(j.locations) > 3 else [])
    arr = v["arrangement"] if v["arrangement"] != "not stated" else (j.workplace or "arrangement not stated")
    out = f"{'; '.join(locs) or 'Location not given'} · {arr}"
    if v.get("remote_restriction"):
        out += f" ({v['remote_restriction']})"
    return out


def _posted(j: Job, today: date) -> str:
    if not j.posted_at:
        return f"Unknown (first seen {_short_date(j.first_seen)})"
    age = age_days(j.posted_at, today)
    rel = {0: "today", 1: "yesterday"}.get(age, f"{age} days ago")
    return f"{_short_date(j.posted_at)} ({rel})"


def _auth(v: dict) -> str:
    ev = v["authorization_evidence"].strip()
    label = AUTH_LABEL[v["authorization"]]
    if v["authorization"] == "unknown" and "silent" in ev.lower():
        return "Unknown: posting is silent"
    quote = ev.strip('“”"')
    return f"{label}: “{quote}”"


def _salary(j: Job, v: dict) -> str:
    return v.get("salary") or j.salary or "Not disclosed"


def _highlight(n: int, j: Job, v: dict, today: date) -> str:
    return "\n".join([
        f"### {n}. [{j.title}]({j.url}) · {j.company}",
        f"{_where(j, v)}  ",
        f"Posted {_posted(j, today)}, per {j.posted_source or 'the job board'} · Salary: {_salary(j, v)} · Fit {v['fit']}/100",
        "",
        f"- **Why it stands out:** {v['why']}",
        f"- **Gap:** {v['gap']}",
        f"- **Work authorization:** {_auth(v)}",
        f"- **Experience asked:** {v['years_required']}",
        "",
    ])


def _table(rows: list[Job], region: Region, today: date) -> str:
    out = ["| # | Role | Location | Posted | Salary | Fit | Why it fits | Gap | Authorization |",
           "|---|---|---|---|---|---|---|---|---|"]
    for n, j in enumerate(rows, 1):
        v = j.verdicts[region.key]
        out.append("| " + " | ".join([
            str(n), f"[{_cell(j.title)}]({j.url})<br>{_cell(j.company)}", _cell(_where(j, v)),
            _cell(_posted(j, today)), _cell(_salary(j, v)), str(v["fit"]), _cell(v["why"]), _cell(v["gap"]),
            _cell(_auth(v)),
        ]) + " |")
    return "\n".join(out)


def title(region: Region, sel: Selection, now: datetime) -> str:
    n = len(sel.main)
    what = f"{n} new match{'es' if n != 1 else ''}" if n else "no new matches"
    return f"{region.flag} {region.name} jobs · {now.strftime('%a %b %-d')} · {what}".strip()


def render(region: Region, sel: Selection, now: datetime, limitations: list[str]) -> str:
    today = now.date()
    tz = now.strftime("%Z")
    out = [f"Searched {now.strftime('%A, %B %-d, %Y at %-I:%M %p')} {tz} ({region.timezone}). "
           f"{sel.considered} fresh postings were read in full; {len(sel.main)} made the list.", ""]
    if not sel.main:
        out += ["No posting from the last day cleared the bar today. Nothing was added to pad the list.", ""]
    else:
        top = sel.main[:3]
        out += [f"## {'Top three' if len(top) == 3 else 'Matches'}", ""]
        out += [_highlight(n, j, j.verdicts[region.key], today) for n, j in enumerate(top, 1)]
        out += ["## All matches", "", _table(sel.main, region, today), ""]
    if sel.unverified:
        out += ["## Freshness unverified", "",
                "Good matches whose posting date the source does not state. Not counted above.", "",
                _table(sel.unverified, region, today), ""]
    out += ["## Search limitations", ""] + [f"- {l}" for l in limitations]
    out += ["", "<sub>Read-only: this digest never applies, submits forms or contacts employers. "
                "Links go to the employer's careers page or applicant-tracking system.</sub>"]
    return "\n".join(out)


def now_in(region: Region) -> datetime:
    return datetime.now(ZoneInfo(region.timezone))
