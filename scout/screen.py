"""Cheap title screen. Nothing reaches the model until it passes.

The screen only has to be right about what is obviously out (senior, intern, unrelated
professions). Borderline titles pass: years of experience live in the description, which
only the judge reads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Screen:
    include: re.Pattern
    exclude: re.Pattern
    early: re.Pattern

    @staticmethod
    def from_config(cfg: dict) -> "Screen":
        s = cfg["screen"]
        return Screen(*(re.compile(s[k], re.I) for k in ("include_title", "exclude_title", "early_career_title")))

    def check(self, title: str) -> tuple[bool, str]:
        m = self.exclude.search(title)
        if m:
            return False, f"excluded: {m.group(0).strip().lower()}"
        if not self.include.search(title):
            return False, "no target role keyword"
        return True, ""

    def is_early(self, title: str) -> bool:
        """Title itself signals new-grad / entry level; used to prioritise the judge budget."""
        return bool(self.early.search(title))
