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
    # --- task 99 wave 7 -----------------------------------------------------
    # The SAME cause as the wave-4 block above, one wave later: paying down the
    # test-ownership row for `features/fanout.py` requires the owning file at
    # exactly `tests/unit/features/test_fanout.py`, and the donor already has a
    # file there. Fully DISJOINT, measured: 44 local ids, 24 donor ids, 0
    # shared -- and disjoint for the usual reason, that the two sides test the
    # module from opposite ends. The local file drives the registry primitives,
    # the spin-up preflight and the text renderer through their own boundaries;
    # the donor's is a module-surface file (`ExportsTest`, `LayerTest`,
    # `FeatureReTest`, `LaneRegistryTest`, `ListCommandTest`, `MergePolicyTest`,
    # `OutcomeAndGateTest`) written against the pre-split bodies. Both are worth
    # keeping, so the donor's re-homes to `_surface`, which is what that suffix
    # already means in every row above.
    #
    # The two sibling modules this wave also gave owning files
    # (`fanoutmq/mqintegrate.py`, `fanoutmq/mqteardown.py`) need no row: the
    # donor carries no test at either destination, so no collision exists to
    # pin. Adding speculative rows for them would pin a hazard that does not
    # exist and make the live gate's "exactly this set" assertion a lie.
    "tests/unit/features/test_fanout.py": {
        "local": "tests/unit/features/test_fanout.py",
        "donor": "tests/unit/features/test_fanout_surface.py",
    },
    # --- task 99 wave 8 -----------------------------------------------------
    # The SAME cause a third time. Paying down the test-ownership rows for
    # `ops/kbgit.py` and `ops/kbsync.py` requires the owning files at exactly
    # those two paths (R-MOD-06 fixes the path; there is no alternative name
    # that also satisfies the rule), and the donor already occupies both.
    # Fully DISJOINT, MEASURED rather than assumed: kbgit 47 local ids / 11
    # donor / 0 shared; kbsync 45 local / 12 donor / 0 shared.
    #
    # Disjoint for the usual reason — the two sides come at the module from
    # opposite ends. The donor's files are module-SURFACE tests
    # (`ExportsAndBoundaryTest`, `NonRepoDegradeTest`, `OwningModuleAndFanInTest`,
    # `RegisterKbGitWiringTest`, plus one smoke row each) written against the
    # pre-split bodies; the local files drive the behaviour — `_first_line`'s
    # two-stage fallback, the four pipeline steps, the precondition ORDER, and
    # the fail-loud disposition set. Both are worth keeping, so the donor's
    # re-home to `_surface`, which is what that suffix already means above.
    #
    # `ops/kbpull.py` also gained an owning file this wave and needs NO row: the
    # donor carries no test at `tests/unit/ops/test_kbpull.py` (measured: 0
    # donor ids), so there is no collision to pin, and a speculative row would
    # make the live gate's "exactly this set" assertion a lie. The wave's second
    # kbsync part (`test_kbsync_handlers.py`) is likewise donor-free.
    "tests/unit/ops/test_kbgit.py": {
        "local": "tests/unit/ops/test_kbgit.py",
        "donor": "tests/unit/ops/test_kbgit_surface.py",
    },
    "tests/unit/ops/test_kbsync.py": {
        "local": "tests/unit/ops/test_kbsync.py",
        "donor": "tests/unit/ops/test_kbsync_surface.py",
    },
    # --- task 99 wave 9 -----------------------------------------------------
    # The SAME cause a fourth time. Paying down the test-ownership rows for
    # `retrieval/recallcmd.py` and `retrieval/vectorcache.py` requires the
    # owning files at exactly those two paths (R-MOD-06 fixes the path; there is
    # no alternative name that also satisfies the rule), and the donor already
    # occupies both. Fully DISJOINT, MEASURED rather than assumed: recallcmd 53
    # local ids / 19 donor / 0 shared; vectorcache 39 local / 24 donor / 0
    # shared.
    #
    # Worth stating because this pair is the closest call in the table: the
    # donor's `test_recallcmd.py` carries classes with the SAME NAMES as two of
    # mine (`BuildRecallPayloadTest`, `CmdRecallTest`), so a filename check and
    # a class-name check would both read them as the same tests. The node ids
    # are what actually decide it, and by node id the two sides share nothing --
    # the donor's are module-SURFACE rows (`ExportsTest`, `LayerDisciplineTest`,
    # `RegisterRecallTest` plus a smoke row per handler) written against the
    # pre-split bodies, while the local file drives the behaviour: INV-5's
    # skip-not-break walk, the pointers-only overflow contract, the org scope
    # block and the three argument refusals. Both are worth keeping, so the
    # donor's re-home to `_surface`, which is what that suffix already means
    # above.
    #
    # THREE files this wave gained need NO row and that is not an omission:
    # `test_pinpolicy.py`, `test_workcorpus.py` and the split half
    # `test_recallcmd_work.py` have no donor file at their paths (measured), so
    # no collision exists to pin, and a speculative row would make the live
    # gate's "exactly this set" assertion a lie.
    "tests/unit/retrieval/test_recallcmd.py": {
        "local": "tests/unit/retrieval/test_recallcmd.py",
        "donor": "tests/unit/retrieval/test_recallcmd_surface.py",
    },
    "tests/unit/retrieval/test_vectorcache.py": {
        "local": "tests/unit/retrieval/test_vectorcache.py",
        "donor": "tests/unit/retrieval/test_vectorcache_surface.py",
    },
    # --- task 99 wave 10 ----------------------------------------------------
    # The SAME cause a fifth time. Paying down the test-ownership rows for
    # `retrieval/evalcmd.py` and `retrieval/goldfixture.py` requires the owning
    # files at exactly those two paths (R-MOD-06 fixes the path; there is no
    # alternative name that also satisfies the rule), and the donor already
    # occupies both. Fully DISJOINT, MEASURED rather than assumed: evalcmd 47
    # local ids / 20 donor / 0 shared; goldfixture 27 local / 29 donor / 0
    # shared.
    #
    # Disjoint for the usual reason — the two sides come at the module from
    # opposite ends. The donor's are module-SURFACE files (`ExportsTest`,
    # `LayerDisciplineTest`, `RegisterEvalTest`, `TextFormatterTest`, plus a
    # smoke row per handler; `SuiteConstants`, `ExportsAndLayer`) written
    # against the PRE-split bodies, while the local files drive the behaviour:
    # the two suite runners' DIFFERING except-chains (the row that separates
    # them is an OSError from the gold-fixture scorer, which propagates), the
    # dispatcher's arm precedence, the loader's fourteen refusals and their
    # ORDER. Both are worth keeping, so the donor's re-home to `_surface`,
    # which is what that suffix already means above.
    #
    # THREE files this wave gained need NO row and that is not an omission:
    # `test_evalsuites.py` (a module the donor does not have at all),
    # `test_benchledger.py` and `test_benchswe.py` have no donor file at their
    # paths (measured — the donor keeps one flat `test_bench.py`), so no
    # collision exists to pin, and a speculative row would make the live gate's
    # "exactly this set" assertion a lie.
    "tests/unit/retrieval/test_evalcmd.py": {
        "local": "tests/unit/retrieval/test_evalcmd.py",
        "donor": "tests/unit/retrieval/test_evalcmd_surface.py",
    },
    "tests/unit/retrieval/test_goldfixture.py": {
        "local": "tests/unit/retrieval/test_goldfixture.py",
        "donor": "tests/unit/retrieval/test_goldfixture_surface.py",
    },
    # --- task 99 wave 11 ----------------------------------------------------
    # The SAME cause a sixth time, and the SMALLEST instance of it yet: of the
    # four owning files this wave lands, exactly ONE lands on a path the donor
    # already occupies. Paying down the test-ownership row for
    # `features/skills.py` requires the owning file at exactly that path
    # (R-MOD-06 fixes it; there is no alternative name that also satisfies the
    # rule), and the donor holds it. Fully DISJOINT, MEASURED rather than
    # assumed: 49 local ids / 18 donor / 0 shared.
    #
    # Disjoint for the usual reason -- the two sides come at the module from
    # opposite ends. The donor's is a module-SURFACE file (`ExportsTest`,
    # `BoundaryTest`, `CatalogTest`, a three-case `FrontmatterTest`, `VetTest`,
    # `PinnedRefTest`, `RosterInjectableTest`) written against the PRE-split
    # bodies, while the local file drives the behaviour the split had to
    # preserve: the two validate arms' DIFFERING path spellings and message
    # order, the folded-scalar guard's key test (`name: >` is the literal ">"),
    # and the ORDER of `skill add`'s two external gates. Both are worth
    # keeping, so the donor's re-homes to `_surface`, which is what that suffix
    # already means above.
    #
    # THREE files this wave gained need NO row and that is not an omission:
    # `test_skillmanifest.py` (a module the donor does not have at all -- it is
    # new in this wave), `test_skillsync.py` and `test_skillpropose.py` have no
    # donor file at their paths (measured -- the donor keeps behaviour-named
    # `test_skill_sync.py` / `test_skill_promote.py` at the flat tier), so no
    # collision exists to pin, and a speculative row would make the live gate's
    # "exactly this set" assertion a lie.
    "tests/unit/features/test_skills.py": {
        "local": "tests/unit/features/test_skills.py",
        "donor": "tests/unit/features/test_skills_surface.py",
    },
    # --- task 99 wave 12 ----------------------------------------------------
    # The SAME cause a seventh time. Paying down the test-ownership rows for
    # `ops/orglint.py` and `ops/steering.py` requires the owning files at
    # exactly those two paths (R-MOD-06 fixes them; there is no alternative
    # name that also satisfies the rule), and the donor holds both. Fully
    # DISJOINT, MEASURED rather than assumed: orglint 38 local ids / 14 donor /
    # 0 shared; steering 31 local / 27 donor / 0 shared.
    #
    # Disjoint for the usual reason -- the two sides come at the module from
    # opposite ends. The donor's are module-SURFACE files (`ExportsTest`,
    # `BoundaryTest`, `DsfConstantsTest`, `RegexTest`, plus one smoke row per
    # handler) written against the PRE-split bodies, while the local files
    # drive the behaviour the split had to preserve: the merged-id map threaded
    # ACROSS teams, O6/O8's two per-rule breaks, the O1 arm no on-disk corpus
    # can reach, and the three DSF disjuncts that decide `ok` independently.
    # There IS one name in common on each side (`LintOrgTest`,
    # `FirstTokenVerbTest` / `LintSteeringTest`), which is exactly why the node
    # ids and not the class names decide it. Both are worth keeping, so the
    # donor's re-home to `_surface`, which is what that suffix already means
    # above.
    #
    # ONE file this wave gains needs NO row and that is not an omission:
    # `test_fleettemplates.py` has no donor file at its path (measured -- the
    # donor keeps the behaviour-named `tests/unit/knowledge/test_templates_lint.py`,
    # which is the very mis-aiming R-MOD-06 exists to stop), so no collision
    # exists to pin, and a speculative row would make the live gate's "exactly
    # this set" assertion a lie.
    "tests/unit/ops/test_orglint.py": {
        "local": "tests/unit/ops/test_orglint.py",
        "donor": "tests/unit/ops/test_orglint_surface.py",
    },
    "tests/unit/ops/test_steering.py": {
        "local": "tests/unit/ops/test_steering.py",
        "donor": "tests/unit/ops/test_steering_surface.py",
    },
    # --- task 99 wave 13 ----------------------------------------------------
    # The SAME cause an eighth time. Paying down the test-ownership row for
    # `features/curationcmd.py` requires the owning file at exactly that path
    # (R-MOD-06 fixes it; there is no alternative name that also satisfies the
    # rule), and the donor holds it. Fully DISJOINT, MEASURED rather than
    # assumed: 43 local ids / 21 donor / 0 shared.
    #
    # Disjoint for the usual reason -- the two sides come at the module from
    # opposite ends. The donor's is a module-SURFACE file (`ExportsTest`,
    # `BoundaryTest`, `RegistrarTest`, plus a smoke row per handler in
    # `CmdLearnTest`/`CmdDecideTest`) written against the PRE-split bodies,
    # while the local file drives the four shared INPUT CONTRACTS: the write
    # prologue's gate seeing the caller's own `--to-shared`, the `--topic`
    # grammar's three verdicts, the near-duplicate threshold from both sides,
    # and the author asymmetry between the two verbs. Both are worth keeping,
    # so the donor's re-homes to `_surface`, which is what that suffix already
    # means above.
    #
    # Worth saying out loud because it is the closest call in the table since
    # wave 9: the donor's `CmdLearnTest`/`CmdDecideTest` share their names with
    # two classes in this wave's SECOND half, `test_curationcmd_handlers.py` --
    # a DIFFERENT path, so there is no collision to pin there and no row for
    # it, and the node ids (not the class names) are what decide it either way.
    #
    # THREE files this wave gains need NO row and that is not an omission:
    # `test_curationwrite.py` (a module the donor does not have at all -- it is
    # new in this wave), `test_curationcmd_handlers.py` and
    # `tests/unit/knowledge/test_curationingest.py` have no donor file at their
    # paths (measured -- the donor keeps one `tests/unit/knowledge/
    # test_curation.py` covering the package), so no collision exists to pin,
    # and a speculative row would make the live gate's "exactly this set"
    # assertion a lie.
    "tests/unit/features/test_curationcmd.py": {
        "local": "tests/unit/features/test_curationcmd.py",
        "donor": "tests/unit/features/test_curationcmd_surface.py",
    },
    # WAVE 15 adds THREE at once -- the largest single-wave batch since task 58,
    # and not because the wave was careless: paying down three test-ownership
    # rows in one commit means three modules gain their owning file, and the
    # donor's own decomposition already wrote a granular file for each of them
    # (its wave-6 seams 22 and 25, plus its knowledge-layer graph unit).
    #
    # Fully DISJOINT, MEASURED rather than assumed: 0 shared node ids across all
    # three pairs (160 local ids / 54 donor). Disjoint for the reason this table
    # keeps recording -- the donor's files are module-SURFACE files
    # (`ExportsTest`, `BoundaryTest`, `RegistrarTest` plus a smoke row per
    # handler) written against the pre-split bodies, while the local files drive
    # the DECISIONS these handlers own: the flag PRECEDENCE and exit-code table
    # of `update`, what `index sync` PERSISTS versus prints, and the
    # direct-vs-graph dispatch of `query`. Both are worth keeping, so the
    # donor's re-home to `_surface`, which is what that suffix already means
    # above.
    "tests/unit/features/test_updatecmd.py": {
        "local": "tests/unit/features/test_updatecmd.py",
        "donor": "tests/unit/features/test_updatecmd_surface.py",
    },
    "tests/unit/features/test_indexcmd.py": {
        "local": "tests/unit/features/test_indexcmd.py",
        "donor": "tests/unit/features/test_indexcmd_surface.py",
    },
    "tests/unit/knowledge/test_graph.py": {
        "local": "tests/unit/knowledge/test_graph.py",
        "donor": "tests/unit/knowledge/test_graph_surface.py",
    },
    # WAVE 17 adds FOUR at once, one more than wave 15's batch and for the same
    # reason at a larger scale: paying down four test-ownership rows in one
    # commit means four modules gain their owning file, and the donor's own
    # decomposition already wrote a granular file for every one of them.
    #
    # Fully DISJOINT, MEASURED rather than assumed: 0 shared node ids across all
    # four pairs (146 local ids / 75 donor). Disjoint for the reason this table
    # keeps recording, and the four are unusually clear cases of it. The donor's
    # files are module-SURFACE files -- `ExportsTest`, `BoundaryTest`,
    # `RegistrarTest`, `Gate1..Gate4` one class per gate FUNCTION, `RegistryPath`
    # / `LoadRegistry` / `AddMember` one per registry PRIMITIVE -- written
    # against the pre-split bodies and asking "does this symbol still work".
    # The local files ask what the wave-17 paydown made askable: whether every
    # `init` scaffold step is idempotent ON ITS OWN, whether a SKIP means
    # `passed: true` on both of the gate's no-op paths, whether both `audit`
    # renderers see one assembled check list, and whether `team list` is
    # entitled to an identity. Both are worth keeping, so the donor's re-home to
    # `_surface`, which is what that suffix already means above.
    #
    # ONE file this wave gains needs NO row and that is not an omission:
    # `tests/unit/features/test_setupcmd_profile.py` is a path the donor does
    # not occupy (it keeps its `profile` coverage inside its own
    # `test_setupcmd.py`, which the row above already pins), so no collision
    # exists to pin and a speculative row would make the live gate's "exactly
    # this set" assertion a lie.
    "tests/unit/features/test_setupcmd.py": {
        "local": "tests/unit/features/test_setupcmd.py",
        "donor": "tests/unit/features/test_setupcmd_surface.py",
    },
    "tests/unit/ops/test_audit.py": {
        "local": "tests/unit/ops/test_audit.py",
        "donor": "tests/unit/ops/test_audit_surface.py",
    },
    "tests/unit/ops/test_releasegate.py": {
        "local": "tests/unit/ops/test_releasegate.py",
        "donor": "tests/unit/ops/test_releasegate_surface.py",
    },
    "tests/unit/ops/test_team.py": {
        "local": "tests/unit/ops/test_team.py",
        "donor": "tests/unit/ops/test_team_surface.py",
    },
    # WAVE 18 adds THREE, from the same cause as every block since wave 4: the
    # ownership ratchet fixes the owning file's path at
    # `tests/unit/<layer>/test_<stem>.py`, and the donor's own decomposition
    # already wrote a granular file at three of the four this wave needed.
    #
    # Fully DISJOINT, MEASURED rather than assumed: 0 shared node ids across all
    # three pairs (168 local ids / 74 donor). The donor's are module-SURFACE
    # files -- `BoundaryTest`, `CommandSurfaceTest`, `ClassifyRecoveryTest` and
    # their siblings -- asking whether each exported symbol still answers. The
    # local files ask what the wave-18 paydown made askable: which entry wins a
    # duplicate id in an index nobody validated, whether the stem set is really
    # the UNION of two independent sources, and whether each of a fold's four
    # tallies is a function of a step ALONE or of a step and its neighbour.
    # Both are worth keeping, so the donor's re-home to `_surface`, which is
    # what that suffix already means above.
    #
    # THREE files this wave gains need NO row, and that is not an omission:
    # `tests/unit/ops/test_metricspricing.py` and the two split siblings
    # (`test_mcp_router.py`, `test_metricspricing_table.py`) are paths the donor
    # does not occupy at all -- measured, 0 donor node ids on each -- so no
    # collision exists to pin, and a speculative row would make the live gate's
    # "exactly this set" assertion a lie.
    "tests/unit/features/test_mcp.py": {
        "local": "tests/unit/features/test_mcp.py",
        "donor": "tests/unit/features/test_mcp_surface.py",
    },
    "tests/unit/knowledge/test_worklog.py": {
        "local": "tests/unit/knowledge/test_worklog.py",
        "donor": "tests/unit/knowledge/test_worklog_surface.py",
    },
    "tests/unit/ops/test_failureladder.py": {
        "local": "tests/unit/ops/test_failureladder.py",
        "donor": "tests/unit/ops/test_failureladder_surface.py",
    },
}

#: The layered rows above -- the collisions a RELAYERING or an OWNERSHIP payoff
#: CREATED, as opposed to the shared-path hazards the two trees arrived with.
#: Seven came from the task-58 relayering; five more from task 99 wave 4, one
#: from wave 7, two from wave 8, two from wave 9, two from wave 10, one from
#: wave 11, two from wave 12, one from wave 13, three from wave 15, four
#: from wave 17 -- the largest single-wave batch in the table -- and three from
#: wave 18, where
#: paying down a test-ownership row put
#: the owning file on a path the donor already occupies. Named so the live gate can assert exactly
#: this set is pinned and free, and so the next one has to come back here.
RELAYERING_COLLISIONS = tuple(sorted(
    rel for rel in COLLISION_DESTINATIONS if rel.startswith("tests/unit/")))
