"""One run: fetch every source, screen, judge what is new, post each region's digest, save state."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import digest, github
from .descriptions import fetch_description
from .judge import JudgeUnavailable, make_judge, system_prompt
from .models import Job, Store
from .regions import Region, assign, load_regions
from .screen import Screen
from .sources import ADAPTERS
from .util import age_days, log, session, today_utc


@dataclass
class Ctx:
    """What a source adapter needs to know about the run."""
    regions: dict[str, Region]
    today: date
    lookback_days: int

    @property
    def iso2_countries(self) -> list[str]:
        return [r.raw["iso2"] for r in self.regions.values() if r.raw.get("iso2")]


@dataclass
class RunResult:
    exit_code: int = 0
    posted: Optional[dict] = None     # region -> issue URL (or rendered Markdown on a dry run)


def source_list(cfg: dict) -> list[dict]:
    """Community lists from config plus every watchlist company with a pollable platform."""
    out = [dict(name=l["name"], ats="simplify", url=l["url"]) for l in cfg["sources"].get("community_lists", [])]
    return out + [c for c in cfg["_companies"] if c.get("ats") in ADAPTERS]


def fetch_all(cfg: dict, ctx: Ctx, only: Optional[str] = None) -> tuple[list[Job], dict]:
    sources = [c for c in source_list(cfg) if not only or only.lower() in c["name"].lower()]
    health: dict[str, dict] = {}

    def one(company: dict) -> list[Job]:
        try:
            jobs = ADAPTERS[company["ats"]].fetch(session(), company, ctx)
            for j in jobs:
                j.meta["early_only"] = company.get("early_only") or []
            health[company["name"]] = {"ok": True, "count": len(jobs)}
            return jobs
        except Exception as e:  # noqa: BLE001 - one broken board must not sink the run
            health[company["name"]] = {"ok": False, "error": str(e)[:200]}
            log.warning("source %s failed: %s", company["name"], e)
            return []

    with ThreadPoolExecutor(max_workers=cfg["sources"].get("workers", 8)) as pool:
        jobs = [j for batch in pool.map(one, sources) for j in batch]
    return jobs, health


def screen_jobs(jobs: list[Job], ctx: Ctx, screen: Screen) -> list[Job]:
    """Keep postings that are in a region, pass the title screen and are recent (or undated)."""
    kept = []
    for j in jobs:
        age = age_days(j.posted_at, ctx.today)
        if age is not None and age > ctx.lookback_days:
            continue
        if not screen.check(j.title)[0]:
            continue
        # Very large boards can be limited, per region, to titles that say entry level.
        j.regions = [r for r in assign(j, ctx.regions)
                     if r not in j.meta.get("early_only", ()) or screen.is_early(j.title)]
        j.meta.pop("early_only", None)
        if j.regions:
            kept.append(j)
    return kept


def judge_queue(jobs: list[Job], region: Region, screen: Screen, ctx: Ctx, max_age: int) -> list[Job]:
    """Unjudged postings worth a model call for this region, most promising first."""
    queue = []
    for j in jobs:
        if region.key not in j.regions or region.key in j.verdicts or region.key in j.digested:
            continue
        age = age_days(j.posted_at, ctx.today)
        if age is None:
            # No posting date: only worth reading when the title itself says entry level.
            if not screen.is_early(j.title):
                continue
        elif age > max_age:
            continue
        queue.append(j)
    queue.sort(key=lambda j: (not screen.is_early(j.title), not region.is_preferred(j),
                              age_days(j.posted_at, ctx.today) or 0, j.id))   # id: stable, unbiased tiebreak
    return queue


def run_judge(judge, cfg: dict, region: Region, queue: list[Job], stamp: str) -> dict:
    system = system_prompt(cfg["_profile"], region)
    max_chars = cfg["judge"]["description_max_chars"]
    halt = threading.Event()
    stats = {"judged": 0, "failed": 0, "no_description": 0, "halted": None}

    def one(job: Job) -> None:
        if halt.is_set():
            return
        text = fetch_description(session(), job, max_chars)
        try:
            verdict = judge.judge(system, job, text, stamp)
        except JudgeUnavailable as e:
            stats["halted"] = str(e)
            halt.set()
            return
        if verdict is None:
            stats["failed"] += 1
            return
        job.verdicts[region.key] = verdict
        stats["judged"] += 1
        stats["no_description"] += 0 if text else 1

    with ThreadPoolExecutor(max_workers=cfg["judge"].get("workers", 4)) as pool:
        list(pool.map(one, queue))
    return stats


def _limitations(cfg, region: Region, sel, health: dict, jstats: dict, skipped: int, cap: int,
                 first_run: bool) -> list[str]:
    out = []
    bad = sorted(n for n, h in health.items() if not h["ok"])
    ok = len(health) - len(bad)
    out.append(f"Coverage is the watchlist, not the whole web: {ok} of {len(health)} sources answered "
               f"(company career boards on Greenhouse, Ashby, Lever, SmartRecruiters, Workday, Oracle Recruiting and amazon.jobs, "
               f"plus community new-grad lists). Employers outside `companies.yaml` are not searched.")
    if bad:
        shown = ", ".join(bad[:12]) + (f" and {len(bad) - 12} more" if len(bad) > 12 else "")
        out.append(f"Sources that failed this run and were not searched: {shown}.")
    win = ("today or yesterday" if sel.window_days == 1 else
           f"within the last {sel.window_days} days ({'first digest for this region' if first_run else 'catching up on missed runs'})")
    out.append(f"Freshness: main-list roles were posted {win} by the date the job board itself reports; "
               f"matches scoring {cfg['digest']['exceptional_fit']}+ may be up to "
               f"{cfg['digest']['exceptional_window_days']} days old. Every role was live on its board at search time. "
               f"Some boards report a repost or edit as a new date.")
    if skipped:
        out.append(f"{skipped} fresh postings passed the title screen but were over the per-run reading cap "
                   f"({cap}); they stay queued for the next run while still fresh.")
    if jstats.get("halted"):
        out.append(f"The model stopped early ({jstats['halted']}); unread postings stay queued.")
    if jstats.get("failed"):
        out.append(f"{jstats['failed']} postings could not be scored this run and will be retried.")
    if jstats.get("no_description"):
        out.append(f"{jstats['no_description']} postings were scored from the title alone because the "
                   f"description could not be fetched; these were scored conservatively.")
    if first_run:
        out.append("No earlier digest exists for this region, so there was no history to deduplicate against.")
    else:
        out.append(f"Roles from earlier digests, reposts and extra locations of the same requisition "
                   f"(same company and title within {cfg['digest']['repost_memory_days']} days) are left out.")
    out.append("Work authorization is reported from each posting's own wording only; it is not inferred "
               "from employer size or sponsorship history. Confirm with the employer before relying on it.")
    return out


def run(cfg: dict, region_keys: list[str], *, dry_run: bool = False, backend: Optional[str] = None,
        limit: Optional[int] = None, post_empty: Optional[bool] = None) -> RunResult:
    root: Path = cfg["_root"]
    regions = load_regions(cfg)
    ctx = Ctx(regions=regions, today=today_utc(), lookback_days=cfg["sources"]["lookback_days"])
    stamp = ctx.today.isoformat()
    screen = Screen.from_config(cfg)
    store = Store(root / "data" / "jobs.jsonl")
    state_path = root / "data" / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"last_digest": {}}

    fetched, health = fetch_all(cfg, ctx)
    if health and not any(h["ok"] for h in health.values()):
        log.error("every source failed; nothing to do")
        return RunResult(exit_code=3)
    candidates = screen_jobs(fetched, ctx, screen)
    log.info("fetched %d postings from %d sources; %d pass region + title + recency",
             len(fetched), len(health), len(candidates))
    # Fold in remembered verdicts and digest marks without storing unjudged postings.
    for j in candidates:
        if j.id in store.jobs:
            store.merge(j, stamp)
        else:
            j.first_seen = j.last_seen = stamp

    judge = make_judge(cfg, backend)
    result = RunResult(posted={})
    for key in region_keys:
        region = regions[key]
        last = state["last_digest"].get(key)
        now = digest.now_in(region)
        win = digest.window_days(last, now.date(), cfg["digest"]["max_catchup_days"])
        queue = judge_queue(candidates, region, screen, ctx, max(win, cfg["digest"]["exceptional_window_days"]))
        cap = limit if limit is not None else cfg["judge"]["max_per_run"]
        todo, skipped = queue[:cap], max(0, len(queue) - cap)
        log.info("[%s] %d postings to read (%d over the cap)", key, len(todo), skipped)

        jstats = {"judged": 0, "failed": 0, "no_description": 0, "halted": None}
        if todo and judge is None:
            log.error("[%s] no model backend: set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN "
                      "(or log in to the `claude` CLI). %d postings are waiting.", key, len(todo))
            result.exit_code = 2
            continue
        if todo:
            jstats = run_judge(judge, cfg, region, todo, stamp)
            log.info("[%s] judged %d, failed %d", key, jstats["judged"], jstats["failed"])
            if jstats["halted"] and not jstats["judged"]:
                log.error("[%s] model unavailable: %s", key, jstats["halted"])
                result.exit_code = 2
                continue
        for j in todo:
            if j.verdicts:
                store.jobs[j.id] = j

        known = list({j.id: j for j in list(store.jobs.values()) + candidates}.values())
        sel = digest.select(known, region, now.date(), cfg, last, stamp)
        body = digest.render(region, sel, now, _limitations(cfg, region, sel, health, jstats, skipped, cap, last is None))
        heading = digest.title(region, sel, now)

        want_empty = cfg["digest"]["post_when_empty"] if post_empty is None else post_empty
        if dry_run:
            result.posted[key] = f"# {heading}\n\n{body}"
            continue
        if sel.main or sel.unverified or want_empty:
            repo, tok = github.repo(cfg), github.token()
            if not repo or not tok:
                log.error("[%s] cannot post: no GitHub repository or token configured", key)
                result.exit_code = 4
                continue
            issues = github.Issues(repo, tok)
            labels = [cfg["github"]["label"], f"{cfg['github']['label']}:{key}"]
            result.posted[key] = issues.create(heading, body, labels)
            issues.close_older(labels[1], cfg["digest"]["close_after_days"], now.date())
            log.info("[%s] posted %s", key, result.posted[key])
        else:
            log.info("[%s] nothing new; no issue posted", key)
        for j in sel.main + sel.unverified:
            j.digested[key] = stamp
            store.jobs[j.id] = j
            for dup in j.meta.get("duplicates", []):
                if dup in store.jobs:
                    store.jobs[dup].digested[key] = stamp
        state["last_digest"][key] = now.date().isoformat()

    if not dry_run:
        # Rejected postings only matter while still fresh; digested ones are the dedup memory.
        short = (ctx.today - timedelta(days=ctx.lookback_days + 3)).isoformat()
        long = (ctx.today - timedelta(days=cfg["digest"]["repost_memory_days"] + 5)).isoformat()
        for jid in [i for i, j in store.jobs.items()
                    if (j.last_seen or "") < (long if j.digested else short)]:
            del store.jobs[jid]
        store.save()
        state["health"] = {"checked": stamp, "sources": dict(sorted(health.items()))}
        state_path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    return result
