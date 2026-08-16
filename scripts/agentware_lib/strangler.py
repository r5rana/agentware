"""agentware_lib.strangler — the STRANGLER ROUTER: which tree serves this verb (layer: cli).

Owns: ``PORTED_VERBS`` (the routing allowlist — EMPTY at this commit), ``GLOBAL_OPTIONS``
(the top-level option-arity table the argv scan needs), ``verb_of`` (the scan PAST the
global flags to the first positional), ``route`` (the allowlist decision) and ``dispatch``
(the two-way call). This is the whole of the strangler's control flow.
Does NOT own: the parser (agentware_lib.cli.assemble_parser / build_parser), any verb
HANDLER (each lives in its owning agentware_lib module — see agentware_lib._registration
for the verb->module map), or the legacy passthrough itself (``scripts/agentware`` execs
``scripts/agentware_legacy.py`` into its own globals; see that entrypoint's docstring).
Layer deps: cli -> core, features, knowledge, ops, retrieval. This module imports NOTHING
from the lib AT MODULE SCOPE — the routing decision must be payable without importing the
tree it may well not route to. ``agentware_lib.cli`` is imported INSIDE ``dispatch``, only
on the branch that actually needs it.
Fan-in: 0 — no lib module imports this one; its only caller is the ``scripts/agentware``
entrypoint, which is the facade and not a module (see catalog/modules.json
depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY LEGACY-DEFAULT (feature 260813 task 29)
-------------------------------------------
The obvious router — "route to the lib if the verb appears in the modular registration
tuple" — is FATAL, and was proven so empirically before this file existed: every verb
present on both sides would go to the modular implementation, and those implementations
are collectively missing 28 surface elements, including ``plan verify-premises`` and
``plan claim`` (the R-GND-01 grounding gate, 4 loop call sites), ``update --self-heal``
(6 sites) and ``skill sync`` (5 sites). Nothing would turn red; the loop would simply
stop grounding its plans.

So the default is inverted. ``PORTED_VERBS`` is an EXPLICIT allowlist that starts EMPTY:
a verb is served by the modular core only once it is written down here, and it is written
down only after its own promotion gate proves its surface a superset of the UNION fixture
(``tests/fixtures/surface-union.json``). Everything not written down is served by the
preserved monolith, so at this commit behaviour is byte-identical to ``aw-pre-merge``.
The allowlist grows one verb per port (tasks 37..54), the legacy module shrinks under the
ratchet, and at task 56 the allowlist and this module's ``legacy`` branch are deleted
together with the passthrough.

WHY THE ARGV SCAN
-----------------
``sys.argv[1]`` is NOT the verb. The top-level parser carries a global ``--format`` option
BEFORE the subparsers action, so ``agentware --format json recall "x"`` has ``--format``
at argv[1] and ``json`` at argv[2] — a naive router reads the option's VALUE as the verb.
That misroute is silent while the allowlist is empty (``json`` is in no allowlist, so it
falls to legacy and works), which is exactly why it is gated NOW, before the first port
makes it a live wrong-implementation bug.

The scan is the smallest thing that is correct: walk tokens, consume each global option's
declared values, honour ``--`` as the end-of-options marker, and return the first
remaining positional. ``GLOBAL_OPTIONS`` is a DECLARED table rather than a parser
introspection because introspecting means building the parser, which means importing the
whole tree to decide whether to import the whole tree. The table is kept honest by a gate
(``tests/test_strangler_routing.py``) that diffs it against the root node of the union
surface fixture, so a global option added on either side reddens rather than misroutes.

Stdlib-only, no I/O, no imports at module scope: this module is on the hot path of every
single invocation of the CLI, including the ones the loop makes in a tight fanout lane.
"""

