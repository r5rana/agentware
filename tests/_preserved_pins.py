"""The COLLISION PIN table — where a same-named test file lands, per side.

Split out of `tests/_preserved_freeze.py` at task 99 wave 4, at the obligation
and not at the midpoint: this file is pure DATA — the decided destination of
every path both trees carry a test file on — while the host is the derivation
and the verification that read it. The host carries a shrink-only
`file_over_cap` row, and an OWNERSHIP payoff that adds pins would otherwise have
to worsen that row to record what it landed.

The boundary is checkable: nothing here imports, computes or verifies. It is a
table and the tuple derived from it.

`_preserved_freeze` re-exports both names, so `COLLISION_DESTINATIONS` and
`RELAYERING_COLLISIONS` stay importable from where every consumer already
imports them.
"""

#: The `tests/test_skill_promote.py` path collision, resolved. Both sides carry
#: a same-named file with DISJOINT content (0 shared node ids of 30 local and 8
#: donor), so a same-path copy destroys one side entirely and no filename-based
#: check can see it. Destinations are pinned HERE, before any donor test is
#: copied, and asserted free in both trees by the gate.
#:
#: LOCAL keeps the name per the plan's decided conflict resolution ("LOCAL keeps
#: the name; the donor ledger re-homes to `skill suggest`/`suggestions`/
#: `promote`"); the donor ledger's tests re-home alongside its verb family.
#:
#: TASK 58 ADDED SEVEN MORE, and they are collisions this repo CREATED. Layering
#: the local corpus moves 191 flat files onto the paths the donor's own
#: decomposition already occupies, so seven local files land on a donor file
#: that is still PENDING in the backlog. Every one is fully DISJOINT (0 shared
#: ids of 208 local and 194 donor), because the two sides test the same module
#: from opposite ends: the local file drives the CLI, while the donor file is a
#: module-surface test that, in its own words, "imports ONLY the module under
#: test, never the entrypoint". Both are worth keeping, so both are pinned --
#: LOCAL keeps the layered name (the task-9 rule), and the donor's re-homes to
#: `_surface` to say what it is rather than which tree it came from.
#:
#: Pinning them HERE, in the commit that creates the hazard, is the whole point:
#: the day someone ports the donor's `tests/unit/retrieval/test_acr.py` the
#: destination is already decided, instead of a blanket copy silently deleting
#: 24 local tests that no filename check would notice.
COLLISION_DESTINATIONS = {
    "tests/test_skill_promote.py": {
        "local": "tests/unit/features/test_skill_promote.py",
        "donor": "tests/unit/features/test_skill_suggest.py",
    },
    "tests/unit/core/test_frontmatter.py": {
        "local": "tests/unit/core/test_frontmatter.py",
        "donor": "tests/unit/core/test_frontmatter_surface.py",
    },
    "tests/unit/features/test_dream.py": {
        "local": "tests/unit/features/test_dream.py",
        "donor": "tests/unit/features/test_dream_surface.py",
    },
    "tests/unit/ops/test_freshness.py": {
        "local": "tests/unit/ops/test_freshness.py",
        "donor": "tests/unit/ops/test_freshness_surface.py",
    },
    "tests/unit/ops/test_metrics.py": {
        "local": "tests/unit/ops/test_metrics.py",
        "donor": "tests/unit/ops/test_metrics_surface.py",
    },
    "tests/unit/retrieval/test_acr.py": {
        "local": "tests/unit/retrieval/test_acr.py",
        "donor": "tests/unit/retrieval/test_acr_surface.py",
    },
    "tests/unit/retrieval/test_ledger.py": {
        "local": "tests/unit/retrieval/test_ledger.py",
        "donor": "tests/unit/retrieval/test_ledger_surface.py",
    },
    "tests/unit/retrieval/test_scorecard.py": {
        "local": "tests/unit/retrieval/test_scorecard.py",
        "donor": "tests/unit/retrieval/test_scorecard_surface.py",
    },
    # --- task 99 wave 4 -----------------------------------------------------
    # A SECOND cause of the same hazard, and the reason this table is keyed by
    # path rather than by task: the R-MOD-06 ownership rule requires a module's
    # owning test at EXACTLY `tests/unit/<layer>/test_<stem>.py`, and for these
    # five modules the donor already occupies that path with its own granular
    # unit file. Writing the owning file the ownership ratchet demands therefore
    # CREATES the collision -- it cannot be avoided by choosing another name
    # without failing the other gate. Both files are worth keeping and they are
    # not duplicates: the donor's were written against the PRE-split bodies (its
    # own "wave-4/wave-5 seam" numbering) and describe themselves as importing
    # only the module under test, which is what the `_surface` suffix already
    # means in the eight rows above.
    "tests/unit/ops/test_derive.py": {
        "local": "tests/unit/ops/test_derive.py",
        "donor": "tests/unit/ops/test_derive_surface.py",
    },
    "tests/unit/ops/test_loopanalytics.py": {
        "local": "tests/unit/ops/test_loopanalytics.py",
        "donor": "tests/unit/ops/test_loopanalytics_surface.py",
    },
    "tests/unit/ops/test_loophealth.py": {
        "local": "tests/unit/ops/test_loophealth.py",
        "donor": "tests/unit/ops/test_loophealth_surface.py",
    },
    "tests/unit/ops/test_looptrace.py": {
        "local": "tests/unit/ops/test_looptrace.py",
        "donor": "tests/unit/ops/test_looptrace_surface.py",
    },
    "tests/unit/ops/test_metricscmd.py": {
        "local": "tests/unit/ops/test_metricscmd.py",
        "donor": "tests/unit/ops/test_metricscmd_surface.py",
    },
}

#: The layered rows above -- the collisions a RELAYERING or an OWNERSHIP payoff
#: CREATED, as opposed to the shared-path hazards the two trees arrived with.
#: Seven came from the task-58 relayering; five more from task 99 wave 4, where
#: paying down a test-ownership row put the owning file on a path the donor
#: already occupies. Named so the live gate can assert exactly this set is
#: pinned and free, and so the next one has to come back here.
RELAYERING_COLLISIONS = tuple(sorted(
    rel for rel in COLLISION_DESTINATIONS if rel.startswith("tests/unit/")))
