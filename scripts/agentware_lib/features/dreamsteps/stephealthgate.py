"""agentware_lib.features.dreamsteps.stephealthgate -- dream step "2", health-gate: the convergence verify-and-auto-fix battery (layer: features).

Owns: dream step "2" (_dream_step_health_gate), the battery that drives it
(_dream_health_gate_checks), and the nine named checks it decomposes into, one
per concern and in the cycle's fixed order: the onboarding sentinel
(_dream_hg_check_sentinel), config completeness with safe-default
forward-migration (_dream_hg_check_config), KB mode / handle / profile / schedule
consistency (_dream_hg_check_identity), knowledge-dir drift
(_dream_hg_check_kdir_drift), a stale or missing cron line
(_dream_hg_check_cron), MAIN.md + index.json structure
(_dream_hg_check_kb_structure), new shipped templates
(_dream_hg_check_templates), the KB .gitignore (_dream_hg_check_gitignore) and a
surfaced rebase-in-progress (_dream_hg_check_rebase).
Does NOT own: the KB-pull step whose pulls this converges after ->
dreamsteps.stepkbpull; the scheduler installer + cron-line renderer it calls ->
features.dreamsched; the other steps -> dreamsteps.step<step>; the ordered
registry that binds this handler to id "2" -> dreamsteps.registry. See
catalog/modules.json.
Layer deps: core (config, kblayout, paths, routing) + knowledge (index) + ops
(kbgit, staging) -- all DOWNWARD -- plus ONE features SIBLING (dreamsched, which
imports only core, so the edge is acyclic). No dreamsteps sibling is imported.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: non-destructive by construction (INV-2) -- it never deletes and
never rewrites a content file, only config.env and structural state, and each
check is best-effort so an unattended cron never crashes. paths.REPO_ROOT is
READ ONLY (the shipped templates are compared and copied FROM): no check mutates
the package tree.
"""

import json
import os
import subprocess

import agentware_lib.core.config as config
import agentware_lib.core.kblayout as kblayout
import agentware_lib.core.paths as paths
import agentware_lib.core.routing as routing
import agentware_lib.knowledge.index as index
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.staging as staging
import agentware_lib.features.dreamsched as dreamsched


def _dream_hg_check_sentinel(kdir, issues):
    """Check 1 — the .initialized sentinel exists and is well-formed."""
    sentinel = os.path.join(kdir, ".initialized")
    if not os.path.isfile(sentinel):
        issues.append("missing .initialized sentinel — needs re-onboarding")
        return
    try:
        with open(sentinel, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        content = ""
    if "Handle:" not in content:
        issues.append(".initialized sentinel malformed (no Handle line)")


def _dream_hg_check_config(fixes, issues):
    """Check 2 — config completeness with safe-default forward-migration."""
    required_keys = [
        ("AGENTWARE_KNOWLEDGE_DIR", "knowledge dir", None),
        ("AGENTWARE_KB_MODE", "KB mode", "power"),
        ("AGENTWARE_CLI", "runtime CLI", "claude"),
        ("AGENTWARE_KB_AUTOCOMMIT", "KB autocommit", "1"),
        ("AGENTWARE_KB_PUSH", "KB push", "1"),
        ("AGENTWARE_RETRIEVAL_MODE", "retrieval mode", "deterministic"),
    ]
    for key, label, default in required_keys:
        val = config._read_config_key(key)
        if not val:
            if default is not None:
                config._set_config_value(key, default)
                fixes.append("config %s missing, set to default '%s'" % (key, default))
            else:
                issues.append("config missing: %s (%s)" % (key, label))


def _dream_hg_check_identity(kdir, issues):
    """Checks 3-5 — KB mode + handle consistency, the team-mode profile overlay,
    and a configured schedule whenever dream is ON."""
    mode = config.resolve_kb_mode()
    handle = routing.resolve_user_handle()
    if mode == "team" and not handle:
        issues.append("team mode but no user_handle set — run: "
                      "scripts/agentware config --set-user-handle <handle>")
    if mode == "team" and handle:
        profile_path = os.path.join(kdir, "profiles", "%s.md" % handle)
        if not os.path.isfile(profile_path):
            issues.append("missing profile overlay: profiles/%s.md" % handle)
    if routing.resolve_dream() == "1":
        schedule = routing.resolve_dream_schedule()
        if not schedule:
            issues.append("dream is ON but no schedule configured")


def _dream_hg_check_kdir_drift(kdir, fixes):
    """Check 6 — the knowledge dir in config matches the ACTUAL kdir (detect
    config corruption).

    ONLY fix when there is NO env-var override active — an env var is an
    ephemeral, intentional override that must NEVER be persisted into config.
    When cron fires dream, no env var is set, so corruption is detected cleanly.
    """
    env_override = os.environ.get(config.CONFIG_KEY)
    cfg_kdir = config._read_config_key("AGENTWARE_KNOWLEDGE_DIR")
    if not env_override and cfg_kdir and os.path.abspath(cfg_kdir) != os.path.abspath(kdir):
        config._set_config_value("AGENTWARE_KNOWLEDGE_DIR", kdir)
        fixes.append("config AGENTWARE_KNOWLEDGE_DIR was %s, restored to %s"
                     % (cfg_kdir, kdir))


def _dream_hg_check_cron(kdir, fixes, issues):
    """Check 7 — cron/schedule: auto-fix if stale (not just report)."""
    if routing.resolve_dream() != "1" or not routing.resolve_dream_schedule():
        return
    cli_path = dreamsched._dream_cli_path()
    expected_line = dreamsched._dream_cron_line(routing.resolve_dream_schedule(), cli_path)
    try:
        cron_out = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=False)
        if cron_out.returncode == 0:
            needs_install = False
            if "agentware" not in cron_out.stdout:
                needs_install = True
            elif expected_line.strip() not in cron_out.stdout:
                needs_install = True
            if needs_install:
                rc = dreamsched._dream_install_schedule(kdir, as_json=False)
                if rc == 0:
                    fixes.append("dream cron line was stale/missing — "
                                 "re-installed schedule")
                else:
                    issues.append("dream cron stale and auto-install failed")
    except (OSError, FileNotFoundError):
        pass  # no crontab binary — skip (macOS uses launchd)


