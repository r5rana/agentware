"""Task-79 PARITY MUTATION SWEEP: the probe table, one runnable row per claim.

Feature 260813-modular-core-adoption, task 79.

Each curated parity row has always carried a `mutation` SENTENCE describing what
would have to change for its named test to go red. This file is the same
sentence, mechanised: `{row id: mutation spec}` over the closed grammar in
`tests/_parity_mutations.py`, every entry verified live by
`tests/_parity_sweep.py` to actually redden the test the row names.

WHY THE TABLE IS SEPARATE FROM THE ROWS
---------------------------------------
Three reasons, in order of weight.

1. The row tables are at their ceilings. `_parity_curated.py` hit 600 lines at
   task 75 and had to be split once already; inlining seventy-eight multi-line
   specs would immediately re-break both halves and force a second cosmetic
   split, which R-MOD-04 explicitly forbids ("split where the obligation
   changes, never to fit the code").
2. The obligation genuinely IS different. A row says what capability exists and
   which test guards it -- prose a reviewer reads. A probe says which bytes to
   change in which file -- a path-and-literal fixture that goes stale whenever
   the package moves, and that the sweep and the fast `--check` both consume as
   DATA. Keeping the volatile half apart means a package rename produces a diff
   here rather than through the ledger everyone reads.
3. It mirrors the shape task 10 already chose for `EXCLUSION_PROBES` and
   `GENERATED_EXCLUSIONS` in `tests/_parity_manifest.py`: adjudicate by row id,
   in one place, so an entry that matches no row is a hard error rather than
   dead weight.

`_row()` in `tests/_parity_merge_rows.py` attaches the probe when it builds the
row, so consumers still see ONE object -- `evidence` carries `test`, `mutation`
and `probe` together, and the manifest publishes all three.

HOW TO ADD A ROW
----------------
Author the spec, then prove it:

    python3 tests/_parity_sweep.py --spec '{"row":"<id>","test":"<dotted>",
                                            "mutation":{...}}'

`PASS` is the only acceptable result. `NOT-REDDENED` means the capability is
unguarded and the row is a promise nothing keeps -- fix the gap, do not reach
for a different mutation that happens to redden something.
"""

#: A function comfortably over the 120-line / 12-branch per-function cap,
#: generated rather than spelled out: the shape under test is "one oversized
#: function inside a SMALL file", and 130 hand-written lines of it in this table
#: would push the table itself past the `tests/**` ceiling it exists to guard.
_OVER_CAP_FUNCTION = (
    "\n\ndef _over_the_per_function_cap():\n    total = 0\n"
    + "".join("    total = total + %d\n" % i for i in range(130))
    + "    return total\n")


