# job-scout

A personal job board that runs itself. Every morning it reads company career boards directly,
asks Claude whether each new posting fits the resume in `profile.md`, and posts one digest per
region as a GitHub Issue: currently the **United States** (07:15 Eastern) and **Dubai / UAE**
(07:15 Gulf time). GitHub emails each issue to whoever watches the repository.

This repository is public, so `profile.md` (the resume text, without contact details) and the
digest issues are visible to anyone. Keep anything private out of both.

## How it works

```
company boards ─────────────┐
  Greenhouse · Ashby · Lever │    title screen        Claude verdict          digest issue
  SmartRecruiters · Workday  ├──► region, recency ──► fit, scope, gap,   ──►  top three + table
  Oracle Recruiting · Amazon │    (no model cost)     work authorization      per region
community new-grad lists ────┘
```

1. **Fetch.** Every board in `companies.yaml` is read through its public JSON API, so each
   posting comes with the employer's own link, a real posting date, and proof it is open now.
2. **Screen.** Rules in `config.yaml` keep postings that are in a region, were posted in the last
   few days, and whose title is not obviously senior, an internship or an unrelated profession.
3. **Judge.** Claude reads each surviving posting once against `profile.md` and returns a
   structured verdict: in scope or not (full-time, 0–2 years, in region), a fit score, one sentence
   tying the role to the resume, the main gap, salary if disclosed, and what the posting itself
   says about work authorization.
4. **Digest.** Roles scoring 70+ that are not authorization-blocked are ranked by fit, then
   authorization evidence, preferred location and freshness. At most 15 are posted and the list is
   never padded. Reposts, extra locations of one requisition and anything from an earlier digest
   are left out.

`data/jobs.jsonl` remembers verdicts and what has been digested; the workflow commits it after
each run, so the repository is its own database.

## Setup

1. Push this repository to GitHub.
2. Give the workflow a way to call Claude. Either one works; set one repository secret:
   - **Claude subscription (no per-token bill).** Run `claude setup-token` locally and save the
     result: `gh secret set CLAUDE_CODE_OAUTH_TOKEN`. Runs count against your plan's usage limits.
   - **Anthropic API key (metered).** `gh secret set ANTHROPIC_API_KEY`. At the default
     `claude-opus-5-5` a posting costs about 2 cents, so a busy day is $1–2. Setting
     `judge.model: claude-haiku-5-5` in `config.yaml` cuts that by roughly 40x at some cost in
     judgment.
3. Watch the repository (Watch → All Activity) so the digests reach your inbox.
4. Run the `digest` workflow once from the Actions tab to confirm it posts.

Without credentials the run fails loudly rather than posting an unjudged list.

## Making it yours

| To change | Edit |
|---|---|
| Resume, level, what you have done | `profile.md` (replace the text when the resume changes) |
| Regions, location preference, authorization rules | `regions:` in `config.yaml` |
| Which titles are considered | `screen:` in `config.yaml` |
| How strict and how long the digest is | `digest:` in `config.yaml` |
| Model, cost, postings read per run | `judge:` in `config.yaml` |
| Which employers are searched | `companies.yaml` |
| When it runs | the two `cron` lines in `.github/workflows/digest.yml` |

Add an employer:

```bash
python -m scout probe "Figma"     # finds the board and prints the line for companies.yaml
```

Workday and Oracle Recruiting sites are added by hand; the header of `companies.yaml` shows the
shape. A new region is one more block under `regions:` plus a cron line.

## Running it locally

```bash
uv venv --python 3.12 && uv pip install -r requirements.txt pytest
.venv/bin/python -m scout run --region us --dry-run    # print the digest, post and save nothing
.venv/bin/python -m scout run --region uae --limit 20  # read at most 20 postings
.venv/bin/python -m scout fetch Stripe                 # what one board returns and what survives
.venv/bin/python -m scout stats
.venv/bin/python -m pytest -q
```

Locally the judge uses `ANTHROPIC_API_KEY` if set, otherwise your `claude` CLI login. To post from
a laptop, set `github.repo` in `config.yaml`; the token comes from `gh auth token`.

## What it does not do

- **It searches the watchlist, not the web.** A role at an employer missing from `companies.yaml`
  and from the community lists will not appear. Each digest states how many sources answered and
  names the ones that failed.
- **UAE coverage is narrower than US coverage.** Many Gulf employers use platforms with no public
  feed (SuccessFactors, Avature, Teamtailor, Workable). Those are not covered.
- **Authorization is read from the posting only.** Silence is reported as "Unknown", never guessed
  from an employer's size or history.
- **It never applies, fills in forms or contacts anyone.** It only reads public job pages.

## Layout

```
profile.md            the resume the model judges against
config.yaml           regions, screen, judge, digest settings
companies.yaml        watchlist
scout/sources/        one adapter per hiring platform
scout/regions.py      which region a posting belongs to
scout/screen.py       title screen
scout/judge.py        the Claude call and its JSON schema (API or `claude -p`)
scout/digest.py       selection, ranking, Markdown
scout/pipeline.py     one run, end to end
data/                 remembered verdicts and source health
.github/workflows/    digest.yml (daily, per region) · tests.yml
```

The approach is modelled on [new-grad-radar](https://github.com/ssshawnwang/new-grad-radar).