def _dream_hg_check_kb_structure(kdir, issues):
    """Checks 8-9 — MAIN.md exists; index.json exists and has entries."""
    main_path = os.path.join(kdir, "MAIN.md")
    if not os.path.isfile(main_path):
        issues.append("missing MAIN.md — KB structure incomplete")
    idx_path = os.path.join(kdir, index.INDEX_REL)
    if not os.path.isfile(idx_path):
        issues.append("missing index.json — will be rebuilt in step a")
        return
    try:
        with open(idx_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("entries"):
            issues.append("index.json has 0 entries — may need re-onboarding")
    except (json.JSONDecodeError, OSError):
        issues.append("index.json is corrupted — will be rebuilt in step a")


def _dream_hg_check_templates(kdir, fixes, issues):
    """Check 10 — template sync: copy any new templates from repo → KB (never
    clobber). The shipped template dir is READ ONLY; only the KB side is written.
    """
    src_templates = os.path.join(paths.REPO_ROOT, "templates")
    dst_templates = os.path.join(kdir, "templates")
    if not (os.path.isdir(src_templates) and os.path.isdir(dst_templates)):
        return
    for fn in sorted(os.listdir(src_templates)):
        if not fn.endswith(".md"):
            continue
        src = os.path.join(src_templates, fn)
        dst = os.path.join(dst_templates, fn)
        if not os.path.isfile(dst):
            try:
                import shutil
                shutil.copy2(src, dst)
                fixes.append("synced new template: %s" % fn)
            except OSError:
                issues.append("failed to sync template: %s" % fn)


def _dream_hg_check_gitignore(kdir):
    """Check 11 — KB .gitignore completeness (idempotent — adds missing rules
    only). Best-effort: an OSError never aborts the battery."""
    try:
        kblayout._ensure_kb_gitignore(kdir)
    except OSError:
        pass  # best-effort


def _dream_hg_check_rebase(kdir, issues):
    """Check 12 — rebase-in-progress detection (surface in the health report for
    discoverability)."""
    if kbgit.git_is_work_tree(kdir) and staging.git_rebase_in_progress(kdir):
        issues.append("KB has rebase-in-progress — steps a/f will skip; resolve "
                      "with: cd %s && git rebase --abort" % kdir)


def _dream_health_gate_checks(kdir):
    """Run all convergence checks and return (fixes_applied, issues_found).

    Each check verifies actual-vs-desired state and FIXES what it can.
    Returns two lists: fixes_applied (things auto-fixed) and issues_found
    (things that need manual intervention or a full re-onboarding).
    """
    fixes, issues = [], []
    _dream_hg_check_sentinel(kdir, issues)           # 1
    _dream_hg_check_config(fixes, issues)            # 2
    _dream_hg_check_identity(kdir, issues)           # 3, 4, 5
    _dream_hg_check_kdir_drift(kdir, fixes)          # 6
    _dream_hg_check_cron(kdir, fixes, issues)        # 7
    _dream_hg_check_kb_structure(kdir, issues)       # 8, 9
    _dream_hg_check_templates(kdir, fixes, issues)   # 10
    _dream_hg_check_gitignore(kdir)                  # 11
    _dream_hg_check_rebase(kdir, issues)             # 12
    return fixes, issues


def _dream_step_health_gate(kdir, dry_run):
    """Step 2 — convergence health-gate: verify + auto-fix system state.

    Checks that the running machine's config, profile, sentinel, schedule, and
    KB structure all match the desired state after pulls. Auto-fixes what it can
    (e.g. config corruption, stale cron, missing templates); reports what it
    can't (e.g. missing profile needs re-onboarding). Non-destructive: never
    deletes, never changes content files — only config.env and structural.
    """
    if dry_run:
        return {"status": "planned",
                "would": "health-gate convergence checks (config, profile, "
                         "sentinel, schedule, KB structure)"}
    fixes, issues = _dream_health_gate_checks(kdir)
    status = "ok" if not issues else ("warn" if not any(
        "re-onboarding" in i for i in issues) else "degraded")
    result = {"status": status, "fixes_applied": len(fixes),
              "issues_found": len(issues)}
    if fixes:
        result["fixes"] = fixes
    if issues:
        result["issues"] = issues
    return result


_EXPORTS = [
    "_dream_hg_check_sentinel",
    "_dream_hg_check_config",
    "_dream_hg_check_identity",
    "_dream_hg_check_kdir_drift",
    "_dream_hg_check_cron",
    "_dream_hg_check_kb_structure",
    "_dream_hg_check_templates",
    "_dream_hg_check_gitignore",
    "_dream_hg_check_rebase",
    "_dream_health_gate_checks",
    "_dream_step_health_gate",
]
