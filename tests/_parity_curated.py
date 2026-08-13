"""Task-10 PARITY MANIFEST: the curated tier.

The generated tier (surface, env/config, test corpus, assets) is re-derived from
binding fixtures and cannot drift. It is also blind to every capability with no
CLI surface, no env key and no distinguishing node id — which is precisely where
the plan's four silent-loss classes live.

This table is that blind spot, written down. Every row names the tree that owns
the capability, the plan task that lands it, the TEST that proves it, and the
MUTATION that must redden that test. A row whose mutation nobody can run is
prose, not a parity claim, so `mutation` is a schema-required field.

Seeded from the plan's own two at-risk inventories ("What each tree must
contribute", LOCAL-only and DONOR-only) plus the four silent-loss classes and the
seven impossible unions. Task 79 completes the table; this is the standing-up.

A row whose named test does not resolve on disk yet is legal — the merged tree
does not exist. It ships in the ratchet's shrink-only `pending_verification`
list, per `learn-ship-a-not-yet-satisfiable-gate-as-a-shrink-only-ratchet`, and
the gate reddens in BOTH directions: an unlisted row with no test is a defect,
and a listed row whose test starts resolving must be removed from the list.

`CURATED_ROWS` is still the whole table and still the ONE name every consumer
imports (`R-MOD-08` names this file). Its MERGE half — the rows asking whether a
product capability survived the two-tree merge, and the row constructors — moved
to `tests/_parity_merge_rows.py` at task 75 when the table hit its 600-line
ceiling; what remains here is the half that keeps GROWING, the repository's own
discipline (documents, evaluation harness, modularity gates, scaffold), which is
also the half `agentware scaffold new` appends to. Row ORDER is unchanged: the
merge rows still come first.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from _parity_merge_rows import (  # noqa: E402
    BOTH, DONOR, LOCAL, MERGE_ROWS, _exrow, _row)

# Re-exported so a reader (and the scaffold's appended row) finds the side
# constants and the builders in the file R-MOD-08 sends them to.
__all__ = ["BOTH", "DONOR", "LOCAL", "CURATED_ROWS", "_row", "_exrow"]


CURATED_ROWS = MERGE_ROWS + [
    # ----------------------------------------------------------- docs/templates
    _row("docs:roadmap-preserved", "docs_templates",
         "ROADMAP.md preserved byte-identical to aw-pre-merge (the donor deleted "
         "it outright)",
         LOCAL, 20, "tests.test_docs_union",
         "regenerate or drop ROADMAP.md: the byte-identity assertion reddens"),
    _row("docs:readme-union-anchors", "docs_templates",
         "The README as a UNION: this tree's hero, badges, maturity rows, "
         "roadmap and licence sections plus the donor's architecture section and "
         "its not-the-source-of-truth note",
         BOTH, 20, "tests.test_docs_union",
         "drop any one of the six local or two donor anchors: the anchor "
         "assertion names the missing one"),
    _row("docs:template-frontmatter-corpus", "docs_templates",
         "Four templates including a decision template, each opening with a "
         "frontmatter block declaring the full required field set",
         DONOR, 51, "tests.unit.knowledge.test_templates_lint",
         "remove a required field from any template: the linter must redden "
         "naming the field"),
    _row("docs:artifact-capture-standard", "docs_templates",
         "The donor's artifact capture standard: a learning referencing a "
         "concrete artifact captures it verbatim",
         DONOR, 61, "tests.unit.knowledge.test_artifact_capture_standard",
         "store the reference without the artifact: the captured body no longer "
         "reproduces the cited artifact"),

    # -------------------------------------------------------------- evaluation
    _row("eval:ab-gate-layout-aware-extraction", "evaluation_quality",
         "The A/B retrieval no-regression gate extracts the WHOLE package for "
         "the HEAD arm; under the modular layout a flat blob is a 110-line "
         "facade with no lib behind it",
         LOCAL, 26, "tests.unit.retrieval.test_eval_pinning",
         "extract only the entrypoint blob: the HEAD arm reports unavailable "
         "instead of a score, and the gate silently stops gating"),
    _row("eval:gold-fixture-superset", "evaluation_quality",
         "The gold-fixture corpus as the union, carrying the only document in a "
         "skills category",
         LOCAL, 59, "tests.unit.retrieval.test_eval",
         "drop the skills-category document: the skills query stops ranking it "
         "first and the loader returns no category for it"),
    _row("eval:help-golden-corpus", "evaluation_quality",
         "The help_golden fixture family as a strict superset of the donor's 80 "
         "members — 125 surfaces regenerated exactly once on the operator's own "
         "interpreter, after the verb surface was final, with COLUMNS pinned and "
         "the capturing interpreter recorded alongside them",
         BOTH, 60, "tests.gates.test_parser_golden",
         "delete one committed golden: the surface-set check names it as an "
         "unpinned live surface and the frozen-donor-member check reddens"),
    _row("eval:security-invariant-coverage", "evaluation_quality",
         "The security-invariant corpus as a per-path-class policy table plus a "
         "coverage assertion that every Python file on disk is claimed",
         LOCAL, 27, "tests.gates.test_invariants",
         "add an unclaimed Python file under scripts: the coverage assertion "
         "must redden rather than silently skip it"),

    # -------------------------------------------------------------- modularity
    _row("mod:lib-decomposition", "modularity",
         "The agentware_lib decomposition itself: a facade plus ~70 modules "
         "under the layer contract, replacing the 34,792-line monolith",
         DONOR, 25, "tests.gates.test_module_manifest",
         "add a file under the lib with no manifest entry: the completeness "
         "check must redden"),
    _row("mod:module-manifest-catalog", "modularity",
         "The generated module manifest (catalog/modules.json) as the "
         "completeness gate over every file on disk",
         DONOR, 67, "tests.gates.test_module_manifest",
         "regenerate the manifest twice: the second run must produce no diff"),
    _row("mod:per-function-caps", "modularity",
         "The per-function ceilings (120 lines, 12 branches) — a file cap alone "
         "is insufficient, configcmd.py is 799 lines with 2 defs",
         BOTH, 68, "tests.test_modularity_negative",
         "add a function over the per-function cap inside a small file: the "
         "negative test must redden on that shape alone"),
    _row("mod:dashboard-package-route-union", "modularity",
         "The dashboard as a package with its path-confinement kernel in its own "
         "file, serving the UNION of both trees' routes",
         BOTH, 69, "tests.integration.test_dashboard_api",
         "drop any route from the served union: the route-set assertion names it"),
    _row("mod:legacy-shrink-only-ratchet", "modularity",
         "The shrink-only legacy ratchet, so agentware_legacy.py can only get "
         "smaller and is deletable when empty",
         LOCAL, 32, "tests.gates.test_legacy_ratchet",
         "raise the high-water mark: legacy is allowed to grow again"),
    _row("mod:legacy-shrink-port-coupling", "modularity",
         "The ratchet's PER-HANDLER coupling: a cmd_* name may leave "
         "agentware_legacy.py only if a newly-appended PORTED_VERBS member — "
         "itself a real union verb — accounts for it by name, so a deletion is "
         "never mistaken for a port",
         LOCAL, 32, "tests.gates.test_legacy_ratchet",
         "delete one handler that no newly-ported verb accounts for while "
         "porting another, or credit a shrink to a token outside the union "
         "fixture: --record must refuse and name the orphan"),
    _row("mod:symmetric-promotion-gate", "modularity",
         "The SYMMETRIC per-verb promotion gate: a verb joins the strangler "
         "allowlist only once its modular surface covers the UNION fixture on "
         "kind, required, nargs, default, choices, type and subparsers dest — "
         "not on flag names",
         BOTH, 32, "tests.gates.test_promotion_gate",
         "narrow a choices set, move a default off its decided value, drop a "
         "required flag or rename a subparsers dest while leaving every option "
         "string spelled the same: each must redden on its own, naming the field"),
    _row("mod:donor-only-verb-presence", "modularity",
         "The PRESENCE gate over the six donor-only verbs (fleet, fleet-emit, "
         "org, team, templates, work), which legacy-default routing can never "
         "resolve, plus the stub and routing checks no surface comparison can "
         "express",
         DONOR, 32, "tests.gates.test_presence_gate",
         "delete any one of the six from the modular parser, reduce one to a "
         "stub with no subcommands and no arguments of its own, or allowlist "
         "one under a token the router's argv scan cannot resolve: the gate "
         "names that verb"),

    # ------------------------------------------------------ scaffold (task 73)
    _row("surface:scaffold", "cli_surface",
         "The `scaffold` verb: `new` emits a compliant unit of any of the four "
         "kinds (module, verb, panel, loop lib) WITH its verb declaration, "
         "manifest entry, mirrored test, doc row and parity row, and `check` is "
         "the read-only CI audit. Post-merge capability, so it is in NEITHER "
         "pre-merge tree's union fixture and only a curated row can cover it",
         LOCAL, 73, "tests.unit.ops.test_scaffoldcmd",
         "drop the `check` subcommand, collapse the two exit codes (2 for an "
         "invalid unit description, 1 for a standing finding) into one, or let "
         "`new` write without --kind: each reddens its own surface assertion"),
    _row("surface:scaffold.new", "cli_surface",
         "`scaffold new --kind {module,verb,panel,loop-lib} --name --layer "
         "--verb --task --dry-run --force --no-regen`: emits the unit and its "
         "wiring, writing ONLY inside the package checkout and never the "
         "knowledge dir",
         LOCAL, 73, "tests.unit.ops.test_scaffoldcmd",
         "drop --kind or --task from the `new` parser, or widen --kind's choices "
         "past the four kinds: the required-argument and choices assertions "
         "redden by name"),
    _row("surface:scaffold.check", "cli_surface",
         "`scaffold check [--format]`: the read-only wiring audit, exiting 1 on "
         "any finding so it is usable as a CI step",
         LOCAL, 73, "tests.unit.ops.test_scaffoldcmd",
         "return 0 from cmd_scaffold_check while findings stand, or drop the "
         "subcommand from the parser: the exit-code and subcommand assertions "
         "redden"),
    _row("mod:scaffold-wiring-audit", "modularity",
         "The five-question wiring audit R-MOD-11 obliges — manifest entry, "
         "declared-verb/manifest agreement, mirrored owning test, a parity row "
         "per top-level verb and a README router row — with the last two "
         "honouring shrink-only debt ledgers in which an undeclared gap AND a "
         "stale declared row both fail",
         LOCAL, 73, "tests.unit.ops.test_scaffoldcheck",
         "delete any one check from `audit`, accept an undeclared testless or "
         "undocumented module, or keep a doc-row debt row after its module gains "
         "a README row: each planted gap must be named by its own finding kind"),
    _row("docs:architecture-extensibility", "docs_templates",
         "The contributor ARCHITECTURE doc and its runnable R-DATA-01 gate: the "
         "module map, the layer contract and its rationale, the `__verbs__` "
         "registration mechanism, the ceiling table, the gate table, a recipe "
         "per non-subcommand extension point and the shared-file chokepoints "
         "with their mitigations — with the map, the layer table and the "
         "ceiling table RENDERED from catalog/modules.json between managed "
         "markers, so the documentation cannot drift from the manifest by a "
         "hand edit on either side",
         DONOR, 74, "tests.gates.test_doc_architecture",
         "delete a recipe anchor, drop a chokepoint row, remove a module row "
         "from either documented map, add a row naming no module, hand-edit a "
         "managed block by one character, or RENAME the symbol a recipe points "
         "at: each arm (A/B/C/D/E/F) must name the offender in a scratch copy "
         "of the real tree"),
    _row("docs:module-map-two-way-parity", "docs_templates",
         "Both documented module maps — the `scripts/agentware_lib/README.md` "
         "router and the architecture doc's map — are complete and exact "
         "against the manifest in BOTH directions, the state that let task 73's "
         "82-row doc-row debt ledger be deleted rather than carried",
         LOCAL, 74, "tests.unit.ops.test_scaffoldcheck",
         "re-create tests/fixtures/doc-row-debt.json, or delete a router row: "
         "`check_docs` reports doc_row_missing and the ledger-is-gone assertion "
         "reddens"),
    _row("docs:add-a-feature-walkthrough", "docs_templates",
         "The worked add-a-feature walkthrough and its structural gate: a verb "
         "taken from nothing to green in the real commands in order, a shorter "
         "path for extending an existing verb, and a troubleshooting entry "
         "naming every gate that can reject a change with what its message "
         "means and how to clear it — gated so a named gate that does not "
         "exist, a required gate with no row, a row with an empty fix column "
         "and a `python3 tests/_x.py` command naming a missing checker are each "
         "a failure",
         LOCAL, 75, "tests.gates.test_doc_walkthrough",
         "rename a named gate to a path that does not exist, delete a required "
         "gate's row, blank a row's fix column, add a runnable block naming a "
         "dead verb or a ghost checker, or change a `norun:` reason to one "
         "outside the declared allowlist: each arm (A/B/C/D/E) must name its "
         "own offender on a scratch copy of the real tree"),
    _row("docs:walkthrough-is-executed", "docs_templates",
         "The walkthrough is EXECUTABLE, not merely proofread: every command "
         "block is run in a scratch checkout in document order and its exit "
         "code read, every quoted `-`/`+` hunk must match the live tree exactly "
         "once, and the only blocks exempt from execution are the two carrying "
         "a declared reason (the suite cannot run itself; the R-PKG chain "
         "records baselines and reads the operator's knowledge dir)",
         LOCAL, 75, "tests.e2e.test_add_a_feature_walkthrough",
         "break any command in the document, or quote a line the tree no longer "
         "holds: the run reddens naming the block's line number — proven live "
         "by planting a bad flag on the third of five generator lines (caught "
         "only because the runner prepends `set -e`) and by a hunk quoting a "
         "stale pin value"),
    _row("skills:modular-development-procedure", "docs_templates",
         "The on-demand modular-development skill: the R-MOD-01..12 PROCEDURE "
         "(triage, layer from the import direction, the five artifacts a unit "
         "lands with, the split-at-the-obligation rule, the gate-to-fix table) "
         "authored as a catalog skill so an agent loads it when code is about "
         "to move and pays nothing for it otherwise — the rules stay always-on "
         "in AGENTS.md, the procedure does not",
         LOCAL, 76, "tests.gates.test_guidance_sync",
         "delete the skill folder, drop its catalog row, or point either at the "
         "other: `skill validate` and the installed-and-catalogued assertion "
         "redden; make the skill always-on steering and the not-always-on "
         "assertion reddens"),
    _row("docs:guidance-surface-contract", "docs_templates",
         "The guidance-surface contract check over every persona, skill, doc "
         "and always-on steering file: a retired path may be named only inside "
         "a retirement sentence (and must still be absent from disk), every "
         "repo-relative path must be present or CLAIMED by catalog/modules.json, "
         "every agentware_lib name must resolve to a published module, every "
         "runnable checker command must exist, and every exemption must carry a "
         "witness the surface still holds",
         LOCAL, 76, "tests.gates.test_guidance_sync",
         "name the retired monolith as a live fact, point a doc at an unclaimed "
         "path (including inside a backticked command), rename a module the "
         "manifest publishes, print a ghost `python3 tests/_x.py`, strip an "
         "exemption's witness, or stop naming a required claim: each arm "
         "(A/B/C/D/E/F) must name its own offender on a scratch copy of the "
         "real tree, and the retirement-sentence twin must stay GREEN"),
]
