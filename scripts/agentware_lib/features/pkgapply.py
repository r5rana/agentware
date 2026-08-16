"""agentware_lib.features.pkgapply -- APPLYING a verified update (layer: features).

Owns: everything between "we have a trustworthy tag" and "the tree now runs it, or
is back exactly where it started" -- the pre-flight syntax check (_pkg_syntax_check),
the bounded subprocess runner (_pkg_run_bounded) and post-apply smoke test
(_pkg_smoke), the git-liveness and stale-index-lock guards (_pkg_git_process_live /
_pkg_clear_stale_index_lock), the ROLLBACK (_pkg_rollback), the version reader
(_pkg_version), the KB migration hook (_pkg_migrate_after_apply), the dirty-tree
predicate (_pkg_working_tree_dirty) and the two-layer runner
(_pkg_run_update / _pkg_run_update_locked).

WHY EVERY STEP IS BOUNDED AND REVERSIBLE
This code rewrites the tree it is itself running from. A hang leaves the operator
with no working CLI, and a half-applied update leaves one that imports but does not
work -- so every subprocess is time-bounded, the new revision is SYNTAX-checked and
SMOKE-tested before it is accepted, and any failure rolls back to the recorded
previous sha rather than leaving the tree mid-flight. The tag that failed is
quarantined so the next tick does not walk into the same breakage.

The apply is serialised by the pkgstate lock and refuses to start on a DIRTY work
tree: overwriting uncommitted operator changes is not a recoverable error, so it is
prevented rather than rolled back.

Does NOT own: deciding a tag is trustworthy -> core.pkgchannel; the state file and
its lock -> core.pkgstate; WHEN to run (cadence, self-heal, heartbeat) ->
features.pkgupdater; the `update` CLI -> features.updatecmd; the release preflight
(task 42).
Layer deps: features -> core (gitio, pkgstate, pkgchannel, pkghome, paths, dates,
config) and features.dreamgate for the session/lock predicates. All DOWNWARD or
same-layer.
Fan-in: 2 at landing (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import json
import os
import re
import subprocess
import sys
import time

import agentware_lib.core.config as config
import agentware_lib.core.gitio as gitio
import agentware_lib.core.paths as paths
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.features.dreamgate import dream_acquire_lock, dream_release_lock
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.gitio import (
    _git,
    _git_head,
    _git_noninteractive_env,
    _git_timed,
    _is_ancestor,
    _pinned_ref,
    git_fetch,
    git_has_upstream,
    git_is_clean,
    git_is_work_tree,
)
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.core.pkgchannel import (
    PKG_CHANNEL_RE,
    _pkg_channel_tags,
    _pkg_verify_tag,
    PKG_FETCH_TIMEOUT,
    _pkg_resolve_verified_tag,
    _pkg_tag_sha,
    _pkg_ver_str,
    _pkg_version_tuple,
)
from agentware_lib.core.pkghome import _PkgStateError
from agentware_lib.core.pkgtrust import _pkg_assert_signers_secure
from agentware_lib.core.pkgstate import (
    UPDATER_LOCK_REL,
    _pkg_is_quarantined,
    _pkg_quarantine_tag,
    _pkg_state_lock,
    _pkg_state_read,
    _pkg_state_update,
    _pkg_state_write,
)


def _pkg_working_tree_dirty():
    """True iff the package working tree has uncommitted changes (offline `git
    status --porcelain` on REPO_ROOT). FAIL-OPEN for the change-gate: any git
    error returns True (treated as dirty -> RUN), so a real regression is never
    masked by a git failure."""
    cp = _git(REPO_ROOT, "status", "--porcelain")
    if cp.returncode != 0:
        return True
    return bool(cp.stdout.strip())


PKG_SMOKE_TIMEOUT = 600       # generous hard cap: a growing suite never false-FAILs a good tag


def _pkg_syntax_check(target=None):
    """py_compile the on-disk CLI; None if OK, else an error string.

    A neutral generalization of the syntax check that the retired dream package
    step used to own. The retired step's own NAME is deliberately not repeated
    here: `tests/_dreampkg_gate.py` exempts only the preserved monolith from its
    literal scan, so a new published module naming it would redden that gate --
    correctly, since the point of the gate is that the step is gone from every
    surface the package actually ships.

    Bootstrap-deadlock: the updater never depends on dream code."""
    import py_compile
    script = os.path.join(target or REPO_ROOT, "scripts", "agentware")
    try:
        py_compile.compile(script, doraise=True)
        return None
    except py_compile.PyCompileError as exc:
        return str(exc)


def _pkg_run_bounded(cmd, cwd, kdir, timeout):
    """Run one smoke subcommand with a HARD timeout: on expiry kill the entire
    process group (so a hung new version FAILS rather than hanging the tick) and
    return rc 124. Returns (rc, last_output_line). Read-only w.r.t. the ledger."""
    env = _git_noninteractive_env()
    if kdir:
        env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    env.setdefault("AGENTWARE_NESTED_UNITTEST", "1")   # never recurse into the suite
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)
        with proc:
            try:
                out, _e = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, 9)
                except (OSError, ProcessLookupError):
                    proc.kill()
                proc.wait()
                return 124, "timed out after %ds" % timeout
            rc = proc.returncode
        lines = (out or "").strip().splitlines()
        return rc, (lines[-1] if lines else "")
    except FileNotFoundError:
        return 127, "CLI not found"
    except OSError as exc:
        return 126, "OS error: %s" % exc


def _pkg_smoke(kdir, timeout=PKG_SMOKE_TIMEOUT, target=None):
    """Decide PASS/FAIL of a freshly-applied package by EXIT CODE. Steps (none
    write the ledger/scorecard):
      (0) py_compile syntax check (cheapest; catches a broken pull pre-exec);
      (1) steering lint (R-PKG-04);
      (2) CLI-liveness — config resolve + index validate + a canary recall all
          exit 0 (a broken/import-broken toolkit IS a bricked machine, the single
          most important check).
    Each exec'd subcommand runs the ON-DISK (possibly newly-pulled) CLI under a
    GENEROUS hard timeout so a growing suite never false-FAILs a good tag into
    rollback. PASS = every step exits 0. Returns {ok, steps, detail}."""
    target = target or REPO_ROOT
    cli = os.path.join(target, "scripts", "agentware")
    steps = []
    syn = _pkg_syntax_check(target)
    steps.append({"name": "syntax", "ok": syn is None,
                  "rc": 0 if syn is None else 1, "detail": syn or "parses"})
    # Short-circuit: a syntax-broken CLI cannot be exec'd — skip the rest, FAIL.
    if syn is None:
        checks = [
            ("steering-lint", [cli, "steering", "lint"], None),
            ("config-resolve", [cli, "config", "--knowledge-dir-only"], kdir),
            ("index-validate", [cli, "index", "validate"], kdir),
            ("canary-recall", [cli, "recall", "smoke canary liveness probe",
                               "--format", "json"], kdir),
        ]
        for name, cmd, kd in checks:
            rc, detail = _pkg_run_bounded(cmd, target, kd, timeout)
            steps.append({"name": name, "ok": rc == 0, "rc": rc, "detail": detail})
    ok = all(s["ok"] for s in steps)
    return {"ok": ok, "steps": steps,
            "detail": ("smoke PASS" if ok else "smoke FAIL: " +
                       ", ".join(s["name"] for s in steps if not s["ok"]))}


def _pkg_git_process_live(target):
    """Best-effort: True only if a git process referencing `target` is POSITIVELY
    found. Conservative — an unknown state reads as not-live, so a genuinely stale
    lock is still cleanable (the caller also gates on lock mtime age)."""
    try:
        cp = subprocess.run(["pgrep", "-fl", "git"], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, timeout=5,
                            check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    if cp.returncode != 0:
        return False
    return any(target in line for line in cp.stdout.splitlines())


def _pkg_clear_stale_index_lock(target, max_age=None):
    """Remove a STALE `.git/index.lock` — mtime older than the apply timeout AND
    no live git process on `target` — that a crashed prior apply left behind and
    that would otherwise block `git reset --hard`. Returns True if it cleared one."""
    max_age = max_age if max_age is not None else PKG_FETCH_TIMEOUT
    lock = os.path.join(target, ".git", "index.lock")
    try:
        st = os.lstat(lock)
    except OSError:
        return False
    age = time.time() - st.st_mtime
    if age > max_age and not _pkg_git_process_live(target):
        try:
            os.remove(lock)
            return True
        except OSError:
            return False
    return False


def _rollback_refusal(state, target, last_good):
    """Every reason a rollback must be REFUSED, or None when it may proceed.

    Each refusal escalates to needs_manual_recovery rather than failing quietly:
    a rollback that declines to run leaves the machine on code that just failed
    smoke, so the operator has to know.
    """
    # Brick-ordering guard: refuse a package rollback once migrate advanced the
    # schema (would strand: older CLI vs newer .schema).
    pending = state.get("pending_apply") or {}
    if pending.get("migrated"):
        return ("migrate already stamped a higher KB schema — package "
                "rollback FORBIDDEN (brick-ordering); needs_manual_recovery")

    # Clear a crashed prior apply's stale index.lock so the reset can proceed.
    _pkg_clear_stale_index_lock(target)

    # Clean-tree guard: never discard operator edits with a hard reset.
    if not git_is_clean(target):
        return ("work tree not clean — refusing hard reset to avoid "
                "eating operator edits; needs_manual_recovery")

    head = _git_head(target)
    if head and head != last_good and not _is_ancestor(target, last_good, "HEAD"):
        return ("last_good %s is not an ancestor of HEAD — refusing a "
                "non-backward reset; needs_manual_recovery" % last_good[:9])
    return None


def _rollback_reset(target, last_good):
    """Perform the backward reset and CONFIRM it landed. Error string or None."""
    rc = _git(target, "reset", "--hard", last_good)   # authorized R-GIT-02 exception
    if rc.returncode != 0:
        return ("git reset --hard %s FAILED: %s; needs_manual_recovery"
                % (last_good[:9], (rc.stderr or "").strip()[:200]))
    now_head = _git_head(target)
    if now_head != last_good:
        return ("post-reset HEAD %s != last_good %s; needs_manual_recovery"
                % ((now_head or "?")[:9], last_good[:9]))
    return None


def _pkg_rollback(failed_tag, target=None, kdir=None, reason="smoke failed"):
    """Roll REPO_ROOT back to the recorded last_good_sha and re-smoke.

    Authorized R-GIT-02 exception (self-extension warning): REPO_ROOT-only,
    backward-only, clean-tree-guarded. FORBIDDEN once migrate stamped a higher KB
    schema (brick-ordering: an older package meeting a newer .schema fails loud).
    If the rollback itself cannot complete, escalates to `needs_manual_recovery`
    (last_good PRESERVED) and returns loudly — NEVER a silent skip.

    Returns {ok, restored, sha, resmoke_ok, needs_manual_recovery, detail}."""
    target = target or REPO_ROOT
    kdir = kdir or resolve_knowledge_dir()
    state = _pkg_state_read()
    last_good = state.get("last_good_sha")
    result = {"ok": False, "restored": False, "sha": last_good,
              "resmoke_ok": None, "needs_manual_recovery": False, "detail": ""}

    def _escalate(detail):
        # Preserve last_good; flag manual recovery; loud (never status:skipped).
        try:
            _pkg_state_update(needs_manual_recovery=True)
        except (OSError, _PkgStateError):
            pass
        result["needs_manual_recovery"] = True
        result["detail"] = detail
        return result

    if not last_good:
        return _escalate("no recorded last_good_sha — cannot roll back "
                         "(needs_manual_recovery)")

    refusal = _rollback_refusal(state, target, last_good)
    if refusal:
        return _escalate(refusal)

    reset_err = _rollback_reset(target, last_good)
    if reset_err:
        return _escalate(reset_err)
    result["restored"] = True

    # Quarantine the failed tag + clear the crash-window marker (anti-thrash).
    if failed_tag:
        _pkg_quarantine_tag(failed_tag, reason)
    _pkg_state_update(pending_apply=None)

    # Re-smoke to CONFIRM the machine is restored to known-good.
    resmoke = _pkg_smoke(kdir or target, target=target)
    result["resmoke_ok"] = resmoke["ok"]
    result["ok"] = resmoke["ok"]
    result["detail"] = ("rolled back to last_good %s; re-smoke %s"
                        % (last_good[:9], "PASS" if resmoke["ok"] else "FAIL"))
    if not resmoke["ok"]:
        # Even known-good fails smoke now — escalate (do not leave silently broken).
        _escalate(result["detail"] + " — known-good STILL fails smoke; "
                  "needs_manual_recovery")
    return result


def _pkg_version(target=None):
    """Current package version signal for rollout visibility: the stable tag at
    HEAD via `git describe --tags --exact-match` (vX.Y.Z), else last_applied_version
    from the sidecar, else None. Extends `_pinned_ref` (Task 11)."""
    target = target or REPO_ROOT
    cp = _git(target, "describe", "--tags", "--exact-match")
    if cp.returncode == 0:
        t = cp.stdout.strip()
        if PKG_CHANNEL_RE.match(t):
            return t.lstrip("v")
    return _pkg_state_read().get("last_applied_version")


def _pkg_migrate_after_apply(target, kdir):
    """Trigger the NEWLY-APPLIED on-disk CLI's migration runner as a SUBPROCESS
    (the running process still holds the OLD code in memory, so an in-process call
    would run the OLD migrations). Returns (status, payload):
      'ok'      — migrate applied / nothing pending (payload = JSON result)
      'refused' — a RESTRUCTURING migration is pending (emit a workorder, keep pkg)
      'error'   — older-CLI guard or exec failure (a real error)."""
    cli = os.path.join(target, "scripts", "agentware")
    env = _git_noninteractive_env()
    env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    try:
        cp = subprocess.run([cli, "migrate", "--apply", "--format", "json"],
                            cwd=target, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            timeout=PKG_SMOKE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "error", {"detail": "migrate --apply timed out"}
    except OSError as exc:
        return "error", {"detail": "migrate exec failed: %s" % exc}
    # `migrate --apply --format json` prints the JSON object FOLLOWED BY a human
    # summary line (e.g. "migrate: refusing to auto-apply RESTRUCTURING ..."), so a
    # plain json.loads(cp.stdout) would fail on the trailing text and mis-triage a
    # restructuring-refusal as an 'error'. Parse only the LEADING JSON object.
    payload = {}
    out = (cp.stdout or "").strip()
    if out:
        try:
            payload, _end = json.JSONDecoder().raw_decode(out)
        except (ValueError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if cp.returncode == 0:
        return "ok", payload
    if payload.get("refused"):
        return "refused", payload
    return "error", (payload or {"detail": (cp.stderr or cp.stdout or "").strip()[:300]})


def _pkg_run_update(target=None, kdir=None, now_iso=None):
    """Dedicated updater apply sequence. Serializes on the single-writer updater
    lock (SAME lock as the tick; the reverse loop-guard depends on it being held
    during ANY apply). Returns a result dict {status, detail, tag, sha, version,
    warnings, needs_manual_recovery?}. status ∈ {applied, up-to-date, none,
    skipped, no-trust, dirty, rolled-back, migrate-workorder, migrate-error,
    error, locked}."""
    target = target or REPO_ROOT
    kdir = kdir or require_knowledge_dir()
    now_iso = now_iso or _now_iso()
    res = {"status": "error", "detail": "", "warnings": [],
           "tag": None, "sha": None, "version": None}
    if not kdir:
        res["detail"] = "no knowledge dir configured — cannot lock/update"
        return res
    if not git_is_work_tree(target):
        res.update(status="none", detail="package is not a git work tree")
        return res
    # Single-writer lock (SAME lock as the tick). The dirty-tree check is done
    # INSIDE the lock (TOCTOU-safe) by _pkg_run_update_locked. A LIVE apply's lock is
    # NEVER reclaimed as stale (pid_alive_never_stale) so two applies can never run
    # concurrently even when the first legitimately exceeds the ttl (audit C16).
    if not dream_acquire_lock(kdir, lock_rel=UPDATER_LOCK_REL,
                              pid_alive_never_stale=True):
        res.update(status="locked",
                   detail="another updater/tick apply holds the lock — skipping")
        return res
    try:
        # Reconcile a crashed prior apply FIRST, UNDER THE LOCK — so a concurrent
        # tick/manual apply can never misread a LIVE apply's marker as crashed and
        # run `git reset --hard` on the same tree. If reconciliation acts, stop
        # (heal now; the normal apply runs on the next tick).
        # Function-local: pkgreconcile imports THIS module at module scope for
        # the apply primitives, so a module-scope import back would close a
        # cycle. Deferring keeps the edge strictly one-way.
        from agentware_lib.features.pkgreconcile import _pkg_reconcile_pending
        rec = _pkg_reconcile_pending(target, kdir)
        if rec is not None:
            return rec
        return _pkg_run_update_locked(target, kdir, now_iso, res)
    finally:
        dream_release_lock(kdir, lock_rel=UPDATER_LOCK_REL)


def _apply_head_mismatch(target, kdir, res, tag, sha, version, new_head):
    """Post-ff HEAD != the verified SHA. Idempotent no-op, or roll back.

    An ff-only that left HEAD unchanged because `sha` is already an ANCESTOR of
    HEAD (a version-newer tag whose COMMIT HEAD already contains — git reports
    "Already up to date") is a BENIGN idempotent no-op, NOT a rollback. Treat it
    as success: record last_applied so the resolver's no-downgrade stops the
    per-tick thrash, WITHOUT re-smoking/migrating (the tree never moved) and
    WITHOUT a false "rolled-back"/quarantine (audit D8).
    """
    if new_head and _is_ancestor(target, sha, new_head):
        with _pkg_state_lock():
            _pkg_state_write(dict(_pkg_state_read(), last_good_sha=new_head,
                                  last_good_tag=tag, last_applied_version=version,
                                  pending_apply=None))
        res.update(status="up-to-date", tag=tag, version=version,
                   detail="signed tag %s (%s) is already contained in HEAD %s — "
                          "idempotent no-op (recorded last_applied; no rollback)"
                          % (tag, version, new_head[:9]))
        return res
    rb = _pkg_rollback(tag, target=target, kdir=kdir,
                       reason="HEAD != verified SHA after apply")
    res.update(status="rolled-back",
               needs_manual_recovery=rb.get("needs_manual_recovery"),
               detail="post-apply HEAD %s != verified SHA %s — rolled back (%s)"
                      % ((new_head or "?")[:9], sha[:9], rb["detail"]))
    return res


def _apply_migrate_triage(res, mstatus, mpayload, tag, sha, version):
    """migrate refused or errored AFTER a passing smoke: KEEP the package.

    Neither branch rolls back, and that asymmetry is deliberate. Smoke passed, so
    the applied code works; a rollback would discard a good package over a KB
    concern. `refused` means a restructuring migration needs an operator decision
    (a workorder), `error` is a real anomaly surfaced loudly — and the loop
    pre-hook re-runs migrate each run, so a transient failure self-heals.
    """
    with _pkg_state_lock():
        _pkg_state_write(dict(_pkg_state_read(), last_good_sha=sha,
                              last_good_tag=tag, last_applied_version=version,
                              pending_apply=None))
    if mstatus == "refused":
        ref = mpayload.get("refused") or {}
        res.update(status="migrate-workorder",
                   detail="applied signed tag %s (%s); migrate REFUSED a "
                          "restructuring migration (%s) — create a migration "
                          "workorder (package KEPT, not rolled back)"
                          % (tag, version, ref.get("name", "?")))
        return res
    res.update(status="migrate-error",
               detail="applied signed tag %s (%s); smoke PASS but migrate "
                      "--apply errored: %s (package KEPT; surfaced)"
                      % (tag, version, mpayload.get("detail", "see logs")))
    return res


def _pkg_run_update_locked(target, kdir, now_iso, res):
    # Assumes the single-writer updater lock is HELD by the caller.
    # Dirty package tree -> graceful skip (never ff onto uncommitted edits).
    if not git_is_clean(target):
        res.update(status="dirty",
                   detail="package work tree has uncommitted changes — skipping "
                          "(commit/stash first; never applying onto edits)")
        return res
    # 1-2. resolve + verify + no-downgrade (records last_check_ts/last_fetch_ts).
    r = _pkg_resolve_verified_tag(target, now_iso=now_iso)
    res["warnings"] = r.get("warnings", [])
    if r["status"] in ("skipped", "no-trust", "none", "up-to-date"):
        res.update(status=r["status"], detail=r["detail"])
        return res
    if r["status"] != "resolved":
        res.update(status="error", detail="unexpected resolver status %r" % r["status"])
        return res
    tag, sha, version = r["tag"], r["sha"], r["version"]
    res.update(tag=tag, sha=sha, version=version)

    # 3. record last_good BEFORE advance — HARD fail-closed precondition. A
    # first-ever apply must NEVER advance with an empty rollback target.
    cur_head = _git_head(target)
    if not cur_head:
        res.update(status="error", detail="cannot resolve current HEAD")
        return res
    try:
        with _pkg_state_lock():   # atomic RMW: no unlocked writer clobbers this
            _pkg_state_write(dict(_pkg_state_read(),
                                  last_good_sha=cur_head,
                                  pending_apply={"applying_tag": tag,
                                                 "applying_sha": sha,
                                                 "migrated": False}))
    except (OSError, _PkgStateError) as exc:
        try:
            _pkg_state_update(last_check_ts=now_iso)
        except (OSError, _PkgStateError):
            pass
        res.update(status="error",
                   detail="pre-advance last_good write FAILED (%s) — NOT applying "
                          "(fail-closed: no rollback target)" % exc)
        return res

    # 5. ff-only apply the CONCRETE verified SHA (never re-resolve — TOCTOU-safe).
    ff = _git(target, "merge", "--ff-only", sha)
    if ff.returncode != 0:
        _pkg_state_update(pending_apply=None)   # tree untouched by a refused ff
        res.update(status="error",
                   detail="ff-only apply to %s refused: %s (diverged/ahead)"
                          % (sha[:9], (ff.stderr or "").strip()[:200]))
        return res

    # 6. assert HEAD == verified SHA (re-assert; TOCTOU).
    new_head = _git_head(target)
    if new_head != sha:
        return _apply_head_mismatch(target, kdir, res, tag, sha, version, new_head)

    # 7. smoke (client apply gate = SMOKE ONLY; no eval --record --gate).
    smoke = _pkg_smoke(kdir, target=target)
    if not smoke["ok"]:
        # 8. rollback + quarantine (migrate has NOT run — rollback is safe).
        rb = _pkg_rollback(tag, target=target, kdir=kdir,
                           reason="smoke failed: %s" % smoke["detail"])
        res.update(status="rolled-back",
                   needs_manual_recovery=rb.get("needs_manual_recovery"),
                   detail="smoke FAILED (%s) — %s" % (smoke["detail"], rb["detail"]))
        return res

    # 9. migrate --apply (smoke passed). Triage exit; NO gate/rollback after this.
    mstatus, mpayload = _pkg_migrate_after_apply(target, kdir)
    if mstatus in ("refused", "error"):
        return _apply_migrate_triage(res, mstatus, mpayload, tag, sha, version)

    # migrate ok -> set migrated=true (crash-window marker), then FINALIZE.
    _pkg_state_update(pending_apply={"applying_tag": tag, "applying_sha": sha,
                                     "migrated": True})
    with _pkg_state_lock():
        _pkg_state_write(dict(_pkg_state_read(), last_good_sha=sha, last_good_tag=tag,
                              last_applied_version=version))
    _pkg_state_update(pending_apply=None)        # clear marker LAST
    res.update(status="applied",
               detail="applied signed tag %s (%s) -> %s; smoke PASS; migrate ok"
                      % (tag, version, sha[:9]))
    return res


def _catalog_skill_names_at_ref(ref):
    """Set of skill names in catalog/skills.json at a git ref, or None.

    Read-only (`git show <ref>:catalog/skills.json`, no checkout). Returns None
    when the blob is absent or unparseable so a `--catalog` diff can SKIP rather
    than mis-report. Pure git — no new dependency.
    """
    cp = gitio._git(paths.REPO_ROOT, "show", "%s:catalog/skills.json" % ref)
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout)
    except (ValueError, TypeError):
        return None
    return {s.get("name") for s in data.get("skills", [])
            if isinstance(s, dict) and s.get("name")}


_EXPORTS = [
    "PKG_SMOKE_TIMEOUT",
    "_pkg_working_tree_dirty",
    "_pkg_syntax_check",
    "_pkg_run_bounded",
    "_pkg_smoke",
    "_pkg_git_process_live",
    "_pkg_clear_stale_index_lock",
    "_pkg_rollback",
    "_pkg_version",
    "_pkg_migrate_after_apply",
    "_catalog_skill_names_at_ref",
    "_pkg_run_update",
    "_pkg_run_update_locked",
]

__all__ = list(_EXPORTS)
