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
"""

BOTH = ["donor", "local"]
DONOR = ["donor"]
LOCAL = ["local"]


def _row(rid, dimension, capability, sides, task, test, mutation, **extra):
    row = {
        "id": rid,
        "dimension": dimension,
        "capability": capability,
        "sides": sides,
        "status": extra.pop("status", "required"),
        "verification": "curated",
        "task": task,
        "evidence": {"test": test, "mutation": mutation},
    }
    row.update(extra)
    return row


def _exrow(rid, dimension, capability, sides, reason, approver, follow_up,
           probe):
    """An EXCLUDED row: deliberately not carried across, and provably absent.

    No `task`/`evidence` — an exclusion is refuted by its probe finding the thing
    present, not by a test. The schema obliges reason, approver, follow_up and a
    runnable absence_probe, so an exclusion can never be bare prose.
    """
    return {
        "id": rid,
        "dimension": dimension,
        "capability": capability,
        "sides": sides,
        "status": "excluded",
        "verification": "absence_probe",
        "reason": reason,
        "approver": approver,
        "follow_up": follow_up,
        "absence_probe": probe,
    }


CURATED_ROWS = [

    # ---------------------------------------------------------------- retrieval
    _row("retrieval:stage-1-skill-pin-floor", "retrieval_policy",
         "The stage-1 skill pin at its 0.85 floor with the three-argument "
         "signature and the learnings-eviction guarantee, applied at all three "
         "call sites including retrieve_bm25 and the bm25+embed pre-trim branch",
         LOCAL, 49, "tests.unit.ops.test_skill_pin_floor",
         "set SKILL_PIN_FLOOR to the donor's None, or drop the pin from the "
         "pre-trim branch: a skill scoring above the floor stops ranking first"),
    _row("retrieval:read-time-trust-and-freshness", "retrieval_policy",
         "The read-time trust surface on every recall and query record: the "
         "live-anchored abs_path plus age_days, last_verified, stale, "
         "superseded_by and trust (zero donor equivalent, 7 symbols)",
         LOCAL, 49, "tests.unit.features.test_trust_surface",
         "emit the RELATIVE path instead of re-anchoring to the live knowledge "
         "dir, or drop any one of the six trust fields from the record"),
    _row("retrieval:staleness-and-supersede-downweight", "retrieval_policy",
         "The ranking downweight applied to stale and superseded entries, "
         "sharing ONE stale-window definition with the audit and trust signals",
         LOCAL, 49, "tests.unit.knowledge.test_staleness_downweight",
         "return a flat 1.0 multiplier: a superseded entry stops ranking below "
         "its successor"),
    _row("retrieval:token-budget-ladder", "retrieval_policy",
         "The committed R-RET-03/R-RET-04 ladder: default token budget 1500 with "
         "a stage-2 escalation to 3000, not the donor's collapsing 10000",
         BOTH, 49, "tests.unit.retrieval.test_recall",
         "raise the default token budget to the donor's 10000: stage 1 and "
         "stage 2 become indistinguishable"),
    _row("retrieval:work-scope-plan-status-multipliers", "retrieval_policy",
         "Plan-status ranking multipliers sourced per feature and inherited by "
         "sibling worklogs (done, running and superseded score differently)",
         LOCAL, 55, "tests.unit.retrieval.test_scopework_recall",
         "flatten every plan-status multiplier to 1.0: a sibling worklog under a "
         "done plan stops scoring its recorded fraction"),
    _row("retrieval:work-corpus-stat-short-circuit", "retrieval_policy",
         "The work-corpus stat short-circuit, so a warm work-scope recall reads "
         "zero work bodies",
         LOCAL, 55, "tests.unit.retrieval.test_workcache",
         "always read work bodies: the warm path stops being O(1) in body reads"),
    _row("retrieval:bm25-exact-score-pin", "retrieval_policy",
         "The donor's exact-score BM25 pin — order-based assertions alone let a "
         "mutated K1 or B survive every test in this tree",
         DONOR, 59, "tests.test_bm25_exact_scores",
         "perturb K1 or B by a value that preserves ranking ORDER: the exact "
         "score assertions must still redden"),

    # ------------------------------------------------------------- kb integrity
    _row("kb:atomic-durable-write", "kb_integrity",
         "The atomic durable index write (temp file, fsync, rename) rather than "
         "the donor's truncating open() + json.dump",
         LOCAL, 22, "tests.unit.knowledge.test_atomic_save",
         "replace the temp-file+rename+fsync with a truncating open(): a "
         "concurrent reader can observe a torn index"),
    _row("kb:reentrant-index-lock", "kb_integrity",
         "The reentrant fcntl.flock index lock with its process-wide guard "
         "around every mutation (the donor has zero fcntl/flock/fsync)",
         LOCAL, 22, "tests.unit.knowledge.test_indexlock",
         "remove the flock acquisition: the 5,000-poll concurrent probe records "
         "torn reads"),
    _row("kb:quarantine-and-continue-rebuild", "kb_integrity",
         "Fault-isolating rebuild: duplicate-id dedup plus quarantine-and-"
         "continue, so one malformed entry cannot fail the whole rebuild",
         LOCAL, 22, "tests.unit.knowledge.test_quarantine",
         "raise on the first malformed entry: a single bad file takes down the "
         "rebuild and the index with it"),
    _row("kb:auto-rebuild-once-recovery", "kb_integrity",
         "load_index auto-rebuild-once on a missing or corrupt index, which is "
         "what makes a fresh clone recover instead of hard-failing",
         LOCAL, 22, "tests.unit.features.test_rebuild",
         "remove the auto-rebuild-once recovery: a corrupt index stops self-"
         "recovering on load"),
    _row("kb:poisoned-index-list-normalizer", "kb_integrity",
         "The lenient list-field normalizer at every raw index read site (graph "
         "adjacency, relation lint, corpus text, frontmatter migration)",
         LOCAL, 23, "tests.unit.knowledge.test_index_reader_safety",
         "read a list field raw at any one site: a mapping where a list is "
         "expected raises an uncaught type or hashability error"),
    _row("kb:corpus-fingerprint-freshness-authority", "kb_integrity",
         "The donor's content corpus_fingerprint as the cache-freshness "
         "authority (the monotonic counter goes stale-blind after a pull), with "
         "the coupled drop-on-write guard",
         DONOR, 24, "tests.unit.retrieval.test_bm25_cache",
         "restore the monotonic generation counter as the authority: an entry "
         "arriving via a pull stops invalidating the cache"),
    _row("kb:foreign-schema-cache-rejection", "kb_integrity",
         "A BM25 postings-cache version neither tree has used (this tree wrote "
         "v2, the donor v1, to the SAME .cache/bm25-postings.json), so a "
         "foreign-schema cache can never load as valid. Scoped to the BM25 "
         "cache deliberately: the VECTOR cache is v1 on both sides with a "
         "byte-identical payload, so it is not a collision and must not be "
         "bumped",
         BOTH, 24, "tests.unit.retrieval.test_cache_freshness",
         "accept a cache written under either legacy version number: a foreign "
         "schema loads as valid at the same cache path"),
    _row("kb:derived-artifacts-decommitted", "kb_integrity",
         "The nine derived knowledge-base artifacts stay gitignored (the donor "
         "tracks index.json, FEATURES.md and every roster)",
         LOCAL, 57, "tests.unit.ops.test_decommit",
         "track any derived artifact again: a simulated two-clone sync conflicts "
         "on it"),
    _row("kb:roster-and-injection-lint", "kb_integrity",
         "The donor's roster and injection lint wired into index validate",
         DONOR, 53, "tests.unit.features.test_skill_roster",
         "blank a roster row: index validate must fail rather than pass"),
    _row("kb:pointer-target-guard", "kb_integrity",
         "The knowledge-dir TARGET guard — the only 'is this directory safe to "
         "point the KB at?' check in either tree — relocated into the core "
         "layout module and wired into BOTH repoint paths (`init` and `config "
         "--set-knowledge-dir`), keeping its deliberate asymmetry over the "
         "derived index: absent is fine, present-but-corrupt is a refusal",
         DONOR, 15, "tests.gates.test_kbtarget_guard",
         "drop the guard from either repoint path, or make it require the "
         "derived index: a fresh checkout stops being pointable, or a directory "
         "that is not a knowledge base is silently accepted"),
    _row("kb:init-clobber-guard-union", "kb_integrity",
         "The `init` clobber guard as the UNION: the wider trigger (the config "
         "already points elsewhere, sentinel or not) with this tree's exit code "
         "3 and its structured `kb-already-configured` JSON payload",
         BOTH, 15, "tests.gates.test_kbtarget_guard",
         "narrow the trigger back to sentinel-only, or return the generic exit "
         "1 without the JSON body: a half-onboarded pointer is clobbered "
         "silently, or a caller can no longer tell a clobber refusal from a "
         "filesystem failure"),
    _exrow("kb:vendor-kb-repoint-migration", "kb_integrity",
           "The one-shot KB auto-repoint / autoprovision migration: two "
           "hard-coded checkout basenames, a sibling-path derivation, an opt-in "
           "env key and a best-effort shell-out to a vendor workspace tool, run "
           "at the start of a dream cycle",
           DONOR,
           "environment-specific by construction — it names two of the "
           "operator's employer's checkouts and shells out to a vendor build "
           "tool that no clone of this package can have. Its ONLY generic part, "
           "the target guard, was relocated FIRST and is carried by "
           "kb:pointer-target-guard.",
           "plan task 15 (operator-approved self-extension plan)",
           "none for the migration itself. A generic 'move my knowledge base' "
           "verb, if ever wanted, belongs in the config surface with the "
           "relocated guard in front of it — not in the nightly dream cycle.",
           {"kind": "path-absent",
            "path": "scripts/agentware_lib/features/dreamrepoint.py"}),

    # -------------------------------------------------------- loop orchestration
    _row("loop:adversarial-review-gate", "loop_orchestration",
         "The adversarial review gate and its reviewer persona, run over the "
         "shipped diff at the end of a self-extension run",
         LOCAL, 47, "tests.integration.test_loop_selfheal_gate",
         "make the review gate return PROCEED unconditionally: a diff violating "
         "an acceptance criterion stops being caught"),
    _row("loop:worklog-marker-regexes-fail-closed", "loop_orchestration",
         "The R-SI-03 marker regexes fail CLOSED: an unpromoted `> LEARNED:` "
         "marker blocks the completion promise (they fail open donor-side)",
         LOCAL, 45, "tests.integration.test_loop_worklog_gate",
         "relax a marker regex to the donor form: an unpromoted marker stops "
         "blocking the promise"),
    _row("loop:outcome-value-union", "loop_orchestration",
         "The LOOP_OUTCOME value set is the UNION, so the two values this tree "
         "emits are not rejected by the donor's org lint check O12",
         LOCAL, 34, "tests.integration.test_loop_emission",
         "drop either locally-emitted outcome value from the accepted set: the "
         "pre-hook hard-blocks the next run in team mode"),
    _row("loop:org-lint-hook-gates", "loop_orchestration",
         "The donor's pre- and post-hook org lint gates, each a no-op when the "
         "knowledge base is flat",
         DONOR, 33, "tests.integration.test_loop_org_lint",
         "remove either call site: an org-invalid artifact passes the phase gate"),
    _row("loop:retrieval-quality-gate", "loop_orchestration",
         "The donor's per-run retrieval-quality gate in post-hooks with its kill "
         "switch, a no-op when the gold set is absent",
         DONOR, 33, "tests.integration.test_loop_retrieval_gate",
         "remove the post-hook call site: a run that degraded retrieval quality "
         "completes clean"),
    _row("loop:session-start-roster-injection", "loop_orchestration",
         "Working SessionStart roster injection via the shared injectable "
         "predicate — this fixes a LIVE defect in this tree",
         DONOR, 53, "tests.integration.test_skill_hook",
         "make the injectable predicate return False for an always-on skill: the "
         "roster stops being injected at session start"),

    # ----------------------------------------------------------------- org/team
    _row("org:team-inference-from-knowledge-dir", "org_team",
         "Team inference from the knowledge-dir path plus shared-root detection",
         DONOR, 50, "tests.e2e.test_team_mode_e2e",
         "return a constant team: a knowledge dir under another team's root "
         "resolves to the wrong shard"),
    _row("org:corpus-scoping-and-shared-precedence", "org_team",
         "Corpus scoping BEFORE index build and shared-corpus precedence, so "
         "recall scores are computed over the scoped corpus",
         DONOR, 50, "tests.unit.retrieval.test_org_scoping",
         "scope after the build: two-team recall isolation breaks and scores are "
         "computed over the unscoped corpus"),
    _row("org:cross-team-write-isolation", "org_team",
         "Cross-team staging isolation: a write aimed at another team's shard is "
         "refused",
         DONOR, 50, "tests.unit.core.test_org_isolation",
         "drop the target-team assertion: a cross-team write succeeds"),
    _row("org:curator-gate-on-shared-writes", "org_team",
         "The curator gate on shared-root writes, plus the payload contract keys",
         DONOR, 50, "tests.unit.ops.test_org_curator",
         "allow a non-curator shared write: the shared corpus becomes writable "
         "by every teammate"),
    _row("org:bash-python-resolver-parity", "org_team",
         "The bash and Python knowledge-dir resolvers agree for flat, org and "
         "config-home-overridden configurations (every agent spawn resolves "
         "through the bash one: task 35 collapsed the six hooks onto the single "
         "common.sh seam, and codex-stream.py spawns the script directly)",
         DONOR, 36, "tests.integration.test_resolver_parity",
         "diverge either resolver's org-root tier: the hooks and the CLI resolve "
         "different knowledge dirs in the same session — "
         "TestParityGateBites.test_dropping_the_org_tier_reddens runs exactly "
         "that mutation against a copy of the script, alongside the config-home, "
         "abspath and blank-value arms"),
    _row("context:marker-driven-project-context-resolution",
         "loop_orchestration",
         "The project/repo CONTEXT the session-start banner and the "
         "steering-capture auto-scope both read: a marker-driven upward walk "
         "that resolves in ANY repository layout (the donor required a vendor "
         "marker file whose parent was named `src`, and answered empty fields "
         "with exit 0 in every other layout — a silent failure the hook renders "
         "as no banner at all), plus the deprecated workspace/package key "
         "mirrors so a consumer written against the donor vocabulary keeps "
         "resolving",
         BOTH, 18, "tests.gates.test_whereami_gate",
         "reintroduce a parent-directory-name constraint on the project dir, "
         "add a non-standard marker to the root table (directly or via a table "
         "it composes), home the resolver in the cli leaf where the "
         "features-layer caller cannot import it, or drop a deprecated key "
         "mirror"),
    _row("org:marker-driven-org-root-identity", "org_team",
         "The org-root identity anchor: when a project name is configured, a "
         "resolved org root whose STANDARD project markers do not declare that "
         "name as a WHOLE token is refused loudly instead of silently becoming "
         "the live knowledge base. Genericised off one vendor's build system "
         "onto the marker table, with the deprecated key spelling still READ so "
         "an existing config.env keeps the guard",
         DONOR, 17, "tests.gates.test_orgidentity_gate",
         "relax the match to a substring (a sibling checkout named <Name>V2 "
         "satisfies <Name>), shrink the marker table below its floor, or stop "
         "reading the deprecated key spelling (the guard silently disarms for "
         "every operator who configured it before the rename)"),
    _row("org:additive-team-mode-migration", "org_team",
         "Team mode arrives as a REGISTERED, idempotent migration step leaving "
         "flat behaviour byte-identical",
         DONOR, 63, "tests.unit.features.test_migrate_runner",
         "make the step non-idempotent: a second apply changes entry counts"),

    # ----------------------------------------------------------- security/trust
    _row("sec:signed-semver-update-channel", "security_trust",
         "The signed SemVer-tag update channel with its six self-heal call sites",
         LOCAL, 41, "tests.unit.features.test_pkg_channel",
         "accept an unsigned or untrusted tag: the updater installs an "
         "unverified package"),
    _row("sec:donor-plain-fetch-updater", "security_trust",
         "The donor's plain-fetch updater, replaced wholesale by the signed "
         "SemVer channel",
         DONOR, 41, "tests.unit.features.test_pkg_channel",
         "restore the plain fetch: an unsigned update installs",
         status="superseded", superseded_by="sec:signed-semver-update-channel"),
    _row("sec:trust-publisher-anchor", "security_trust",
         "The trust publisher signing-key anchor (no donor equivalent at all)",
         LOCAL, 40, "tests.unit.core.test_pkg_trust_anchor",
         "accept a key absent from the anchor: any publisher can sign a release"),
    _row("sec:scoped-staging-allowlist", "security_trust",
         "The donor's scoped staging allowlist with its 256 KiB blob cap, "
         "converting ALL eight blanket `git add -A` call sites",
         DONOR, 53, "tests.unit.ops.test_kb_git",
         "restore a blanket add at any one of the eight sites: one survivor "
         "defeats staging isolation"),
    _row("sec:push-retry-backoff-jitter", "security_trust",
         "Push retry with backoff and deterministic jitter, required because "
         "this tree has fanout AND graph run --concurrency",
         DONOR, 53, "tests.integration.test_kb_push",
         "remove the retry loop: simulated push contention fails the run"),
    _row("sec:knowledge-content-injection-scan", "security_trust",
         "Injection and conflict-marker scanning of KNOWLEDGE CONTENT before it "
         "is injected into a session",
         DONOR, 53, "tests.integration.test_target_injection",
         "skip the scan: a poisoned knowledge file reaches the session prompt"),
    _row("sec:transcript-redaction-on-the-commit-path", "security_trust",
         "Transcript secret redaction at the PRODUCER boundary, so every "
         "committed sink a mined turn reaches — the emitted workorder, its "
         "directory name, and the promoted preferences entry — inherits it",
         LOCAL, 61, "tests.unit.ops.test_prefer_queue_redaction",
         "drop the redaction call in `_prefer_queue_scan`: a credential in a "
         "preference-shaped prompt reaches work/auto/pref-*/plan.md, "
         "learnings/pref-*.md and index.json, all of them committed"),

    # ---------------------------------------------------------------- dream/ops
    _row("dream:four-restored-nightly-steps", "dream_maintenance",
         "The four nightly dream steps the donor dropped, restored as four files "
         "behind the step registry",
         LOCAL, 46, "tests.unit.features.test_dream",
         "unregister any one restored step: the registry stops matching this "
         "tree's full set"),
    _row("dream:race-free-lock", "dream_maintenance",
         "The race-free dream lock critical section, replacing the donor's "
         "read-decide-write TOCTOU",
         LOCAL, 46, "tests.unit.features.test_dream_min_interval",
         "restore read-decide-write acquisition: a concurrent double-acquire "
         "shows two winners"),
    _row("dream:donor-toctou-lock", "dream_maintenance",
         "The donor's read-decide-write dream lock, replaced by this tree's "
         "atomic critical section",
         DONOR, 46, "tests.unit.features.test_dream_min_interval",
         "restore the donor acquisition: the double-acquire probe shows two "
         "winners",
         status="superseded", superseded_by="dream:race-free-lock"),
    _row("dream:step-registry-package", "dream_maintenance",
         "The donor's dream step PACKAGE — one file per step behind a registry, "
         "which is the destination shape the restored steps land in",
         DONOR, 21, "tests.test_dream_steps_package",
         "collapse the package back to one module: the per-step file ceiling "
         "stops being enforceable"),
    _row("dream:min-interval-guard", "dream_maintenance",
         "The dream min-interval gate, so a second cycle inside the interval is "
         "skipped rather than run",
         LOCAL, 61, "tests.unit.features.test_dream_min_interval",
         "remove the interval check: a second dream inside the window runs"),
    _row("dream:never-mutates-the-package-tree", "dream_maintenance",
         "The INVARIANT the retired step violated, gated rather than assumed: no "
         "dream step reaches the package root, the dream corpus hard-resets "
         "nothing, and the CLI refuses the retired step id by name",
         LOCAL, 16, "tests.gates.test_dreampkg_retire_gate",
         "register any step whose body reads the package root, or restore a "
         "hard-reset in the dream corpus: the mutation check names the step and "
         "the file"),
    _exrow("dream:package-pull-step", "dream_maintenance",
           "Dream step \"0\": a tip-of-main `git pull --ff-only`/`--rebase` on "
           "the PACKAGE repository root at the head of every nightly cycle, with "
           "a py_compile anti-brick check and a hard-reset revert",
           DONOR,
           "a rival UNSIGNED updater. It defeated the signed-tag thesis of the "
           "dedicated updater, could stamp a higher schema out-of-band so the "
           "updater's brick-guard then refused rollback and stranded the "
           "machine, and raced the periodic tick on the same working tree with "
           "non-cross-checking locks. This tree retired it in feature 260712 "
           "task 13; the donor fork predates that and still carries it.",
           "plan task 16 (operator-approved self-extension plan)",
           "none for the step. Package updates are delegated to the signed "
           "SemVer-tag updater (plan task 41), the ONLY path that may advance "
           "the repository root; its shared post-check is retained by the "
           "KB-pull step, which stays.",
           {"kind": "cli-absent",
            "argv": ["dream", "--steps", "0", "--dry-run"],
            "expect": "unknown dream step(s): 0"}),

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
]
