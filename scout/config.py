"""Load config.yaml, companies.yaml and profile.md from the repository root."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(root: Path = ROOT) -> dict:
    cfg = yaml.safe_load((root / "config.yaml").read_text())
    cfg["_root"] = root
    cfg["_companies"] = yaml.safe_load((root / "companies.yaml").read_text()) or []
    cfg["_profile"] = (root / "profile.md").read_text()
    return cfg
