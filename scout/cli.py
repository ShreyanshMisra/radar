"""Command line: python -m scout run | probe | fetch | stats"""
from __future__ import annotations

import argparse
import collections
import logging
import os
import re
import sys

from . import config
from .models import Store
from .pipeline import Ctx, fetch_all, run, screen_jobs
from .regions import load_regions
from .screen import Screen
from .sources import ADAPTERS
from .util import log, session, today_utc


def cmd_run(args) -> int:
    cfg = config.load()
    keys = list(cfg["regions"]) if args.region == "all" else [args.region]
    res = run(cfg, keys, dry_run=args.dry_run, backend=args.backend, limit=args.limit,
              post_empty=True if args.post_empty else None)
    for key, out in (res.posted or {}).items():
        print(out if args.dry_run else f"{key}: {out}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and res.posted:
        with open(summary, "a") as f:
            f.writelines(f"- **{k}**: {v}\n" for k, v in res.posted.items())
    return res.exit_code


def _ctx(cfg) -> Ctx:
    return Ctx(regions=load_regions(cfg), today=today_utc(), lookback_days=cfg["sources"]["lookback_days"])


def cmd_fetch(args) -> int:
    """Debug one source: what it returns and what survives the screen."""
    cfg = config.load()
    ctx = _ctx(cfg)
    jobs, health = fetch_all(cfg, ctx, only=args.name)
    for name, h in health.items():
        print(f"{name}: {h}")
    kept = screen_jobs(jobs, ctx, Screen.from_config(cfg))
    print(f"{len(jobs)} postings, {len(kept)} pass region + title + recency")
    for j in sorted(kept, key=lambda j: j.posted_at or "", reverse=True)[: args.show]:
        print(f"  {j.posted_at or '????-??-??'}  {','.join(j.regions):6}  {j.company}: {j.title}  [{'; '.join(j.locations)}]")
    return 0


def cmd_probe(args) -> int:
    """Find which hiring platform hosts a company's board, and print a companies.yaml entry."""
    s = session()
    slugs = list(dict.fromkeys([args.token] if args.token else [
        re.sub(r"[^a-z0-9]", "", args.name.lower()), re.sub(r"[^a-z0-9]+", "-", args.name.lower()).strip("-"),
        args.name.replace(" ", ""),
    ]))
    ctx = _ctx(config.load())
    found = False
    for ats in ("greenhouse", "ashby", "lever", "smartrecruiters"):
        for slug in slugs:
            variants = [{}] if ats != "lever" else [{}, {"instance": "eu"}]
            for extra in variants:
                company = {"name": args.name, "ats": ats, "token": slug, **extra}
                try:
                    jobs = ADAPTERS[ats].fetch(s, company, ctx)
                except Exception:  # noqa: BLE001 - no board under this slug
                    continue
                if not jobs:
                    continue
                found = True
                inst = ", instance: eu" if extra else ""
                print(f"# {len(jobs)} postings\n- {{name: {args.name}, ats: {ats}, token: {slug}{inst}}}")
    if not found:
        print(f"No public Greenhouse/Ashby/Lever/SmartRecruiters board found for {args.name!r}. "
              f"If it uses Workday, add a `workday: {{host, tenant, site}}` entry by hand (see companies.yaml).")
    return 0 if found else 1


def cmd_stats(_args) -> int:
    cfg = config.load()
    store = Store(cfg["_root"] / "data" / "jobs.jsonl")
    print(f"{len(store.jobs)} remembered postings")
    for key in cfg["regions"]:
        vs = [j.verdicts[key] for j in store.jobs.values() if key in j.verdicts]
        dig = sum(1 for j in store.jobs.values() if key in j.digested)
        fams = collections.Counter(v["role_family"] for v in vs if v["in_scope"])
        print(f"[{key}] judged {len(vs)}, in scope {sum(v['in_scope'] for v in vs)}, digested {dig}; {dict(fams)}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="scout", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="fetch, screen, judge and post the digest for a region")
    r.add_argument("--region", default="all", help="region key from config.yaml, or 'all'")
    r.add_argument("--dry-run", action="store_true", help="print the digest; post nothing, save nothing")
    r.add_argument("--backend", choices=["auto", "api", "claude_code"])
    r.add_argument("--limit", type=int, help="read at most N postings per region this run")
    r.add_argument("--post-empty", action="store_true", help="post an issue even when nothing matched")
    r.set_defaults(fn=cmd_run)

    f = sub.add_parser("fetch", help="debug: fetch sources whose name contains NAME")
    f.add_argument("name")
    f.add_argument("--show", type=int, default=25)
    f.set_defaults(fn=cmd_fetch)

    pr = sub.add_parser("probe", help="find a company's job board and print its companies.yaml entry")
    pr.add_argument("name")
    pr.add_argument("--token", help="board slug to try instead of guesses from the name")
    pr.set_defaults(fn=cmd_probe)

    sub.add_parser("stats", help="summarise remembered postings").set_defaults(fn=cmd_stats)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S", stream=sys.stderr)
    log.setLevel(logging.DEBUG if args.verbose else logging.INFO)
    return args.fn(args)
