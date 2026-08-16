"""agentware_lib.features.pkgreconcile -- CRASH RECOVERY for a half-applied update (layer: features).

Owns: the reconciler that runs when the updater finds state describing an apply
that never finished -- `_pkg_reconcile_pending`, plus `_pkg_tag_at_head_if_newer`,
which answers "is HEAD already at (or past) the tag we were moving to?".

WHY THIS EXISTS AT ALL
The updater rewrites the tree it is running from, so it can be killed at any point:
mid-fetch, mid-checkout, after checkout but before the smoke test, after a rollback
but before the state file was updated. On the next tick the recorded state and the
actual tree can disagree in either direction. Reconciling means deciding, from the
TREE rather than from the record, whether the pending move actually landed --
finishing the bookkeeping if it did, reverting if it did not, and quarantining the
tag if it broke something.

Split from `features.pkgapply` at task 41 for two reasons: applying and recovering
are different obligations (one moves forward from a known-good state, the other
reasons about an UNKNOWN state), and together they exceeded the 600-line module
cap. `pkgapply` reaches this module through a function-local import so the edge
stays one-way and no cycle exists.

Does NOT own: performing an apply or a rollback -> features.pkgapply; tag
resolution -> core.pkgchannel; the state file and its lock -> core.pkgstate;
scheduling -> features.pkgupdater.
Layer deps: features -> core (gitio, pkgstate, pkgchannel, pkghome, dates) plus a
function-local edge back to features.pkgapply for the apply primitives it reuses.
Fan-in: 1 at landing (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import json
import os
import subprocess

# The apply PRIMITIVES this reconciler reuses. Imported at module scope is safe in
# THIS direction: pkgapply reaches back only through a function-local import, so
# the module-scope edge runs one way and no cycle is reachable.
from agentware_lib.features.pkgapply import (
    _pkg_migrate_after_apply,
    _pkg_rollback,
    _pkg_smoke,
)

from agentware_lib.core.dates import _now_iso
from agentware_lib.core.gitio import (
    _git,
    _git_head,
    _git_timed,
    _is_ancestor,
    git_is_clean,
)
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.core.pkgchannel import (
    _pkg_channel_tags,
    _pkg_tag_sha,
    _pkg_verify_tag,
    _pkg_ver_str,
    _pkg_version_tuple,
)
from agentware_lib.core.pkghome import _PkgStateError
from agentware_lib.core.pkgtrust import _pkg_assert_signers_secure
from agentware_lib.core.pkgstate import (
    _pkg_quarantine_tag,
    _pkg_state_write,
    _pkg_state_lock,
    _pkg_state_read,
    _pkg_state_update,
)


def _pkg_tag_at_head_if_newer(target, applied):
    """If HEAD is exactly at a VERIFIED channel tag whose version > `applied`,
    return (tag, version_tuple); else (None, None). Detects an unfinalized apply
    after a crash between ff-advance and finalize."""
    head = _git_head(target)
    if not head:
        return None, None
    # Integrity-guard the trust anchor BEFORE consulting it, exactly like the main
    # resolver (18558): the crash-window reconcile must FAIL-CLOSED on a symlinked/
    # wrong-owner/group-writable anchor, never TRUST a tampered one to finalize an
    # apply the main path would refuse (audit A1). Fail-closed here == (None, None).
    try:
        signers = _pkg_assert_signers_secure()
    except _PkgStateError:
        return None, None
    for tag in _pkg_channel_tags(target):
        if _pkg_tag_sha(target, tag) != head:
            continue
        ver = _pkg_version_tuple(tag)
        if applied is not None and ver is not None and ver <= applied:
            return None, None
        ok, _d = _pkg_verify_tag(target, tag, signers)
        if ok:
            return tag, ver
        return None, None
    return None, None


def _pkg_reconcile_pending(target, kdir):
    """Reconcile a partial/interrupted prior apply, if any (else None). Assumes the
    updater lock is HELD by the caller (it mutates git/state).

    Detection: a `pending_apply` marker set, OR HEAD at a verified tag newer than
    last_applied. Before accepting ANYTHING it CONFIRMS the ff-advance actually
    landed (`HEAD == the applying SHA`): a crash BETWEEN the pre-advance marker write
    and `git merge --ff-only` leaves HEAD at the OLD good SHA, so re-smoking the OLD
    code would falsely stamp the NEW version — that case clears the marker WITHOUT
    advancing last_applied (the tag is re-attempted, never silently suppressed).
    When the advance DID land: never accept unsmoked code — re-smoke, finalize on
    pass; on FAIL roll back + quarantine when migrated==false, else strand LOUDLY
    (rollback forbidden post-migrate)."""
    state = _pkg_state_read()
    pending = state.get("pending_apply") or {}
    applied = _pkg_version_tuple(state.get("last_applied_version"))
    head_tag, head_tag_ver = _pkg_tag_at_head_if_newer(target, applied)

    if not pending and not head_tag:
        # No marker and HEAD is not at a newer verified tag -> nothing to reconcile
        # (a dirty tree here is an operator's in-progress edit — never touch it).
        return None

    head = _git_head(target)
    if head is None:
        # Transient git failure — do NOT mutate state; retry on the next tick.
        return {"status": "error",
                "detail": "cannot resolve HEAD during reconcile — retry next tick"}

    applying_sha = pending.get("applying_sha") or (head if head_tag else None)
    applying_tag = pending.get("applying_tag") or head_tag
    migrated = bool(pending.get("migrated"))

    # CONFIRM the ff-advance completed. If HEAD != the intended SHA, the advance
    # never happened (crash between the marker write and the merge) — the OLD code
    # is on disk, so DO NOT finalize a version bump onto it.
    if applying_sha is not None and head != applying_sha:
        return _reconcile_advance_missing(migrated)

    # HEAD == applying_sha (the advance landed). Never accept unsmoked code.
    smoke = _pkg_smoke(kdir, target=target)
    if smoke["ok"]:
        return _reconcile_smoke_pass(target, kdir, head, applying_tag,
                                     applying_sha, head_tag_ver)
    return _reconcile_smoke_fail(target, kdir, applying_tag, migrated)


def _reconcile_advance_missing(migrated):
    """HEAD != applying_sha: the ff-advance never happened.

    The OLD code is on disk, so a version bump must NOT be finalized onto it. If
    migrate had already stamped, tree and schema disagree in a way this function
    cannot safely repair, so it strands loudly; otherwise the stale marker is just
    cleared and the tag is re-attempted on the next tick.
    """
    if migrated:
        # migrate stamped but HEAD is not at the applied SHA — inconsistent.
        try:
            _pkg_state_update(needs_manual_recovery=True)
        except (OSError, _PkgStateError):
            pass
        return {"status": "stranded", "needs_manual_recovery": True,
                "detail": "pending marks migrated but HEAD != applying_sha — "
                          "inconsistent; needs_manual_recovery"}
    try:
        _pkg_state_update(pending_apply=None)
    except (OSError, _PkgStateError):
        pass
    return {"status": "reconciled-cleared",
            "detail": "crashed BEFORE the ff-advance (HEAD != applying_sha) — "
                      "cleared the stale marker; last_applied unchanged; the "
                      "tag will be re-attempted"}


def _reconcile_finalize(head, applying_tag, ver_str, detail):
    """Commit the reconciled version to state and clear the crash marker."""
    with _pkg_state_lock():
        _pkg_state_write(dict(_pkg_state_read(), last_good_sha=head,
                              last_good_tag=applying_tag,
                              last_applied_version=ver_str,
                              pending_apply=None))
    return {"status": "reconciled-finalized", "tag": applying_tag,
            "detail": detail}


def _reconcile_smoke_pass(target, kdir, head, applying_tag, applying_sha,
                          head_tag_ver):
    """Re-smoke PASSED: run the migrate triage, then finalize.

    Runs the SAME migrate triage as the normal apply path (do NOT defer it to the
    loop pre-hook only): a reconciled release must get its migrations applied
    consistently — refused emits a workorder + keeps the package, error surfaces
    loudly + keeps the package, ok proceeds (audit D13). migrate --apply is
    idempotent, so re-running it when `migrated` was already set is a no-op.
    """
    ver = _pkg_version_tuple(applying_tag) if applying_tag else head_tag_ver
    ver_str = _pkg_ver_str(ver)
    # BEFORE migrate can advance the KB .schema, persist the crash-window marker
    # with migrated=True (mirrors the apply path's own pre-finalize stamp). If the
    # process is killed during or after migrate but before the finalize lands, the
    # persisted marker stays migrated=True, so a LATER reconcile takes the
    # `not migrated` = False branch and STRANDS loudly (needs_manual_recovery)
    # instead of performing a FORBIDDEN post-migrate `git reset --hard` that would
    # restore the OLD package under a HIGHER schema (brick). The finalize clears it
    # on every success path. Stamping conservatively (even before a possible
    # refusal) fails toward a loud strand, never a silent brick.
    try:
        _pkg_state_update(pending_apply={"applying_tag": applying_tag,
                                         "applying_sha": applying_sha,
                                         "migrated": True})
    except (OSError, _PkgStateError):
        pass
    mstatus, mpayload = _pkg_migrate_after_apply(target, kdir)
    if mstatus == "refused":
        ref = mpayload.get("refused") or {}
        r = _reconcile_finalize(
            head, applying_tag, ver_str,
            "reconciled a crashed apply: re-smoke PASS -> finalized %s; "
            "migrate REFUSED a restructuring migration (%s) — create a "
            "migration workorder (package KEPT, not rolled back)"
            % (applying_tag, ref.get("name", "?")))
        r["status"] = "reconciled-migrate-workorder"
        return r
    if mstatus == "error":
        r = _reconcile_finalize(
            head, applying_tag, ver_str,
            "reconciled a crashed apply: re-smoke PASS -> finalized %s; "
            "migrate --apply errored: %s (package KEPT; surfaced)"
            % (applying_tag, mpayload.get("detail", "see logs")))
        r["status"] = "reconciled-migrate-error"
        return r
    return _reconcile_finalize(
        head, applying_tag, ver_str,
        "reconciled a crashed apply: re-smoke PASS -> finalized %s; migrate ok"
        % applying_tag)


def _reconcile_smoke_fail(target, kdir, applying_tag, migrated):
    """Re-smoke FAILED: roll back if migrate had not run, else STRAND.

    The asymmetry is brick-ordering: once migrate has advanced the KB schema, a
    `git reset --hard` back to the old package would leave OLD code running under
    a NEWER schema. That is unrecoverable without manual work, so the post-migrate
    branch refuses to roll back and strands loudly instead.
    """
    if not migrated:
        rb = _pkg_rollback(applying_tag, target=target, kdir=kdir,
                           reason="reconcile: unfinalized apply fails smoke")
        return {"status": "reconciled-rolledback",
                "needs_manual_recovery": rb.get("needs_manual_recovery"),
                "detail": "reconciled: re-smoke FAIL, migrate had NOT run -> "
                          "rolled back (%s)" % rb["detail"]}
    try:
        _pkg_state_update(needs_manual_recovery=True)
    except (OSError, _PkgStateError):
        pass
    return {"status": "stranded", "needs_manual_recovery": True,
            "detail": "STRANDED: post-migrate apply is unfinalized and fails smoke "
                      "— rollback forbidden (brick-ordering); needs_manual_recovery"}


_EXPORTS = [
    "_pkg_tag_at_head_if_newer",
    "_pkg_reconcile_pending",
]

__all__ = list(_EXPORTS)