#: {row id: mutation spec}. Every entry is verified by the sweep; a row without
#: one fails `python3 tests/_parity_sweep.py --check`.
MUTATION_PROBES = {
    # ------------------------------------------------------------- cli_surface
    "surface:scaffold": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/scaffoldcmd.py",
        "find": "RC_BAD_UNIT = 2",
        "replace": "RC_BAD_UNIT = 1"
    },
    "surface:scaffold.new": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/scaffoldcmd.py",
        "find": "choices=list(scaffoldunit.KIND_NAMES),",
        "replace": "choices=list(scaffoldunit.KIND_NAMES) + [\"gadget\"],"
    },
    "surface:scaffold.check": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/scaffoldcmd.py",
        "find": "        return 0 if result[\"ok\"] else 1",
        "replace": "        return 0"
    },
    # ---------------------------------------------------------- docs_templates
    "docs:roadmap-preserved": {
        "kind": "delete-path",
        "path": "ROADMAP.md"
    },
    "docs:readme-union-anchors": {
        "kind": "replace",
        "path": "README.md",
        "find": "\n## Architecture\n",
        "replace": "\n## How it is put together\n"
    },
    "docs:template-frontmatter-corpus": {
        "kind": "replace",
        "path": "templates/learning-template.md",
        "find": "\nsummary: ",
        "replace": "\nsummry: "
    },
    "docs:artifact-capture-standard": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/curation/curationrender.py",
        "find": "fields.get(\"category\") or \"learnings\", body.rstrip(\"\\n\"), back_label)",
        "replace": "fields.get(\"category\") or \"learnings\", \"\\n\".join(body.rstrip(\"\\n\").split(\"```\")[::2]), back_label)"
    },
    "docs:architecture-extensibility": {
        "kind": "replace",
        "path": "docs/architecture.md",
        "find": "| `lib` file | 600 lines |",
        "replace": "| `lib` file | 601 lines |"
    },
    "docs:module-map-two-way-parity": {
        "kind": "write",
        "path": "tests/fixtures/doc-row-debt.json",
        "text": "{\n \"rows\": [\n  {\n   \"kind\": \"module_without_doc_row\",\n   \"location\": \"scripts/agentware_lib/ops/scaffoldcmd.py\",\n   \"value\": \"agentware_lib.ops.scaffoldcmd\"\n  }\n ]\n}\n"
    },
    "docs:add-a-feature-walkthrough": {
        "kind": "replace",
        "path": "docs/add-a-feature.md",
        "find": "tests/gates/test_suite_delta.py",
        "replace": "tests/gates/test_suite_delta_gone.py",
        "count": 0
    },
    "docs:walkthrough-is-executed": {
        "kind": "replace",
        "path": "docs/add-a-feature.md",
        "find": "-EXPECTED_SURFACES = 128\n+EXPECTED_SURFACES = 129",
        "replace": "-EXPECTED_SURFACES = 127\n+EXPECTED_SURFACES = 129"
    },
    "skills:modular-development-procedure": {
        "kind": "delete-path",
        "path": ".claude/skills/modular-development"
    },
    "docs:community-health-corpus": {
        "kind": "replace",
        "path": "SECURITY.md",
        "find": "## Reporting a vulnerability",
        "replace": "## How to shout about it"
    },
    "docs:licensing-declared-once": {
        "kind": "replace",
        "path": "LICENSE",
        "find": "3. Grant of Patent License",
        "replace": "3. No Patent License"
    },
    "docs:no-operator-identity-in-the-tree": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/privacy.py",
        "find": "    needles = [n for n in (needles or []) if n]\n    if not needles:\n        return []",
        "replace": "    needles = [n for n in (needles or []) if n]\n    if needles:\n        return []"
    },
    "docs:guidance-surface-contract": {
        "kind": "replace",
        "path": ".claude/skills/modular-development/SKILL.md",
        "find": "python3 tests/_ceiling_gate.py",
        "replace": "python3 tests/_ceiling_gate_v2.py"
    },
    # ------------------------------------------------------- dream_maintenance
    "dream:four-restored-nightly-steps": {
        "kind": "replace",
        "path": "scripts/agentware_lib/features/dreamsteps/registry.py",
        "find": "    (\"s\", \"stale-propose\", _dream_step_stale_propose),\n",
        "replace": ""
    },
    "dream:race-free-lock": {
        "kind": "replace",
        "path": "scripts/agentware_lib/features/dreamgate.py",
        "find": "fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)",
        "replace": "fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o644)"
    },
    "dream:donor-toctou-lock": {
        "kind": "replace",
        "path": "scripts/agentware_lib/features/dreamgate.py",
        "find": "    with _dream_acquire_critsec(path):",
        "replace": "    with contextlib.nullcontext():"
    },
    "dream:step-registry-package": {
        "kind": "write",
        "path": "scripts/agentware_lib/features/dreamsteps.py",
        "text": "\"\"\"The dream maintenance steps, collapsed back into ONE flat module.\n\nThis re-monolith undoes the task-21 split: the per-step files behind the\nregistry are folded back into a single module, so no per-step file ceiling can\nbe enforced any more.\n\"\"\"\n\nfrom agentware_lib.features.dreamsteps.registry import *  # noqa: F401,F403\n"
    },
    "dream:min-interval-guard": {
        "kind": "replace",
        "path": "scripts/agentware_lib/features/dream.py",
        "find": "    if age < DREAM_CADENCE_INTERVAL_SEC:",
        "replace": "    if False:"
    },
    "dream:never-mutates-the-package-tree": {
        "kind": "append",
        "path": "scripts/agentware_lib/features/dreamsteps/dreamgit.py",
        "text": "\n\ndef _dream_revert_worktree(target, sha):\n    \"\"\"Restored hard-reset in the dream corpus (the invariant this row gates).\"\"\"\n    return _git_timed(target, \"reset\", \"--hard\", sha)\n"
    },
    # ------------------------------------------------------ evaluation_quality
    "eval:ab-gate-layout-aware-extraction": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/regressiongate/abgate.py",
        "find": "[\"git\", \"archive\", \"HEAD\", \"scripts\"], cwd=root,",
        "replace": "[\"git\", \"archive\", \"HEAD\", \"scripts/agentware\"], cwd=root,"
    },
    "eval:gold-fixture-superset": {
        "kind": "replace",
        "path": "tests/fixtures/gold_fixture_suite.json",
        "find": "\"category\": \"skills\",",
        "replace": "\"category\": \"learnings\","
    },
    "eval:help-golden-corpus": {
        "kind": "delete-path",
        "path": "tests/fixtures/help_golden/attach.txt"
    },
    "eval:security-invariant-coverage": {
        "kind": "write",
        "path": "scripts/_unclaimed_coverage_probe.py",
        "text": "import os\n\n\ndef probe():\n    return os.getcwd()\n"
    },
    # ------------------------------------------------------------ kb_integrity
    "kb:atomic-durable-write": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/index/indexwrite.py",
        "find": "    fd, tmp = tempfile.mkstemp(prefix=\".index.\", suffix=\".tmp\", dir=d)\n    try:\n        with os.fdopen(fd, \"w\", encoding=\"utf-8\") as f:\n            json.dump(data, f, indent=2, ensure_ascii=False)\n            f.write(\"\\n\")\n            f.flush()\n            os.fsync(f.fileno())\n        os.chmod(tmp, _replace_mode(path))\n        os.replace(tmp, path)\n    except BaseException:",
        "replace": "    tmp = os.path.join(d, \".index.donor.unused\")\n    try:\n        with open(path, \"w\", encoding=\"utf-8\") as f:\n            json.dump(data, f, indent=2, ensure_ascii=False)\n            f.write(\"\\n\")\n    except BaseException:"
    },
    "kb:reentrant-index-lock": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/index/indexlock.py",
        "find": "    st[\"rlock\"].acquire()  # RLock: reentrant per-thread, serializes threads\n",
        "replace": "    yield\n    return\n"
    },
    "kb:quarantine-and-continue-rebuild": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/index/indexrebuild.py",
        "find": "            problems.append(\"missing frontmatter id (un-migrated?): %s\" % rel)\n            continue",
        "replace": "            raise ValueError(\"missing frontmatter id (un-migrated?): %s\" % rel)"
    },
    "kb:auto-rebuild-once-recovery": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/index/indexread.py",
        "find": "def load_index(kdir, _allow_rebuild=True):",
        "replace": "def load_index(kdir, _allow_rebuild=False):"
    },
    "kb:poisoned-index-list-normalizer": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/graph.py",
        "find": "        for tok in frontmatter._entry_list_field(e, \"relates\"):",
        "replace": "        for tok in (e.get(\"relates\") or []):",
        "count": 1
    },
    "kb:corpus-fingerprint-freshness-authority": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/bm25.py",
        "find": "    cache_fp = cache.get(\"fingerprint\")\n    if not cache_fp:\n        return False\n    stored_fp = data.get(\"corpus_fingerprint\") if isinstance(data, dict) else None\n    if stored_fp:\n        return cache_fp == stored_fp  # tier 1: O(1) in-memory — no tree re-read\n    cache_sig = cache.get(\"stat_signature\")\n    if cache_sig and cache_sig == _corpus_stat_signature(kdir, data):\n        return True  # tier 2: stat-only short-circuit — still no body reads\n    # tier 3: re-derive from the tree (the only tier that reads entry bodies).\n    live_fp = _corpus.eval_corpus_fingerprint(_corpus.build_corpus(kdir, data))\n    return cache_fp == live_fp",
        "replace": "    # RETIRED DESIGN RESTORED: the monotonic `generation` counter is the\n    # freshness authority again, with no content fingerprint or stat proxy.\n    cache_gen = cache.get(\"generation\")\n    live_gen = data.get(\"generation\") if isinstance(data, dict) else None\n    if cache_gen is None:\n        cache_gen = live_gen\n    return cache_gen == live_gen"
    },
    "kb:foreign-schema-cache-rejection": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/bm25.py",
        "find": "BM25_CACHE_VERSION = 3",
        "replace": "BM25_CACHE_VERSION = 1"
    },
    "kb:derived-artifacts-decommitted": {
        "kind": "replace",
        "path": "scripts/agentware_lib/core/kblayout.py",
        "find": "KB_DERIVED_GITIGNORE_RELS = (\"index.json\", \"FEATURES.md\") + tuple(",
        "replace": "KB_DERIVED_GITIGNORE_RELS = (\"FEATURES.md\",) + tuple("
    },
    "kb:roster-and-injection-lint": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/index/indexvalidate.py",
        "find": "        if title.replace(\"*\", \"\").strip():\n            return True",
        "replace": "        if title:\n            return True"
    },
    "kb:pointer-target-guard": {
        "kind": "replace",
        "path": "scripts/agentware_lib/core/kblayout.py",
        "find": "    idx = os.path.join(path, INDEX_REL)\n    if os.path.isfile(idx):",
        "replace": "    idx = os.path.join(path, INDEX_REL)\n    if not os.path.isfile(idx):\n        return False, \"no %s\" % INDEX_REL\n    if os.path.isfile(idx):"
    },
    "kb:init-clobber-guard-union": {
        "kind": "replace",
        "path": "scripts/agentware_lib/features/setupcmd.py",
        "find": "        if existing and os.path.abspath(existing) != os.path.abspath(kdir):\n            pointable, reason = kblayout.kb_pointable(existing)\n",
        "replace": "        if existing and os.path.abspath(existing) != os.path.abspath(kdir) \\\n                and os.path.isfile(os.path.join(existing, kblayout.SENTINEL_REL)):\n            pointable, reason = kblayout.kb_pointable(existing)\n"
    },
    # ------------------------------------------------------ loop_orchestration
    "loop:adversarial-review-gate": {
        "kind": "replace",
        "path": "scripts/loop/review.sh",
        "find": "run_selfheal_review() {\n  # Toolkit / init guards",
        "replace": "run_selfheal_review() {\n  return 0  # gate neutered: PROCEED unconditionally\n  # Toolkit / init guards"
    },
    "loop:worklog-marker-regexes-fail-closed": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/worklog.py",
        "find": "LEARNED_RE = re.compile(r\"^\\s*(?:[-*+]\\s+)?>?\\s*LEARNED:\\s*(.*?)\\s*$\")",
        "replace": "LEARNED_RE = re.compile(r\"^\\s*>?\\s*LEARNED:\\s*(.*?)\\s*$\")"
    },
    "loop:outcome-value-union": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/fleet/fleetshard.py",
        "find": "\"premise_falsified\", \"adversarial_review_blocked\",",
        "replace": "\"adversarial_review_blocked\","
    },
    "loop:org-lint-hook-gates": {
        "kind": "replace",
        "path": "scripts/loop/hooks.sh",
        "find": "  run_org_lint_gate pre-hook\n",
        "replace": "  :\n"
    },
    "loop:retrieval-quality-gate": {
        "kind": "replace",
        "path": "scripts/loop/hooks.sh",
        "find": "  run_retrieval_quality_gate\n",
        "replace": "  :\n"
    },
    "loop:session-start-roster-injection": {
        "kind": "replace",
        "path": "scripts/agentware_lib/features/skillsync.py",
        "find": "    if not installed and not pending:\n        return 0",
        "replace": "    if True:\n        return 0"
    },
    "context:marker-driven-project-context-resolution": {
        "kind": "replace",
        "path": "scripts/agentware_lib/core/projectcontext.py",
        "find": "    (\"package_name\", \"project_name\"),\n",
        "replace": ""
    },
    # -------------------------------------------------------------- modularity
    "mod:lib-decomposition": {
        "kind": "write",
        "path": "scripts/agentware_lib/ops/undeclared_module.py",
        "text": "\"\"\"Owns: nothing yet - a lib module added without its manifest entry.\n\nDoes NOT own: anything.\n\"\"\"\n\n\ndef helper():\n    return 1\n"
    },
    "mod:module-manifest-catalog": {
        "kind": "replace",
        "path": "catalog/modules.json",
        "find": "    {\n      \"layer\": \"scripts\",\n      \"line_cap\": 250,\n      \"path\": \"scripts/agentware_dashboard/confine.py\"\n    },\n",
        "replace": ""
    },
    "mod:per-function-caps": {
        "kind": "append",
        "path": "scripts/agentware_lib/core/dates.py",
        "text": _OVER_CAP_FUNCTION
    },
    "mod:dashboard-package-route-union": {
        "kind": "replace",
        "path": "scripts/agentware_dashboard/routes.py",
        "find": "    \"/api/alerts\": readevals.api_alerts,\n",
        "replace": ""
    },
    "mod:legacy-shrink-only-ratchet": {
        "kind": "replace",
        "path": "tests/fixtures/legacy-ratchet.json",
        "find": "\"legacy_lines\": 0,",
        "replace": "\"legacy_lines\": 500,"
    },
    "mod:legacy-shrink-port-coupling": {
        "kind": "replace",
        "path": "tests/_legacy_ratchet.py",
        "find": "    for verb in sorted(set(ported) - (verbs if verbs is not None\n                                      else union_verbs())):\n",
        "replace": "    for verb in sorted(set()):\n"
    },
    "mod:symmetric-promotion-gate": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/recallcmd.py",
        "find": "p_recall.add_argument(\"--strategy\", choices=(\"bm25\", \"bm25+acr\"),",
        "replace": "p_recall.add_argument(\"--strategy\", choices=(\"bm25\",),"
    },
    "mod:donor-only-verb-presence": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/team.py",
        "find": "    (\"team\", 2500, \"register_team\"),\n",
        "replace": ""
    },
    "mod:scaffold-wiring-audit": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/scaffoldcheck.py",
        "find": "        findings.extend(check_docs(root, manifest))\n",
        "replace": ""
    },
    "mod:ci-runs-one-entrypoint": {
        "kind": "replace",
        "path": ".github/workflows/ci.yml",
        "find": "      - name: Run the gate\n        run: python3 tests/_ci_gate.py",
        "replace": "      - name: Run the tests\n        run: python3 -m unittest discover -s tests/unit -t .\n\n      - name: Run the gate\n        run: python3 tests/_ci_gate.py"
    },
    "mod:no-vacuous-gate-command": {
        "kind": "replace",
        "path": "tests/_ceiling_gate.py",
        "find": "if __name__ == \"__main__\":\n    sys.exit(main())",
        "replace": "# the dispatch task 78 found missing, removed again\n"
    },
    "mod:commit-series-single-identity": {
        "kind": "replace",
        "path": "tests/_commit_series.py",
        "find": "        for role in (\"author\", \"committer\"):\n            if c[\"%s_name\" % role] != name:",
        "replace": "        for role in (\"author\",):\n            if c[\"%s_name\" % role] != name:"
    },
    "mod:commit-series-identity-is-anchored-not-declared": {
        "kind": "replace",
        "path": "tests/_commit_series.py",
        "find": "    for key in (\"name\", \"email\", \"emails\"):",
        "replace": "    for key in ():"
    },
    "mod:green-is-the-recorded-baseline": {
        "kind": "replace",
        "path": "tests/_suite_gate.py",
        "find": "    for node_id, row in sorted(declared.items()):\n        if node_id not in live:",
        "replace": "    for node_id, row in sorted(declared.items()):\n        if False and node_id not in live:"
    },
    "mod:fresh-clone-one-command": {
        "kind": "delete-path",
        "path": "scripts/agentware_lib/knowledge/index"
    },
    # ---------------------------------------------------------------- org_team
    "org:team-inference-from-knowledge-dir": {
        "kind": "replace",
        "path": "scripts/agentware_lib/core/config/orgresolve.py",
        "find": "    if not kdir:\n        return None\n    norm = os.path.normpath(kdir).replace(os.sep, \"/\")",
        "replace": "    return \"alpha\"\n    norm = os.path.normpath(kdir).replace(os.sep, \"/\")"
    },
    "org:corpus-scoping-and-shared-precedence": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/recallcmd.py",
        "find": "    scoped_entries = _skillpin.scope_entries_for_team(",
        "replace": "    ranked = _skillpin.recall_ranked(kdir, data, query)\n    scoped_entries = _skillpin.scope_entries_for_team("
    },
    "org:cross-team-write-isolation": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/orglint.py",
        "find": "            if declared and declared != team:",
        "replace": "            if False:"
    },
    "org:curator-gate-on-shared-writes": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/fleet/fleetguard.py",
        "find": "    if not _is_curator(org_root, handle):",
        "replace": "    if False:"
    },
    "org:bash-python-resolver-parity": {
        "kind": "replace",
        "path": "scripts/aw-knowledge-dir",
        "find": "  org=\"$(aw_lookup_key AGENTWARE_ORG_ROOT",
        "replace": "  return 1\n  org=\"$(aw_lookup_key AGENTWARE_ORG_ROOT"
    },
    "org:marker-driven-org-root-identity": {
        "kind": "replace",
        "path": "tests/_orgidentity_gate.py",
        "find": "            elif declared != PROBE_NAME_MATCH and matched:",
        "replace": "            elif False:"
    },
    "org:additive-team-mode-migration": {
        "kind": "replace",
        "path": "scripts/agentware_lib/features/migratecmd.py",
        "find": "    return [m for m in _KB_MIGRATIONS if m[0] > cur]",
        "replace": "    return [m for m in _KB_MIGRATIONS if m[0] >= cur]"
    },
    # -------------------------------------------------------- retrieval_policy
    "retrieval:stage-1-skill-pin-floor": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/skillpin.py",
        "find": "        scored = _apply_skill_pin(scored, SKILL_PIN_FLOOR, top_k)\n        if top_k is not None:",
        "replace": "        if top_k is not None:"
    },
    "retrieval:read-time-trust-and-freshness": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/freshsignals.py",
        "find": "    return {\"stale\": sig[\"stale\"],\n            \"superseded_by\": list(sig[\"superseded_by\"]),\n            \"trust\": sig[\"trust\"]}",
        "replace": "    return {\"stale\": sig[\"stale\"],\n            \"superseded_by\": list(sig[\"superseded_by\"])}"
    },
    "retrieval:staleness-and-supersede-downweight": {
        "kind": "replace",
        "path": "scripts/agentware_lib/knowledge/freshsignals.py",
        "find": "    eid = entry.get(\"id\") if isinstance(entry, dict) else None\n    if eid is not None and eid in superseded:\n        return STALENESS_SUPERSEDED_MULT",
        "replace": "    eid = entry.get(\"id\") if isinstance(entry, dict) else None\n    if eid is not None and eid in superseded:\n        return 1.0"
    },
    "retrieval:token-budget-ladder": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/recallcmd.py",
        "find": "1500",
        "replace": "10000",
        "count": 0
    },
    "retrieval:work-scope-plan-status-multipliers": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/workcorpus.py",
        "find": "_WORK_STATUS_MULT = {\"superseded\": 0.3, \"cancelled\": 0.3, \"done\": 0.7}",
        "replace": "_WORK_STATUS_MULT = {\"superseded\": 1.0, \"cancelled\": 1.0, \"done\": 1.0}"
    },
    "retrieval:work-corpus-stat-short-circuit": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/workcorpus.py",
        "find": "    cached = _load_work_corpus_cache(kdir, digest)\n    if cached is not None:\n        return cached\n",
        "replace": "    _load_work_corpus_cache(kdir, digest)\n"
    },
    "retrieval:bm25-exact-score-pin": {
        "kind": "replace",
        "path": "scripts/agentware_lib/retrieval/bm25.py",
        "find": "_BM25_K1 = 1.5",
        "replace": "_BM25_K1 = 1.45"
    },
    # ---------------------------------------------------------- security_trust
    "sec:signed-semver-update-channel": {
        "kind": "replace",
        "path": "scripts/agentware_lib/core/pkgchannel.py",
        "find": "    if cp.returncode == 0:\n        return True, \"verified signed tag\"",
        "replace": "    if True:\n        return True, \"verified signed tag\""
    },
    "sec:donor-plain-fetch-updater": {
        "kind": "replace",
        "path": "scripts/agentware_lib/core/pkgchannel.py",
        "find": "        sha = _resolve_candidate(target, tag, signers, result)",
        "replace": "        sha = _pkg_tag_sha(target, tag)"
    },
    "sec:trust-publisher-anchor": {
        "kind": "replace",
        "path": "scripts/agentware_lib/core/pkgtrust.py",
        "find": "    for fp in sorted(intree_fps - pinned):",
        "replace": "    for fp in sorted(intree_fps & pinned):"
    },
    "sec:scoped-staging-allowlist": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/kbstage.py",
        "find": "    allow, rejected = [], []\n    for rel in git_changed_paths(target):",
        "replace": "    allow, rejected = [], []\n    return list(git_changed_paths(target)), []\n    for rel in git_changed_paths(target):"
    },
    "sec:push-retry-backoff-jitter": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/kbsync.py",
        "find": "    for attempt in range(1, attempts + 1):",
        "replace": "    for attempt in range(1, 1):"
    },
    "sec:knowledge-content-injection-scan": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/planauthor/plansanitize.py",
        "find": "    v = _sanitize_provenance(value)",
        "replace": "    return value"
    },
    "sec:transcript-redaction-on-the-commit-path": {
        "kind": "replace",
        "path": "scripts/agentware_lib/ops/preferqueue.py",
        "find": "            body = _transcript_redact_secrets(body)",
        "replace": "            body = body  # redaction dropped"
    },
    "mod:mutation-proof-is-executed": {
        "kind": "replace",
        "path": "tests/_parity_mutation_rows.py",
        "find": "    \"docs:roadmap-preserved\": {\n        \"kind\": \"delete-path\",\n        \"path\": \"ROADMAP.md\"\n    },\n",
        "replace": ""
    },
    "mod:neutrality-redaction-at-the-write-boundary": {
        "kind": "replace",
        "path": "tests/_neutrality_redact.py",
        "find": "    rows, meta = gate.load_needles(needles_path)",
        "replace": "    rows, meta = ([], {c: {\"status\": \"builtin\", "
                   "\"source\": \"builtin\", \"count\": 0} "
                   "for c in gate.CLASSES})\n"
                   "    del needles_path  # a private list, drifting from the scan\n"
    },
    "mod:object-store-authorship-census": {
        "kind": "replace",
        "path": "tests/_portability_store.py",
        "find": "    out = _git(repo, \"cat-file\", \"--batch-all-objects\",",
        "replace": "    out = _git(repo, \"rev-list\", \"--all\")\n"
                   "    return sorted(_lines(out))\n"
                   "    out = _git(repo, \"cat-file\", \"--batch-all-objects\","
    },
    "mod:mutation-sweep-runs-in-ci": {
        "kind": "replace",
        "path": "tests/_ci_gate.py",
        "find": "        _stage(\"parity-sweep\", \"parity mutation sweep (every curated row)\",",
        "replace": "        _stage(\"parity-sweep-RENAMED\", \"parity mutation sweep (every curated row)\","
    },
}


def probe_for(row_id):
    """The runnable mutation for a curated row, or None if it has none yet."""
    spec = MUTATION_PROBES.get(row_id)
    return dict(spec) if spec else None