#: The routing ALLOWLIST — verbs served by ``agentware_lib`` instead of the preserved
#: monolith. EMPTY at task 29 by design (see the module docstring): legacy is the default
#: and a verb joins this set only after its promotion gate passes. Deleted with the
#: passthrough at task 56, when the lib serves everything and there is nothing to route.
PORTED_VERBS = frozenset({
    # task 37 — the FIRST port. `migrate` owns the KB schema interlock
    # (agentware_lib.features.migratecmd): ONE schema version, ONE migration
    # table, ONE `.schema` writer, reached by BOTH dispatch arms for as long as
    # the monolith is still serving verbs.
    "migrate",
    # task 38 — the first ported KB WRITER. `relate` is LOCAL-ONLY (the donor has
    # no equivalent), so the whole capability rides on this row; its handler is
    # named in agentware_lib.features.migratecmd._KB_WRITER_TARGETS in the same
    # commit, or it would dispatch straight past the older-CLI schema refusal.
    "relate",
    # task 39 — the LOOP-CRITICAL port, and the largest: all NINE `plan` subverbs
    # (lint / verify-premises / new / add-task / set-status / set-state /
    # state-backfill / claim / target-dirs) across twelve lib modules. This row is
    # deliberately the LAST edit of the task: `agentware.sh` calls `plan
    # verify-premises` and `plan claim` on every iteration, so routing `plan` to an
    # incomplete lib arm would break the loop that executes this very plan. It is
    # appended only after the registrar was proven to reproduce the union fixture's
    # nine leaves and every flag, and after the 141 ported definitions were DELETED
    # from the monolith (not merely shadowed) with a re-export block keeping
    # `CLI.<name>` and the dispatch gate's handler-identity checks resolving.
    "plan",
    # task 40 — `trust` manages the HOME-pinned publisher signing key. NOT a KB
    # writer (it writes ~/.agentware/allowed_signers, outside the knowledge dir),
    # so it needs no _KB_WRITER_TARGETS row — the interlock's correspondence
    # check keys on _KB_WRITER_VERBS, which does not list it.
    "trust",
    # task 41 — the SIGNED SemVer-tag update channel, replacing the donor's
    # plain-fetch updater. `update` writes ~/.agentware state, NOT the knowledge
    # dir, so it needs no _KB_WRITER_TARGETS row (it is absent from
    # _KB_WRITER_VERBS) — but the apply path DOES run KB migrations, which reach
    # the interlock through features.migratecmd exactly as the monolith did.
    "update",
    # task 42 — `release preflight` is READ-ONLY and hermetic (temp repo, scratch
    # key), so it is not a KB writer and needs no _KB_WRITER_TARGETS row. Key and
    # tag ACTIONS stay operator-gated in docs/release-signing.md rather than
    # becoming CLI surface.
    "release",
    # task 43 — the shared tick. NOT a KB writer (it schedules and dispatches;
    # the work it triggers is guarded where that work lives), so no
    # _KB_WRITER_TARGETS row. `--install-schedule` stays an explicit operator
    # action: nothing installs a schedule as a side effect of a CLI run.
    "tick",
    # task 44 — the periodic self-exam (opt-in, default OFF). It emits SUGGESTED
    # workorders, but it does so THROUGH `cmd_plan_new`, which is already a
    # guarded writer, and `checkup` itself is absent from _KB_WRITER_VERBS — so
    # no _KB_WRITER_TARGETS row is required here. The guard sits at the writer,
    # not at every caller of one.
    "checkup",
    # task 45 — the steering-capture family and its offline transcript miner.
    # `prefer` IS a KB writer (capture writes an entry and mutates index.json),
    # and it is already listed in _KB_WRITER_VERBS, so its handlers need
    # _KB_WRITER_TARGETS rows in this same commit or they dispatch past the
    # older-CLI schema refusal. cmd_prefer is registered because the predispatch
    # gate keys on the DISPATCHED func — a cmd_prefer that delegates in-process
    # to cmd_learn would otherwise be UNGUARDED.
    "prefer",
    # task 47 — the adversarial review gate: THREE verbs promoted together
    # (`review` / `remediate` / `review-report`). They are one subsystem sharing
    # one vocabulary, and promoting them apart would put the finding schema on
    # both sides of the router at once — a gate whose two halves disagree about
    # which severities BLOCK fails by PASSING, which is the one failure mode a
    # review gate may not have. Each registrar was proven to reproduce the
    # monolith's flag set exactly before this row was written.
    "review",
    "remediate",
    "review-report",
    # task 48 — the DAG scheduler, all thirteen subverbs. Promoted only after the
    # lib registrar was proven to reproduce the monolith's subverb set AND every
    # per-subverb flag exactly; a scheduler that spawns real agents against a real
    # repository is the last verb whose surface should be taken on trust.
    "graph",
    # task 52 — the DONOR-ONLY verb wave. These six exist ONLY in the modular
    # core: the preserved monolith has no handler for any of them, so under
    # LEGACY-DEFAULT routing every one of them was DEAD — `agentware fleet --help`
    # exited nonzero, because the router sent it to a tree that has never heard of
    # the verb. Adding a row here is not a promotion, it IS the capability. Each
    # was proven to reproduce its COMPLETE union node — every subverb and every
    # flag, at every level — before its row was written.
    "org",          # org lint O1..O13 (a true no-op on a flat install)
    "team",         # create / list / register
})

