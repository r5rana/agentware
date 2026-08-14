"""agentware_lib.ops.fleet.fleetguard -- shared-write governance + org read guards (layer: ops).

Owns: the shared-write governance guard (wave-6 seam 3) -- guard_shared_write,
which REFUSES a default team-loop write into shared/ unless a curator passes
--to-shared, and its curator predicate _is_curator (a tolerant token match
against shared/OWNERS.md) -- plus the org READ guards the org linter and the
recall path consult: the prompt-injection marker table (INJECTION_MARKERS), the
leftover-conflict-marker regex (ORG_CONFLICT_MARKER_RE) and the shared-id
read-denylist reader (_org_read_denylist).
Does NOT own: the org-root resolution and the committed telemetry sink it
consults (agentware_lib.ops.fleet.fleetshard -- _fleet_org_root is reached
through that OWNER module so monkeypatching it still propagates); the fleet
reporter (agentware_lib.ops.fleet.fleetreport); the per-team template contract
(agentware_lib.ops.fleet.fleettemplates); the O1..O13 org lint that CONSUMES
these constants (agentware_lib.ops.orglint); the shared-root / team path
inference itself (agentware_lib.core.config).
Layer deps: ops -> core (config/orgkeys/routing) + the SAME-PACKAGE sibling
agentware_lib.ops.fleet.fleetshard (module alias, one direction only -- fleetshard
imports nothing from this package, so the package graph stays a DAG).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Determinism: every function here is pure/read-only over the on-disk corpus; a
missing OWNERS.md or DENYLIST degrades to "not a curator" / the empty set rather
than raising, and a non-org (flat) kdir is ALWAYS allowed (byte-identical to a
flat single-operator install, INV-1 / R-ORG-04).
"""

import os
import re

import agentware_lib.core.config as config
import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.core.routing as routing
import agentware_lib.ops.fleet.fleetshard as fleetshard


def _is_curator(org_root, handle):
    """True iff `handle` is listed in shared/OWNERS.md (a curator). Tolerant."""
    if not handle:
        return False
    p = os.path.join(org_root or "", orgkeys.ORG_SHARED_DIR, "OWNERS.md")
    try:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read().lower()
    except OSError:
        return False
    # OWNERS.md lists curators in a table; a handle token match is sufficient.
    return handle.lower() in text


def guard_shared_write(kdir, to_shared=False):
    """Refuse a default team-loop write into shared/ unless it's a curator --to-shared.

    Returns (ok, error). The default agent loop writes to its OWN teams/<team>/;
    a write whose resolved kdir is the shared/ root is the org-wide, highest-blast-
    radius path (CR-gated). Allowed ONLY when to_shared is set AND the resolved
    handle is a curator in shared/OWNERS.md. A non-org (flat) kdir is always allowed
    (byte-identical to today). Pure/read-only.
    """
    if not config._kdir_is_shared_root(kdir):
        return True, None  # team/flat write — always allowed
    if not to_shared:
        return False, ("shared/ write refused: the default team loop cannot write "
                       "org-wide knowledge. Re-run with --to-shared as a curator "
                       "(CR-gated; see shared/OWNERS.md).")
    org_root = fleetshard._fleet_org_root(kdir) or config.resolve_org_root()
    handle = routing.resolve_user_handle() or os.environ.get("USER")
    if not _is_curator(org_root, handle):
        return False, ("shared/ write refused: --to-shared requires a curator "
                       "handle listed in shared/OWNERS.md (got %r)." % handle)
    return True, None


INJECTION_MARKERS = (
    "ignore previous instructions", "ignore all previous instructions",
    "disregard the above", "you are now", "system prompt:",
    "<|im_start|>", "assistant:", "### instruction",
)


ORG_CONFLICT_MARKER_RE = re.compile(r"^(<{7}|={7}|>{7})(\s|$)")


def _org_read_denylist(org_root):
    """Set of shared ids suppressed from recall (shared/DENYLIST). Tolerant."""
    ids = set()
    p = os.path.join(org_root, orgkeys.ORG_DENYLIST_REL)
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if s and not s.startswith("#"):
                    ids.add(s)
    except OSError:
        pass
    return ids


_EXPORTS = [
    '_is_curator',
    'guard_shared_write',
    'INJECTION_MARKERS',
    'ORG_CONFLICT_MARKER_RE',
    '_org_read_denylist',
]
