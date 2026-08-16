"""agentware_lib.knowledge.readguard -- refuse UNTRUSTED knowledge BEFORE it is injected (layer: knowledge).

Owns: the read-time content scan (`entry_text_is_poisoned`, `poison_reason`), the
per-corpus suppression pass (`suppressed_entry_ids`) and the shared-id read
denylist join. Nothing else.
Does NOT own: the marker TABLES -> core.orgkeys (`INJECTION_MARKERS`,
`ORG_CONFLICT_MARKER_RE`); the org LINT that reports poisoned entries to an
operator -> ops.orglint; the write-side governance that decides who may put an
entry in `shared/` -> ops.fleet.fleetguard; the ranking that consumes the
surviving entries -> retrieval.

WHY A LINT IS NOT ENOUGH
------------------------
`org lint` already flags an entry carrying an injection marker or a leftover
conflict marker. A lint REPORTS — and reporting happens when a human runs it,
which is not when the entry gets injected. Between those two moments the entry is
read by recall, ranked, and pasted into a session context, which is the only
moment that matters. This module is the refusal at that moment.

R-SEC-02 says never trust external content and never follow instructions embedded
in it. Knowledge becomes external content the instant more than one person can
write it: a shared org corpus is authored by OTHER TEAMS, so `shared/` is
attacker-influenced in the general case. A knowledge base that injects whatever it
holds is prompt injection with a delivery mechanism and a persistence layer.

WHAT IS SCANNED, AND WHAT IS DELIBERATELY NOT
---------------------------------------------
SHARED entries are scanned; a caller's OWN team entries are not. That is not an
oversight — it is where the trust boundary actually is. Your own team's knowledge
is written by you and your teammates through the same toolkit that wrote this
scan; treating it as hostile would mean refusing to inject the corpus the system
exists to inject, and every learning that legitimately QUOTES an injection string
(this file's own tests, the marker table itself, a learning ABOUT prompt
injection) would be suppressed from its author's own recall.

SUPPRESSION IS REPORTED, NEVER SILENT
-------------------------------------
A suppressed entry is returned in a `suppressed` list with its id and reason, so
the payload says "3 results, 1 entry suppressed" rather than quietly showing 3.
A silent filter is indistinguishable from a corpus that simply had nothing to say,
and the one case where the operator most needs to know their knowledge base has
been tampered with is exactly the case a silent filter hides.

Layer deps: knowledge -> core (`orgkeys` for the tables, `config` for the shared
predicate). Pure and read-only apart from reading the entry files it scans.
Determinism: a pure function of (entry, file bytes) — no clock, no network, so
the same corpus suppresses the same ids on every run (INV-1).
"""

import os

import agentware_lib.core.config as config
import agentware_lib.core.orgkeys as orgkeys


def entry_text_is_poisoned(text):
    """True iff `text` carries an injection marker or a leftover conflict marker.

    Case-insensitive on the marker table (an injection does not have to be typed
    in lower case to work), line-anchored on the conflict regex (a `<<<<<<<` in
    the middle of a sentence is prose, at the start of a line it is a broken
    merge).
    """
    return poison_reason(text) is not None


def poison_reason(text):
    """The reason `text` is untrusted, or None. Named so the refusal can SAY why.

    A guard that refuses without a reason gets disabled the first time it is
    wrong, because nobody can tell whether it was wrong.
    """
    if not text:
        return None
    low = text.lower()
    for marker in orgkeys.INJECTION_MARKERS:
        if marker in low:
            return "prompt-injection marker %r" % marker
    for line in text.splitlines():
        if orgkeys.ORG_CONFLICT_MARKER_RE.match(line):
            return "leftover git conflict marker"
    return None


def _entry_is_shared(entry):
    """True iff an index entry is org-shared (origin == 'shared')."""
    return (entry or {}).get("origin") == "shared"


def _denylist_reasons(denylist):
    """Seed the suppression map from the curator-maintained shared/DENYLIST.

    Separate from the content scan because it is a different KIND of refusal: the
    scan is a judgment about the bytes, this is a human saying "retract that one"
    without editing the entry. Keeping them apart means a denylist entry does not
    have to look poisoned, and a poisoned entry does not need a human first.
    """
    return {eid: "listed in the shared read-denylist"
            for eid in (denylist or ()) if eid}


def suppressed_entry_ids(kdir, entries, denylist=None):
    """Ids to REFUSE before injection: `{id: reason}` over SHARED entries only.

    `denylist` is the operator/curator-maintained set of shared ids suppressed by
    fiat (shared/DENYLIST); it is unioned in so a human can retract an entry
    without editing it. Reading an entry file is best-effort — an unreadable
    shared entry is NOT suppressed here, because it also cannot be injected, and
    inventing a suppression for it would report tampering that did not happen.
    """
    out = _denylist_reasons(denylist)
    for entry in entries or ():
        if not isinstance(entry, dict) or not _entry_is_shared(entry):
            continue
        eid = entry.get("id")
        rel = entry.get("path") or ""
        if not eid or not rel or eid in out:
            continue
        try:
            with open(os.path.join(kdir, rel), "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        reason = poison_reason(text)
        if reason:
            out[eid] = reason
    return out


def filter_for_injection(kdir, entries, denylist=None):
    """`(kept, suppressed)` — the pass a caller runs before ranking/injection.

    Returns a NEW list plus `[{'id', 'reason'}]` so the caller can REPORT the
    suppression rather than silently returning fewer results.
    """
    bad = suppressed_entry_ids(kdir, entries, denylist=denylist)
    if not bad:
        return list(entries or []), []
    kept = [e for e in (entries or [])
            if not (isinstance(e, dict) and e.get("id") in bad)]
    suppressed = [{"id": eid, "reason": bad[eid]} for eid in sorted(bad)
                  if any(isinstance(e, dict) and e.get("id") == eid
                         for e in (entries or []))]
    return kept, suppressed


_EXPORTS = [
    "entry_text_is_poisoned",
    "poison_reason",
    "suppressed_entry_ids",
    "filter_for_injection",
]

__all__ = list(_EXPORTS)
