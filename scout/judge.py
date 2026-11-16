"""Ask Claude whether one posting fits the candidate, and why. One structured call per posting.

Two interchangeable backends return the same verdict:
  ApiJudge         the Anthropic API through the SDK (ANTHROPIC_API_KEY, metered).
  ClaudeCodeJudge  headless Claude Code (`claude -p`) on a Claude subscription login or a
                   CLAUDE_CODE_OAUTH_TOKEN. Subscription credentials are only valid inside
                   Claude Code, which is why this shells out instead of reusing the SDK.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

from .models import Job
from .regions import Region
from .util import log

ROLE_FAMILIES = ["software_engineering", "data_engineering", "forward_deployed_or_customer_engineering",
                 "product_management", "analytics_or_bi", "strategy_and_operations", "ml_or_ai", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "in_scope": {"type": "boolean", "description":
                     "True only if ALL hold: a full-time permanent role (not an internship, co-op, "
                     "apprenticeship or fixed short contract); realistically open to someone with 0-2 years of "
                     "experience; located in (or remotely workable from) the target region; the hiring "
                     "employer is identified (not an anonymous staffing-agency listing)."},
        "out_of_scope_reason": {"type": "string", "description": "Why in_scope is false; empty string otherwise."},
        "role_family": {"type": "string", "enum": ROLE_FAMILIES},
        "years_required": {"type": "string", "description":
                           "Experience the posting asks for, as written, e.g. '0-2 years', '3+ years', "
                           "'new grad', 'not stated'."},
        "fit": {"type": "integer", "description":
                "0-100: how well the candidate's demonstrated experience meets the core requirements. "
                "85+ = meets essentially every core requirement with direct evidence; 70-84 = strong, one "
                "modest gap; 50-69 = plausible but with real gaps; below 50 = weak."},
        "why": {"type": "string", "description":
                "One concise sentence connecting this role to specific experience on the resume. Name the "
                "experience. Never invent qualifications the resume does not show."},
        "gap": {"type": "string", "description":
                "The most meaningful qualification gap in one short phrase, or 'None significant'."},
        "arrangement": {"type": "string", "enum": ["onsite", "hybrid", "remote", "not stated"]},
        "remote_restriction": {"type": "string", "description":
                               "For remote roles, any geographic restriction as stated (e.g. 'US only', "
                               "'must reside in CA, NY or TX'); empty string if none or not remote."},
        "salary": {"type": "string", "description":
                   "Salary or range exactly as disclosed in the posting, with currency and period; empty "
                   "string if not disclosed. Never estimate."},
        "authorization": {"type": "string", "enum": ["favorable", "unknown", "excluded"], "description":
                          "Judged only from this posting's own wording, per the authorization rules in the "
                          "system prompt. 'unknown' when the posting is silent."},
        "authorization_evidence": {"type": "string", "description":
                                   "The posting's own wording on work authorization, sponsorship, "
                                   "citizenship, nationality or clearance, quoted briefly; 'Posting is silent' "
                                   "if there is none."},
    },
    "required": ["in_scope", "out_of_scope_reason", "role_family", "years_required", "fit", "why", "gap",
                 "arrangement", "remote_restriction", "salary", "authorization", "authorization_evidence"],
    "additionalProperties": False,
}

SYSTEM = """You screen job postings for one specific candidate. For each posting you return a \
structured verdict that a daily digest is built from. The candidate reads the digest to decide \
where to apply, so a wrong "fits" costs them an application and a wrong "doesn't fit" hides a \
real opportunity. Judge from the posting text and the profile below, nothing else.

# Candidate profile (the source of truth)

{profile}

# What counts as a match

The candidate wants full-time entry-level or new-graduate roles in software engineering, data \
engineering, forward deployed or customer/solutions engineering, product management or technical \
product, product/data analytics, business intelligence, or strategy & operations, or any other \
role where the skills they have demonstrated meet the core requirements. Strong fit matters more \
than the label on the role.

- Experience: in scope means the posting asks for 0-2 years (or says new grad, entry level, \
early career, or is silent and reads as junior). Internships count as experience the candidate \
has, roughly one year in total. A posting that requires 3+ years, or a senior scope under a \
plain title, is out of scope.
- Use the resume as written. Do not credit skills or domains it does not show. When a posting's \
core requirement is something the resume lacks, say so in `gap` and lower `fit`.
- Internships, co-ops, apprenticeships and anonymous staffing-agency listings are out of scope.
- The candidate is the reader. Write `why` and `gap` to them in the second person ("you built \
..."), never in the third person.
- If the posting text is missing or too thin to judge, score conservatively from the title and \
say in `gap` that the description was unavailable.

# This region: {region_name}

{region_brief}

