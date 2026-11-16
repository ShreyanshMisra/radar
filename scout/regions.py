"""Decide which configured region(s) a posting belongs to.

Structured country data from the ATS wins. Without it the location text is pattern-matched,
deliberately on the permissive side: a wrong guess costs one model call, and the judge is
told to reject postings that turn out to be elsewhere, while a miss is silent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import Job

_STATES = ("AL|AK|AZ|AR|CA|CO|CT|DC|DE|FL|GA|HI|IA|ID|IL|IN|KS|KY|LA|MA|MD|ME|MI|MN|MO|MS|MT|NC|ND|NE|NH|NJ|"
           "NM|NV|NY|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VA|VT|WA|WI|WV|WY")
_STATE_NAMES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Hawaii|Idaho|"
                "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
                "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|"
                "North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|"
                "South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|"
                "District of Columbia")
_CITIES = ("San Francisco|SF Bay|Bay Area|NYC|Boston|Seattle|Austin|Chicago|Los Angeles|Mountain View|Palo Alto|"
           "Menlo Park|Sunnyvale|San Jose|Santa Clara|San Diego|Denver|Atlanta|Miami|Dallas|Houston|Philadelphia|"
           "Pittsburgh|Portland|Phoenix|Minneapolis|Detroit|Raleigh|Durham|Charlotte|Nashville|Salt Lake City|"
           "Washington,? D\\.?C|Cambridge, MA|Redmond|Bellevue|Brooklyn|Manhattan|Jersey City|Irvine|Boulder")

BUILTIN = {
    # ", CA" also means Canada and ", IN" India in some feeds; `not_pattern` below settles those.
    "us": (rf"\b(United States|USA|U\.S\.A?\.?|US)\b|\b({_STATE_NAMES})\b|(,|-|\bUS,?)\s*({_STATES})\b"
           rf"|\b({_CITIES})\b|^({_STATES})$"),
    "us_not": (r"\b(Canada|Toronto|Vancouver|Montreal|Ontario|British Columbia|India|Bengaluru|Bangalore|Hyderabad|"
               r"Pune|Mumbai|Gurgaon|Gurugram|Chennai|Noida|Delhi|Germany|Berlin|Munich|Israel|Tel Aviv|Morocco|"
               r"Australia|New South Wales|Georgia, GE|Tbilisi|Indonesia|Jakarta|Argentina|Buenos Aires|"
               r"Cambridge, (UK|England)|United Kingdom|London)\b"),
}
_US_EXPLICIT = re.compile(r"\b(United States|USA|U\.S\.A?\.?)\b|\bUS\b", re.I)
_BARE_REMOTE = re.compile(r"^\W*(remote|anywhere|work from home|distributed)\W*$", re.I)


@dataclass
class Region:
    key: str
    name: str
    flag: str
    timezone: str
    countries: set[str]
    pattern: re.Pattern
    not_pattern: Optional[re.Pattern]
    preferred: Optional[re.Pattern]
    preferred_label: str
    claim_bare_remote: bool
    brief: str
    raw: dict = field(default_factory=dict)

    @staticmethod
    def from_config(key: str, c: dict) -> "Region":
        def rx(v: Optional[str], case: bool = False) -> Optional[re.Pattern]:
            if not v:
                return None
            if v.startswith("builtin:"):
                # The built-in US pattern is case-sensitive on purpose: ", IN" is Indiana,
                # ", in" is a preposition.
                return re.compile(BUILTIN[v.split(":", 1)[1]])
            return re.compile(v, 0 if case else re.I)
        return Region(
            key=key, name=c["name"], flag=c.get("flag", ""), timezone=c.get("timezone", "UTC"),
            countries={x.upper() for x in c.get("countries", [])},
            pattern=rx(c["location_pattern"]), not_pattern=rx(c.get("not_pattern")),
            preferred=rx(c.get("preferred_pattern")), preferred_label=c.get("preferred_label", ""),
            claim_bare_remote=bool(c.get("claim_bare_remote")), brief=c.get("brief", "").strip(), raw=c,
        )

    def matches(self, job: Job) -> bool:
        if job.country_hints:
            return bool(self.countries & set(job.country_hints))
        for loc in job.locations:
            if self._matches_text(loc):
                return True
        if not job.locations or all(_BARE_REMOTE.match(l) for l in job.locations):
            return self.claim_bare_remote
        return False

    def _matches_text(self, loc: str) -> bool:
        # Feeds join several sites into one string ("Dublin; New York, NY"), so test each part.
        for part in re.split(r"\s*[;|/]\s*|\s+or\s+|\s+&\s+", loc):
            if not part or not self.pattern.search(part):
                continue
            if self.not_pattern and self.not_pattern.search(part) and not (
                    self.key == "us" and _US_EXPLICIT.search(part)):
                continue
            return True
        return False

    def is_preferred(self, job: Job) -> bool:
        return bool(self.preferred and any(self.preferred.search(l) for l in job.locations))


def load_regions(cfg: dict) -> dict[str, Region]:
    return {k: Region.from_config(k, v) for k, v in cfg["regions"].items()}


def assign(job: Job, regions: dict[str, Region]) -> list[str]:
    return [k for k, r in regions.items() if r.matches(job)]
