"""The Job record and the JSONL store that makes the repository its own database."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class Job:
    id: str
    source: str                 # e.g. "greenhouse:stripe", "simplify"
    company: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    # Country codes/names straight from the ATS ("US", "AE", "UNITED STATES"). When present
    # they decide the region; otherwise the location strings are pattern-matched.
    country_hints: list[str] = field(default_factory=list)
    posted_at: Optional[str] = None        # YYYY-MM-DD as stated by the source
    posted_source: Optional[str] = None    # where that date came from, shown in the digest
    workplace: Optional[str] = None        # onsite | hybrid | remote, when the ATS says
    salary: Optional[str] = None
    detail: dict = field(default_factory=dict)   # how to fetch the description later
    meta: dict = field(default_factory=dict)
    # --- state, owned by the pipeline ---
    regions: list[str] = field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    verdicts: dict = field(default_factory=dict)   # region -> verdict dict
    digested: dict = field(default_factory=dict)   # region -> date it appeared in a digest
    # --- transient, never written to disk ---
    description: Optional[str] = None

    @staticmethod
    def make(source: str, native_id: str, **kw) -> "Job":
        jid = hashlib.sha1(f"{source}|{native_id}".encode()).hexdigest()[:16]
        kw["title"] = " ".join((kw.get("title") or "").split())
        kw["locations"] = [l.strip() for l in kw.get("locations") or [] if l and l.strip()]
        kw["country_hints"] = sorted({h.strip().upper() for h in kw.get("country_hints") or [] if h and h.strip()})
        return Job(id=jid, source=source, **kw)

    def to_record(self) -> dict:
        d = asdict(self)
        d.pop("description", None)
        return d


class Store:
    """data/jobs.jsonl: one posting per line, sorted by id so diffs stay small."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.jobs: dict[str, Job] = {}
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    j = Job(**json.loads(line))
                    self.jobs[j.id] = j

    def merge(self, fresh: Job, today: str) -> Job:
        """Fold a freshly fetched posting into the store, keeping verdicts and digest marks."""
        old = self.jobs.get(fresh.id)
        if old:
            fresh.first_seen = old.first_seen
            fresh.verdicts = old.verdicts
            fresh.digested = old.digested
            # A source that once gave a real date must not lose it to a later fetch without one.
            if not fresh.posted_at and old.posted_at:
                fresh.posted_at, fresh.posted_source = old.posted_at, old.posted_source
        else:
            fresh.first_seen = today
        fresh.last_seen = today
        self.jobs[fresh.id] = fresh
        return fresh

    def prune(self, cutoff: str) -> int:
        """Drop postings not seen since `cutoff` (they closed, or aged out of every source)."""
        stale = [i for i, j in self.jobs.items() if (j.last_seen or "") < cutoff]
        for i in stale:
            del self.jobs[i]
        return len(stale)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for jid in sorted(self.jobs):
                f.write(json.dumps(self.jobs[jid].to_record(), sort_keys=True, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)