Report authorization from the posting's own words only. Do not infer it from the employer's \
size, reputation or past sponsorship history."""


def system_prompt(profile: str, region: Region) -> str:
    return SYSTEM.format(profile=profile.strip(), region_name=region.name, region_brief=region.brief)


def user_content(job: Job, description: Optional[str]) -> str:
    lines = [f"Company: {job.company}", f"Title: {job.title}",
             f"Location(s): {'; '.join(job.locations) or 'not given'}"]
    if job.workplace:
        lines.append(f"Workplace type (from the job board): {job.workplace}")
    if job.salary:
        lines.append(f"Compensation (from the job board): {job.salary}")
    if job.meta.get("list_sponsorship") and job.meta["list_sponsorship"] != "Other":
        lines.append(f"Community-list sponsorship tag (unverified): {job.meta['list_sponsorship']}")
    lines.append(f"URL: {job.url}")
    lines.append("\nPosting text:\n" + (description or "(not available)"))
    return "\n".join(lines)


class JudgeUnavailable(RuntimeError):
    """No usable credentials, or the usage limit was hit. Remaining postings stay pending."""


def _clean(v: dict, model: str, had_description: bool, today: str) -> Optional[dict]:
    if not isinstance(v, dict) or not all(k in v for k in SCHEMA["required"]):
        return None
    v = {k: v[k] for k in SCHEMA["required"]}
    try:
        v["fit"] = max(0, min(100, int(v["fit"])))
    except (TypeError, ValueError):
        return None
    v.update(model=model, judged_at=today, had_description=had_description)
    return v


# Models that can decline a request and support a server-side fallback (see the migration guide).
_FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


class ApiJudge:
    name = "api"

    def __init__(self, cfg: dict):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = cfg["judge"]["model"]
        self.effort = cfg["judge"].get("effort", "low")

    @staticmethod
    def available() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def judge(self, system: str, job: Job, description: Optional[str], today: str) -> Optional[dict]:
        a = self.anthropic
        kwargs = dict(
            model=self.model, max_tokens=4000,
            # The profile and rules are identical for every posting in a run: cache them.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_content(job, description)}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        try:
            if self.model.startswith(_FALLBACK_MODELS):
                resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except (a.AuthenticationError, a.PermissionDeniedError) as e:
            raise JudgeUnavailable(f"Anthropic API rejected the credentials: {e.message}") from e
        except a.NotFoundError as e:
            raise JudgeUnavailable(f"unknown model {self.model!r}: {e.message}") from e
        except a.RateLimitError as e:
            log.warning("rate limited on %s: %s", job.url, e.message)
            return None
        except a.APIStatusError as e:
            log.warning("API error %s on %s: %s", e.status_code, job.url, e.message)
            return None
        except a.APIConnectionError as e:
            log.warning("network error on %s: %s", job.url, e)
            return None
        if resp.stop_reason == "refusal":
            log.warning("model declined %s", job.url)
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            return _clean(json.loads(text), resp.model, bool(description), today)
        except json.JSONDecodeError:
            log.warning("unparseable verdict for %s: %r", job.url, text[:200])
            return None


class ClaudeCodeJudge:
    name = "claude_code"
    _METERED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    def __init__(self, cfg: dict):
        self.model = cfg["judge"]["model"]
        self.effort = cfg["judge"].get("effort", "low")
        self.timeout = int(cfg["judge"].get("claude_code_timeout", 240))

    @staticmethod
    def available() -> bool:
        return shutil.which("claude") is not None

    def judge(self, system: str, job: Job, description: Optional[str], today: str) -> Optional[dict]:
        # No tools, no MCP servers, no settings files, no session history: a pure function call.
        cmd = ["claude", "-p", "--model", self.model, "--effort", self.effort, "--tools", "",
               "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
               "--disable-slash-commands", "--output-format", "json",
               "--json-schema", json.dumps(SCHEMA), "--system-prompt", system]
        # Never let a metered key silently take over a subscription run.
        env = {k: v for k, v in os.environ.items() if k not in self._METERED}
        try:
            proc = subprocess.run(cmd, input=user_content(job, description), capture_output=True, text=True,
                                  timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            log.warning("claude -p timed out on %s", job.url)
            return None
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            self._raise_if_fatal((proc.stderr or proc.stdout or "").strip())
            log.warning("claude -p gave no JSON for %s (exit %s)", job.url, proc.returncode)
            return None
        if out.get("is_error") or out.get("subtype") != "success":
            self._raise_if_fatal(str(out.get("result") or out.get("subtype") or ""))
            log.warning("claude -p failed on %s: %s", job.url, str(out.get("result"))[:200])
            return None
        used = list((out.get("modelUsage") or {}).keys())
        return _clean(out.get("structured_output"), used[0] if used else self.model, bool(description), today)

    @staticmethod
    def _raise_if_fatal(msg: str) -> None:
        low = msg.lower()
        if any(w in low for w in ("not logged in", "/login", "invalid api key", "authentication", "oauth")):
            raise JudgeUnavailable(f"Claude Code is not authenticated: {msg[:200]}")
        if "limit" in low and any(w in low for w in ("usage", "rate", "reached", "resets")):
            raise JudgeUnavailable(f"Claude usage limit reached: {msg[:200]}")


def make_judge(cfg: dict, override: Optional[str] = None):
    """Resolve judge.backend ('auto' | 'api' | 'claude_code') to a judge, or None if none works."""
    want = (override or cfg["judge"].get("backend") or "auto").lower()
    order = {"auto": (ApiJudge, ClaudeCodeJudge), "api": (ApiJudge,), "claude_code": (ClaudeCodeJudge,)}[want]
    for cls in order:
        if cls.available():
            return cls(cfg)
    return None
