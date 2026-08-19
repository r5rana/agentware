"""Shared header for the `test_skill_promote` suite, split out by task 58.

Holds every import, helper, constant and base class the split
parts share, so no part duplicates a definition and none can
drift from another.
"""

import json
import os
import unittest
try:
    from tests._fixtures import (SyntheticKBTestCase, build_synthetic_kb,
                                 load_cli)
except ImportError:  # allow `python3 -m unittest tests.unit.features.test_skill_promote`
    from _fixtures import (SyntheticKBTestCase, build_synthetic_kb, load_cli)
def _learn(eid, tags, summary, body_terms):
    """Build one learnings entry. `body_terms` is distinctive vocabulary so
    unrelated entries never accrue a stray token-Jaccard edge."""
    slug = eid.replace("learn-", "")
    return {
        "id": eid,
        "title": eid,
        "category": "learnings",
        "path": "learnings/%s.md" % slug,
        "tags": list(tags),
        "created": "2026-05-01",
        "last_verified": "2026-05-01",
        "author": "testuser",
        "summary": summary,
        "body": "# %s\n\n%s\n" % (eid, " ".join(body_terms)),
    }
_TIGHT = [
    _learn("learn-pay-idempotency", ["payment", "refund", "idempotency"],
           "Make refund submission idempotent under retries.",
           ["idempotency", "key", "dedupe", "submit", "ledger"]),
    _learn("learn-pay-retry", ["payment", "refund", "retry"],
           "Retry refund settlement with capped backoff.",
           ["retry", "backoff", "settlement", "queue", "attempt"]),
    _learn("learn-pay-webhook", ["payment", "refund", "webhook"],
           "Verify refund webhook signatures before applying.",
           ["webhook", "signature", "verify", "callback", "reconcile"]),
]
_CHAIN = [
    _learn("learn-chain-a", ["alpha", "bridge-ab"],
           "Aardvark azimuth anchor aperture almanac.",
           ["aardvark", "azimuth", "anchor", "aperture", "almanac"]),
    _learn("learn-chain-b", ["bridge-ab", "bridge-bc"],
           "Beacon bramble bison bulwark basalt.",
           ["beacon", "bramble", "bison", "bulwark", "basalt"]),
    _learn("learn-chain-c", ["bridge-bc", "gamma"],
           "Gadget gimbal gully granite garnet.",
           ["gadget", "gimbal", "gully", "granite", "garnet"]),
]
_KERNEL_SUMMARY = [
    _learn("learn-toolkit-recall", ["toolkit", "promote", "recall-note"],
           "Run scripts/agentware recall before editing AGENTS.md rules.",
           ["recall", "budget", "topk", "curated", "ladder"]),
    _learn("learn-toolkit-learn", ["toolkit", "promote", "learn-note"],
           "Promote via scripts/agentware learn; never hand-edit .claude files.",
           ["promote", "durable", "supersede", "handle", "verify"]),
    _learn("learn-toolkit-audit", ["toolkit", "promote", "audit-note"],
           "scripts/agentware audit --stale flags drift in .claude/skills.",
           ["audit", "stale", "drift", "orphan", "provenance"]),
]
_COVERED_DEBUG = [
    _learn("learn-dbg-repro", ["systematic-debugging", "reproduce"],
           "Reproduce the failure deterministically first.",
           ["reproduce", "deterministic", "seed", "harness", "capture"]),
    _learn("learn-dbg-bisect", ["systematic-debugging", "bisect"],
           "Bisect the regression to isolate the culprit commit.",
           ["bisect", "regression", "isolate", "culprit", "window"]),
    _learn("learn-dbg-hypothesis", ["systematic-debugging", "hypothesis"],
           "Form one hypothesis and instrument before changing code.",
           ["hypothesis", "instrument", "evidence", "confirm", "lock"]),
]
_CACHE = [
    _learn("learn-cache-lru", ["cache-eviction", "lru"],
           "Evict least-recently-used entries under memory pressure.",
           ["lru", "pressure", "victim", "recency", "slab"]),
    _learn("learn-cache-ttl", ["cache-eviction", "ttl"],
           "Expire entries past their time-to-live on read.",
           ["ttl", "expire", "clock", "lazy", "sweep"]),
    _learn("learn-cache-stampede", ["cache-eviction", "stampede"],
           "Guard against a thundering-herd cache stampede.",
           ["stampede", "herd", "lock", "coalesce", "singleflight"]),
]
if __name__ == "__main__":
    import unittest
    unittest.main()
