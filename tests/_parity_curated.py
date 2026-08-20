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
         LOCAL, 20, "tests.gates.test_readme_union",
         "regenerate or drop ROADMAP.md: the byte-identity assertion reddens"),
    _row("docs:readme-union-anchors", "docs_templates",
         "The README as a UNION: this tree's hero, badges, maturity rows, "
         "roadmap and licence sections plus the donor's architecture section and "
         "its not-the-source-of-truth note",
         BOTH, 20, "tests.gates.test_readme_union",
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
         BOTH, 68, "tests.gates.test_modularity_negative",
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
    _row("docs:community-health-corpus", "docs_templates",
         "The community-health corpus a public repository is judged by — "
         "contributing guide (with the modular contract written for a HUMAN "
         "contributor), security policy, code of conduct, changelog, and the "
         "issue and pull-request templates — each gated for existence, "
         "substance, its required sections and a README route to it, because a "
         "community file nothing points at is one nobody finds",
         LOCAL, 77, "tests.gates.test_repo_standard",
         "delete a required file, replace one with a stub, drop a required "
         "section, unroute one from the README, gut the PR checklist, break a "
         "template's frontmatter, or disable blank issues with no template "
         "left to file: each arm (A/D) must name its own offender on a copy of "
         "the shipped files"),
    _row("docs:licensing-declared-once", "docs_templates",
         "One licence, declared identically on every surface that declares one "
         "(the LICENSE text including its patent grant, the README badge and "
         "footer, the NOTICE, the contribution terms, the dashboard's "
         "package.json) with third-party adopted text attributed in BOTH "
         "directions — the NOTICE names the work, its licence and canonical "
         "URL, and the adopting file must still carry its own attribution "
         "witness",
         LOCAL, 77, "tests.gates.test_repo_standard",
         "strip the patent grant from LICENSE, drop a licence declaration, set "
         "a different licence in package.json, name an undeclared foreign "
         "licence, delete the NOTICE's attribution block, or stop adopting the "
         "text the NOTICE still attributes: each arm (B/C) names its own "
         "offender — and the twin mutation proves an ADOPTED licence stays "
         "LEGAL, since a ban that forbids attribution defeats the NOTICE"),
    _row("docs:no-operator-identity-in-the-tree", "docs_templates",
         "The shipped tree carries ZERO live operator identity (R-LOC-03), "
         "pinned by a gate rather than only by the advisory audit row: the "
         "preserved ROADMAP's three by-name vision quotes and eight sample "
         "handles in the test corpus were redacted, and the roadmap's "
         "byte-identity claim was NARROWED to enumerate that one sanctioned "
         "deviation instead of being waived",
         LOCAL, 77, "tests.gates.test_repo_standard",
         "reintroduce the live operator handle anywhere in a tracked file: arm "
         "F reports it through the same scanner `agentware audit` runs; "
         "re-personalise a redacted ROADMAP attribution, or change any other "
         "line of the preserved file, and the narrowed preservation test in "
         "tests.gates.test_readme_union names the line"),
    _row("mod:ci-runs-one-entrypoint", "modularity",
         "Continuous integration runs the FULL suite (four tier-scoped runs, "
         "never a whole-tree discover), the portability, parity, body-differ, "
         "constant-differ and modularity-ratchet gates, the modularity negative "
         "test and steering lint — all through ONE declared stage table in "
         "tests/_ci_gate.py that the workflow calls and a contributor can run "
         "locally, so the YAML holds no gate command of its own and CI cannot "
         "drift from CONTRIBUTING.md",
         LOCAL, 78, "tests.gates.test_ci_workflow",
         "inline a gate command in the workflow (a `-m unittest` step, or a "
         "checker inside a block scalar): arm B names the offending run line — "
         "and the twin proves `git config` setup and the sanctioned entrypoint "
         "stay LEGAL, since an arm that banned those would ban working CI. Drop "
         "a stage and arm C fails naming task 78's own words for it"),
    _row("mod:no-vacuous-gate-command", "modularity",
         "Every checker a CI stage names is EFFECTIVE, not merely present: it "
         "carries a real __main__ dispatch and can exit non-zero. Three of the "
         "five commands CONTRIBUTING.md documented as 'the structural checkers' "
         "could not fail — _ceiling_gate.py and _modularity_rules.py had no "
         "__main__ at all (running them imported a module and exited 0) and "
         "_manifest_gen.py --check ignored the flag and printed the manifest",
         LOCAL, 78, "tests.gates.test_ci_gate_stages",
         "delete a checker's __main__ dispatch, or shrink a declared debt "
         "fixture by one row: arm D fails — the first statically, the second "
         "behaviourally by running the command and demanding rc 1 naming the "
         "offending location, because 'the gate ran' and 'the gate could have "
         "failed' are different claims"),
    _row("mod:green-is-the-recorded-baseline", "modularity",
         "CI implements THIS repository's definition of green — 'no worse than "
         "the recorded baseline', never 'all pass' — by running each tier "
         "through a shrink-only known-failure ledger rather than reading "
         "unittest's exit code, which would make CI red on day one for the one "
         "declared pre-existing failure plan task 89 already owns. The tier "
         "result comes from TestResult/test.id() over a private JSON channel, "
         "never from scraping stdout, because a tier that runs sub-suites "
         "(test_modularity_negative) emits their FAIL headers into its own "
         "output and a scraper misattributes a child's expected red to the "
         "parent",
         LOCAL, 78, "tests.gates.test_ci_gate_stages",
         "add an undeclared failure and the tier reddens UNDECLARED; fix a "
         "declared one without deleting its row and it reddens STALE naming the "
         "row — proven live in ONE real unit-tier run against a deliberately "
         "wrong ledger. Give a row an owner that is not a plan task, or a "
         "reason under 80 chars, and the schema refuses it; kill the tier child "
         "before it reports and the result is a FAILURE, never a clean empty "
         "one"),
    _row("mod:fresh-clone-one-command", "modularity",
         "A fresh clone reaches a verified working state with ONE documented "
         "command and no install step — proven from a directory asserted EMPTY, "
         "by cloning and running that command inside the clone, under a patched "
         "HOME so the operator's real config.env is untouched",
         LOCAL, 78, "tests.e2e.test_ci_bootstrap",
         "break any step of the health sequence (delete a lib package, stop "
         "init scaffolding a subdir, seed an index that fails its validator, "
         "make recall emit non-JSON, break a steering rule): each is caught by "
         "its own step. Point the clone at a non-empty directory and it is "
         "REFUSED, since 'from an empty directory' is the claim under test"),
    _row("mod:commit-series-single-identity", "modularity",
         "The branch's HISTORY is publishable, not just its working tree: one "
         "operator identity across the author field, the COMMITTER field (a "
         "different field, set from a different config key, and the one a "
         "rebase rewrites) and every email-shaped token in a subject, body or "
         "trailer — plus tool-attribution lines like `Generated with`, which "
         "carry no address at all and so are invisible to an email sweep. The "
         "portability gate scans the commit range for VENDOR TOKENS; nothing "
         "scanned the shape of the series, and three commits had in fact been "
         "authored under a second personal address",
         LOCAL, 82, "tests.gates.test_commit_series",
         "narrow the identity arm to the author field alone (drop \"committer\" "
         "from the role loop): test_a_foreign_committer_address_is_named goes "
         "red against a synthetic repo whose author is correct and whose "
         "committer is a CI bot — the exact residue a rebase leaves"),
    _row("mod:commit-series-identity-is-anchored-not-declared", "modularity",
         "The expected identity is ANCHORED, never written down: it is read at "
         "runtime from the author of the commit the branch was cut from, and a "
         "`name`/`email`/`emails` key in the fixture is a SCHEMA ERROR. That "
         "removes two failures at once — operator data committed into a public "
         "repository (R-LOC-03; the first version of this fixture wrote the "
         "address in and _repo_standard's personal-data arm caught it) and the "
         "one field a maintainer could widen to two entries to buy silence. A "
         "stray identity can only be cleared by removing it from history. The "
         "window's `first` is pinned by value and asserted equal to the range's "
         "real earliest date for the same reason, and the count carries a floor "
         "with no ceiling so a squash or reset that eats history reddens",
         LOCAL, 82, "tests.gates.test_commit_series",
         "empty the refused-key tuple in _identity_errors so a literal address "
         "may be declared again: test_a_declared_address_is_a_schema_error and "
         "test_a_declared_name_is_a_schema_error both go red, because writing "
         "both addresses down would once more be a legal way to buy silence"),
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
    _row("mod:mutation-proof-is-executed", "modularity",
         "Every curated row's `mutation` is a RUNNABLE probe over a closed "
         "grammar that the sweep EXECUTES against that row's named test, not a "
         "sentence checked for being longer than twenty characters — the sweep "
         "reports STALE-PROBE for a no-op, NOT-REDDENED for an unguarded "
         "capability and LOAD-ERROR for a mutation that only breaks an import, "
         "and refuses a probe aimed at the named test itself",
         LOCAL, 79, "tests.gates.test_parity_sweep",
         "take one row's probe out of the table: the coverage arm names that "
         "row rather than passing over a shorter table"),
    _row("mod:neutrality-redaction-at-the-write-boundary", "modularity",
         "The WRITE side of the portability gate: every node id leaves "
         "`_preserved_scan.collect_node_ids` neutralised and every captured "
         "output tail leaves `_baseline_capture.tail` neutralised, through the "
         "SAME needle set the five-surface scan grades the tree by — so a "
         "generator can never commit a vendor token the gate would then report "
         "with no wave able to clear it, which is exactly how three frozen "
         "captures carried one for two waves",
         LOCAL, 83, "tests.gates.test_neutrality_redact",
         "make the redactor keep its own needle list instead of calling the "
         "gate's loader: the shared-source arm adds a needle nobody hardcoded "
         "and demands that BOTH the scan and the redactor move on it, so a "
         "private copy goes red even though it still imports the same module"),
    _row("mod:object-store-authorship-census", "modularity",
         "The SIXTH surface: every commit the object store holds beyond the "
         "base ref, classified by the weakest `git push` that could publish it "
         "(branch / pushable / mirror_only / unreachable) and checked against "
         "the identity ANCHORED at the base ref's author. Reachability is not "
         "publishability — `git log base..HEAD` stops at the branch while a "
         "store keeps every abandoned rewrite and fetched tip, and `push --all` "
         "publishes by REF",
         LOCAL, 83, "tests.gates.test_portability_store",
         "reimplement the census on `rev-list` instead of "
         "`cat-file --batch-all-objects`: "
         "test_the_store_sees_a_commit_the_range_walk_cannot goes red, because "
         "it asserts the DIFFERENCE between the two enumerations rather than "
         "the classifier's output"),
    _row("mod:mutation-sweep-runs-in-ci", "modularity",
         "The sweep is a DECLARED CI stage, so the proof is executed on every "
         "run rather than the once its author ran it: `--check` in both "
         "profiles for milliseconds, the full 78-row `--all` in the full "
         "profile",
         LOCAL, 79, "tests.gates.test_ci_workflow",
         "delete the sweep stage from the declared table: the coverage arm "
         "names it by id rather than passing over a shorter table"),
]
