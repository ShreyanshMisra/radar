"""One adapter per hiring platform. Each exposes fetch(session, company, ctx) -> list[Job]."""
from . import amazon, ashby, greenhouse, lever, oracle, simplify, smartrecruiters, workday

ADAPTERS = {
    "greenhouse": greenhouse,
    "ashby": ashby,
    "lever": lever,
    "smartrecruiters": smartrecruiters,
    "workday": workday,
    "oracle": oracle,
    "amazon": amazon,
    "simplify": simplify,
}