#: TOP-LEVEL options and how many VALUES each consumes, so the scan can step over an
#: option's value instead of reading it as the verb. Derived from the union surface's root
#: node (``--format``, a _StoreAction with nargs=None => 1 value) plus argparse's implicit
#: help action, which the surface capture omits because it is not a declared argument.
#: Gated against that fixture — do not edit one without the other.
GLOBAL_OPTIONS = {
    "--format": 1,
    "-h": 0,
    "--help": 0,
}

#: ``route`` verdicts. Named rather than bare strings so a typo at a call site is an
#: AttributeError instead of a silent "not equal to 'lib'" that quietly means legacy.
LIB = "lib"
LEGACY = "legacy"

#: argparse's end-of-options marker: everything after it is positional, whatever it looks
#: like. ``agentware -- recall`` must resolve ``recall``, not treat it as an option.
END_OF_OPTIONS = "--"


def verb_of(argv):
    """Return the first POSITIONAL token in ``argv`` — the verb — or ``None``.

    ``argv`` is the token list WITHOUT the program name (i.e. ``sys.argv[1:]``).
    Returns ``None`` when there is no verb at all: an empty argv, a bare ``-h``, or a
    trailing global option whose value never arrived. ``None`` routes to legacy, which is
    correct — argparse there emits the same usage/error text it always has.

    An UNKNOWN option is assumed to consume no values. It cannot be resolved (this module
    deliberately never builds a parser), and assuming zero is the safer of the two errors:
    it may promote the unknown option's value to "the verb", where argparse rejects it
    loudly, whereas assuming one could swallow a REAL verb and produce a bare-usage error
    that names nothing.
    """
    pending = 0
    for i, token in enumerate(argv):
        if pending:
            pending -= 1
            continue
        if token == END_OF_OPTIONS:
            return argv[i + 1] if i + 1 < len(argv) else None
        if token.startswith("-") and token != "-":
            if "=" not in token:
                pending = GLOBAL_OPTIONS.get(token, 0)
            continue
        return token
    return None


def route(argv):
    """Return ``LIB`` if ``argv``'s verb is allowlisted, else ``LEGACY``.

    Reads ``PORTED_VERBS`` through the module global at CALL time, so a port that appends
    to the allowlist takes effect without re-importing, and so a test can substitute an
    allowlist and prove the LIB branch is live rather than decorative.
    """
    return LIB if verb_of(argv) in PORTED_VERBS else LEGACY


def dispatch(argv, legacy_main):
    """Run one invocation through whichever tree owns its verb; return the exit code.

    ``legacy_main`` is the monolith's ``main`` as the entrypoint resolved it AFTER the
    passthrough exec — passed IN rather than imported so this module never reaches back
    into the entrypoint, and so the legacy arm stays exactly one call deep.

    ``argv`` is always forwarded EXPLICITLY (never left to default to ``sys.argv[1:]``
    inside the callee) so the tokens that were ROUTED are the same tokens that get PARSED.
    """
    if route(argv) == LIB:
        from agentware_lib.cli import main as lib_main
        return lib_main(argv)
    return legacy_main(argv)


__all__ = ["PORTED_VERBS", "GLOBAL_OPTIONS", "LIB", "LEGACY", "END_OF_OPTIONS",
           "verb_of", "route", "dispatch"]
