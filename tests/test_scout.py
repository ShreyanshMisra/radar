"""Behaviour that must not drift: who is in a region, what the screen drops, what a digest shows."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scout import config, digest
from scout.models import Job, Store
from scout.pipeline import Ctx, judge_queue, screen_jobs
from scout.regions import load_regions
from scout.screen import Screen
from scout.util import iso_date, long_date, relative_date, strip_html

CFG = config.load()
REGIONS = load_regions(CFG)
SCREEN = Screen.from_config(CFG)
TODAY = date(2026, 11, 16)


def job(title="Software Engineer", company="Acme", locations=("Boston, MA",), posted="2026-11-16", hints=(), **kw):
    return Job.make("test", f"{company}|{title}|{locations}|{kw.get('nid', '')}", company=company, title=title,
                    url=f"https://example.com/{company}/{title}".replace(" ", "-"), locations=list(locations),
                    country_hints=list(hints), posted_at=posted, posted_source="test board")


def verdict(fit=80, auth="unknown", in_scope=True, **kw):
    v = dict(in_scope=in_scope, out_of_scope_reason="", role_family="software_engineering",
             years_required="0-2 years", fit=fit, why="Built ETL pipelines at Siemens.", gap="None significant",
             arrangement="hybrid", remote_restriction="", salary="", authorization=auth,
             authorization_evidence="Posting is silent")
    v.update(kw)
    return v


@pytest.mark.parametrize("loc,expected", [
    ("Boston, MA", ["us"]), ("New York City", ["us"]), ("NYC", ["us"]), ("San Francisco, CA", ["us"]),
    ("Remote - US", ["us"]), ("Remote (US)", ["us"]), ("United States", ["us"]), ("US, CA, Santa Clara", ["us"]),
    ("Seattle, Washington, USA", ["us"]), ("Remote", ["us"]),
    ("Toronto, ON", []), ("Toronto, CA", []), ("Bengaluru, IN", []), ("London", []), ("Berlin, DE", []),
    ("Remote (Canada)", []), ("Tel Aviv, IL", []),
    ("Dubai", ["uae"]), ("Dubai, United Arab Emirates", ["uae"]), ("Abu Dhabi, UAE", ["uae"]),
    ("Dubai, AE", ["uae"]), ("Riyadh, Saudi Arabia", []), ("Dublin; New York, NY", ["us"]),
    ("London / Dubai", ["uae"]),
])
def test_region_from_location_text(loc, expected):
    assert [k for k, r in REGIONS.items() if r.matches(job(locations=(loc,)))] == expected


def test_structured_country_beats_text():
    # "Cambridge" alone would look like Massachusetts; the ATS says GB.
    assert not REGIONS["us"].matches(job(locations=("Cambridge",), hints=("GB",)))
    assert REGIONS["uae"].matches(job(locations=("Head Office",), hints=("ae",)))
    assert REGIONS["us"].matches(job(locations=("HQ",), hints=("United States",)))


def test_preferred_location():
    assert REGIONS["us"].is_preferred(job(locations=("Boston, MA",)))
    assert REGIONS["us"].is_preferred(job(locations=("Waltham, Massachusetts",)))
    assert not REGIONS["us"].is_preferred(job(locations=("Austin, TX",)))
    assert REGIONS["uae"].is_preferred(job(locations=("Dubai, UAE",)))


@pytest.mark.parametrize("title,ok", [
    ("Software Engineer", True), ("Software Engineer I", True), ("Software Engineer II", True),
    ("Data Analyst", True), ("Associate Product Manager", True), ("Product Manager", True),
    ("Forward Deployed Engineer", True), ("Solutions Engineer", True), ("Business Intelligence Analyst", True),
    ("Strategy & Operations Associate", True), ("Analytics Engineer, New Grad 2027", True),
    ("Technical Program Manager", True), ("Chief of Staff", True), ("Customer Success Manager", True),
    ("Technical Consultant", True), ("Applied Scientist", True),
    ("Senior Software Engineer", False), ("Sr. Data Analyst", False), ("Staff Engineer", False),
    ("Software Engineer III", False), ("Engineering Manager", False), ("Software Engineering Intern", False),
    ("Data Science Internship", False), ("Lead Data Engineer", False), ("Director of Product", False),
    ("Account Executive", False), ("Sales Engineer", False), ("Hardware Engineer", False),
    ("Account Manager", False), ("Recruiter", False), ("Principal Product Manager", False),
    ("Solutions Architect", False), ("Barista", False),
])
def test_title_screen(title, ok):
    assert SCREEN.check(title)[0] is ok


def test_early_career_titles():
    assert SCREEN.is_early("Software Engineer, New Grad")
    assert SCREEN.is_early("Software Engineer I")
    assert SCREEN.is_early("Associate Product Manager")
    assert not SCREEN.is_early("Software Engineer, Infrastructure")


def test_screen_jobs_drops_stale_and_respects_early_only():
    ctx = Ctx(regions=REGIONS, today=TODAY, lookback_days=7)
    fresh, stale = job(), job(title="Data Analyst", posted="2026-10-01")
    big_plain, big_early = job(title="Data Engineer", company="Big"), job(title="Data Engineer I", company="Big")
    for j in (big_plain, big_early):
        j.meta["early_only"] = ["us"]
    kept = screen_jobs([fresh, stale, big_plain, big_early], ctx, SCREEN)
    assert [j.title for j in kept] == ["Software Engineer", "Data Engineer I"]


def test_judge_queue_prioritises_entry_level_then_preferred_location():
    ctx = Ctx(regions=REGIONS, today=TODAY, lookback_days=7)
    a = job(title="Software Engineer", locations=("Austin, TX",))
    b = job(title="Software Engineer", locations=("Boston, MA",), company="B")
    c = job(title="Software Engineer, New Grad", locations=("Austin, TX",), company="C")
    old = job(title="Data Analyst", posted="2026-11-10", company="D")
    undated_plain = job(title="Data Analyst", posted=None, company="E")
    undated_early = job(title="Junior Data Analyst", posted=None, company="F")
    done = job(title="Data Engineer", company="G")
    done.verdicts["us"] = verdict()
    jobs = [a, b, c, old, undated_plain, undated_early, done]
    for j in jobs:
        j.regions = ["us"]
    q = judge_queue(jobs, REGIONS["us"], SCREEN, ctx, max_age=2)
    assert [j.company for j in q] == ["F", "C", "B", "Acme"]


def _ready(j, v, stamp="2026-11-16"):
    j.regions, j.first_seen, j.last_seen = ["us"], stamp, stamp
    j.verdicts["us"] = v
    return j


def select(jobs, last="2026-11-15"):
    return digest.select(jobs, REGIONS["us"], TODAY, CFG, last, "2026-11-16")


def test_selection_never_pads_and_excludes_blocked_roles():
    jobs = [
        _ready(job(company="Good"), verdict(82)),
        _ready(job(company="Weak"), verdict(55)),
        _ready(job(company="Senior"), verdict(90, in_scope=False)),
        _ready(job(company="Citizens"), verdict(95, auth="excluded")),
        _ready(job(company="Closed"), verdict(90), stamp="2026-11-15"),     # not seen open this run
    ]
    assert [j.company for j in select(jobs).main] == ["Good"]


def test_freshness_window_and_exceptional_extension():
    jobs = [
        _ready(job(company="Today"), verdict(75)),
        _ready(job(company="Yesterday", posted="2026-11-15"), verdict(75)),
        _ready(job(company="TwoDaysGood", posted="2026-11-14"), verdict(75)),
        _ready(job(company="TwoDaysGreat", posted="2026-11-14"), verdict(90)),
        _ready(job(company="ThreeDaysGreat", posted="2026-11-13"), verdict(95)),
    ]
    assert {j.company for j in select(jobs).main} == {"Today", "Yesterday", "TwoDaysGreat"}
    # After three missed runs the window stretches to cover the gap.
    assert {j.company for j in select(jobs, last="2026-11-13").main} == {
        "Today", "Yesterday", "TwoDaysGood", "TwoDaysGreat", "ThreeDaysGreat"}


def test_first_digest_opens_with_the_whole_catchup_window():
    week_old = _ready(job(company="WeekOld", posted="2026-11-09"), verdict(75))
    assert [j.company for j in select([week_old], last=None).main] == ["WeekOld"]
    assert select([week_old]).main == []


def test_undated_roles_go_to_unverified_section():
    sel = select([_ready(job(company="NoDate", posted=None), verdict(88)), _ready(job(company="Dated"), verdict(80))])
    assert [j.company for j in sel.main] == ["Dated"]
    assert [j.company for j in sel.unverified] == ["NoDate"]


def test_same_requisition_in_several_locations_is_one_row():
    a = _ready(job(locations=("Boston, MA",), nid="1"), verdict(80))
    b = _ready(job(locations=("New York, NY",), nid="2"), verdict(84))
    sel = select([a, b])
    assert len(sel.main) == 1
    assert sel.main[0].locations == ["New York, NY", "Boston, MA"]
    assert sel.main[0].meta["duplicates"] == [a.id]


def test_reposts_of_digested_roles_are_skipped():
    earlier = _ready(job(nid="old", posted="2026-11-01"), verdict(80), stamp="2026-11-02")
    earlier.digested["us"] = "2026-11-02"
    repost = _ready(job(nid="new"), verdict(80))
    other_region_only = _ready(job(company="Elsewhere", nid="x"), verdict(80))
    assert [j.company for j in select([earlier, repost, other_region_only]).main] == ["Elsewhere"]


def test_ranking_fit_then_authorization_then_location_then_freshness():
    jobs = [
        _ready(job(company="A-fit72"), verdict(72, auth="favorable")),
        _ready(job(company="B-fit91-austin", locations=("Austin, TX",)), verdict(91)),
        _ready(job(company="C-fit92-boston"), verdict(92)),
        _ready(job(company="D-fit90-sponsor", locations=("Austin, TX",)), verdict(90, auth="favorable")),
        _ready(job(company="E-fit93-old", locations=("Austin, TX",), posted="2026-11-15"), verdict(93)),
    ]
    assert [j.company[0] for j in select(jobs).main] == ["D", "C", "B", "E", "A"]


def test_render_has_links_dates_and_limitations():
    j = _ready(job(title="Data | Analyst", company="Acme"), verdict(88, salary="$90,000 - $110,000",
               authorization="favorable", authorization_evidence="We sponsor visas"))
    sel = select([j])
    now = datetime(2026, 11, 16, 7, 15, tzinfo=ZoneInfo("America/New_York"))
    body = digest.render(REGIONS["us"], sel, now, ["Coverage is the watchlist."])
    assert f"]({j.url})" in body and body.count(j.url) == 2          # highlight and table row
    assert "Data \\| Analyst" in body                                   # pipes cannot break the table
    assert "Nov 16 (today)" in body and "per test board" in body
    assert "$90,000 - $110,000" in body and "Favorable: “We sponsor visas”" in body
    assert "America/New_York" in body and "## Search limitations" in body
    assert "1 new match" in digest.title(REGIONS["us"], sel, now)


def test_render_empty_day_says_so():
    now = datetime(2026, 11, 16, 8, 0, tzinfo=ZoneInfo("Asia/Dubai"))
    sel = digest.select([], REGIONS["uae"], TODAY, CFG, None, "2026-11-16")
    assert "Nothing was added to pad the list" in digest.render(REGIONS["uae"], sel, now, [])
    assert "no new matches" in digest.title(REGIONS["uae"], sel, now)


def test_store_roundtrip_keeps_verdicts_and_drops_description(tmp_path):
    store = Store(tmp_path / "jobs.jsonl")
    j = job()
    j.description = "long text"
    store.merge(j, "2026-11-15")
    j.verdicts["us"] = verdict()
    store.save()
    again = Store(tmp_path / "jobs.jsonl")
    refetched = job()
    refetched.posted_at = None
    again.merge(refetched, "2026-11-16")
    assert refetched.verdicts["us"]["fit"] == 80
    assert (refetched.first_seen, refetched.last_seen, refetched.posted_at) == ("2026-11-15", "2026-11-16", "2026-11-16")
    assert "long text" not in (tmp_path / "jobs.jsonl").read_text()


def test_date_and_text_helpers():
    assert iso_date("2026-09-09T10:50:29-04:00") == "2026-09-09"
    assert iso_date(1793738172) == "2026-11-03" and iso_date("1786469891368") == "2026-08-11"
    assert long_date("November 16, 2026") == "2026-11-16"
    assert relative_date("Posted Today", TODAY) == "2026-11-16"
    assert relative_date("Posted Yesterday", TODAY) == "2026-11-15"
    assert relative_date("Posted 3 Days Ago", TODAY) == "2026-11-13"
    assert relative_date("Posted 30+ Days Ago", TODAY) == "2026-10-16"
    assert strip_html("&lt;p&gt;Hi&lt;/p&gt;&lt;ul&gt;&lt;li&gt;A &amp;amp; B&lt;/li&gt;&lt;/ul&gt;") == "Hi\nA & B"
