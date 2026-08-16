"""agentware_lib.features.updatecmd -- the `update` verb (layer: features).

Owns: the `update` CLI -- `cmd_update` and the `register_update` registrar
declaring its six flags (`--check`, `--catalog`, `--self-heal`,
`--staleness-check`, `--status-line`, plus the inherited `--format`).

The flags are five distinct READ-ONLY reports plus one mutating default, which is
why they share a handler rather than a subcommand tree: every one of them answers
"what is the state of this package right now?", and only the bare `update` applies
anything. `--check`, `--catalog`, `--staleness-check` and `--status-line` never
write, and `--self-heal` writes only to reconcile a crash the operator did not
cause.

`--status-line` is the SessionStart surface: no network, silent when there is
nothing to say. That silence is load-bearing -- a status line that speaks on every
session gets ignored, and then the one time it reports a stranded apply nobody
reads it.

Does NOT own: applying an update -> features.pkgapply; crash recovery ->
features.pkgreconcile; cadence, self-heal mechanics, staleness and heartbeat ->
features.pkgupdater; tag verification -> core.pkgchannel; the trust anchor ->
core.pkgtrust; `tick`, which drives the updater on a schedule and stays resident
until its own port.
Layer deps: features -> core (config, dates, gitio, pkgchannel, pkgstate, pkghome)
and same-layer features (pkgapply, pkgreconcile, pkgupdater). All DOWNWARD or
same-layer.
Fan-in: 0 (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

`cmd_update` is a PURE MOVE from the monolith; `register_update` is the monolith's
own argparse block lifted verbatim out of build_parser.
"""

import json
import os
import subprocess
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.core.dates import _now_iso
from agentware_lib.core.gitio import (
    _git,
    _git_head,
    _pinned_ref,
    git_fetch,
    git_has_upstream,
    git_is_clean,
    git_is_work_tree,
)
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.core.pkgchannel import (
    PKG_FETCH_TIMEOUT,
    _pkg_channel_tags,
    _pkg_resolve_verified_tag,
    _pkg_tag_sha,
    _pkg_ver_str,
    _pkg_version_tuple,
)
from agentware_lib.core.pkghome import _PkgStateError
from agentware_lib.core.pkgstate import _pkg_state_read, _pkg_state_update
from agentware_lib.features.pkgapply import (
    _catalog_skill_names_at_ref,
    _pkg_run_update,
    _pkg_version,
    _pkg_working_tree_dirty,
)
from agentware_lib.features.dreamgate import is_session_active
from agentware_lib.features.pkgupdater import (
    _pkg_emit_heartbeat,
    _pkg_lock_status_word,
    _pkg_self_heal,
    _pkg_staleness_prehook,
    resolve_updater,
)


def register_update(sub, fmt_parent):
    """Declare the `update` verb. Lifted verbatim from the monolith's build_parser."""
    p_update = sub.add_parser(
        "update", parents=[fmt_parent],
        help="self-update the agentware package (fast-forward-only; re-runs "
             "R-PKG-04/05 gates on apply)")
    p_update.add_argument(
        "--check", action="store_true",
        help="report current vs upstream + changelog, then STOP (read-only; "
             "safe for a SessionStart updates-available nudge)")
    p_update.add_argument(
        "--catalog", action="store_true",
        help="also show which authored skill-catalog entries the pending "
             "update would add/remove (read-only)")
    p_update.add_argument(
        "--self-heal", action="store_true", dest="self_heal",
        help="reconcile a crashed prior apply (re-smoke -> finalize/rollback/"
             "strand); used by the loop reverse-guard on a stale updater lock")
    p_update.add_argument(
        "--staleness-check", action="store_true", dest="staleness_check",
        help="layer-2 on-use nudge: if the last signed-channel fetch is >24h old, "
             "read-only fetch + WARN (never applies; always exits 0)")
    p_update.add_argument(
        "--status-line", action="store_true", dest="status_line",
        help="read-only, no-network one-liner for SessionStart (silent when "
             "up-to-date; surfaces a stranded/unfinalized apply)")
    p_update.set_defaults(func=cmd_update)


def _update_self_heal(target, emit):
    """`update --self-heal` — reconcile a crashed prior apply and report.

    A migrate ERROR during self-heal is a real anomaly (package kept, but
    `migrate --apply` failed) and must exit NON-ZERO even though it does not set
    needs_manual_recovery — mirroring the normal apply path's migrate-error. A
    self-heal that silently exits 0 on that state is how a broken KB schema stays
    invisible to the loop that calls this.
    """
    kdir = require_knowledge_dir()
    if not kdir:
        return emit({"ok": True, "status": "no-kb"},
                    ["update: no knowledge dir — nothing to heal."], 0)
    r = _pkg_self_heal(kdir, target)
    heal_failed = (bool(r.get("needs_manual_recovery"))
                   or str(r.get("status", "")).endswith("migrate-error"))
    return emit({"ok": not heal_failed, "self_heal": r},
                ["update: self-heal -> %s (%s)"
                 % (r["status"], r.get("detail", ""))],
                1 if heal_failed else 0)


def cmd_update(args):
    """Dedicated, hardened self-update of the agentware PACKAGE via a SIGNED
    SemVer tag channel — NEVER @{upstream}/tip-of-main.

    The apply decision is keyed off a `vX.Y.Z` tag VERIFIED against the HOME-pinned
    publisher key (Task 3), not the branch-tip behind count. Strict, non-reorderable
    sequence (Task 6): record last_good BEFORE advance -> ff-only apply the verified
    CONCRETE SHA -> assert HEAD==SHA -> smoke -> (pass) `migrate --apply`; a smoke
    failure auto-rolls-back to last_good and quarantines the tag. The manual `update`
    and the tick serialize on ONE updater lock. Client apply gate = SMOKE ONLY
    (`eval --record --gate` is a publisher pre-tag concern via `gate release --full`,
    never re-run here — it would write reliability rows into the operator KB).

    `--check` is read-only w.r.t. the WORK TREE (executes no pulled code, applies
    nothing) but DOES fetch from the remote (a network op: `git fetch` for the
    @{upstream} view + a `fetch --tags` in the resolver), so it is NOT for a
    SessionStart nudge; the no-network SessionStart mode is `--status-line`. `--check`
    reports the verified-tag resolution; the @{upstream} behind/ahead + `--catalog`
    diff is kept purely INFORMATIONAL (never the apply decision)."""
    target = REPO_ROOT
    check_only = getattr(args, "check", False)
    show_catalog = getattr(args, "catalog", False)
    fmt = getattr(args, "format", "text")

    def emit(payload, lines, rc):
        if fmt == "json":
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            for line in lines:
                print(line)
        return rc

    if getattr(args, "status_line", False):
        # Read-only, NO-NETWORK one-liner for SessionStart. Prints ONLY when there
        # is something to surface; SILENT (suppressed) when up-to-date. Never blocks.
        try:
            st = _pkg_state_read()
        except _PkgStateError:
            print("agentware update: ⚠ trust/state anchor tampered — run "
                  "`scripts/agentware trust verify`")
            return 0
        if st.get("needs_manual_recovery"):
            print("agentware update: ⚠ a prior package apply is STRANDED "
                  "(needs_manual_recovery) — run `scripts/agentware update "
                  "--self-heal` (see ~/.agentware/update-state.json)")
        elif st.get("pending_apply"):
            print("agentware update: a package apply is unfinalized — the next "
                  "tick will reconcile it")
        return 0

    if getattr(args, "staleness_check", False):
        return _pkg_staleness_prehook(resolve_knowledge_dir(), target)

    if getattr(args, "self_heal", False):
        return _update_self_heal(target, emit)

    if not git_is_work_tree(target):
        return emit(
            {"ok": True, "status": "not-a-git-repo", "applied": False},
            ["update: package is not a git work tree — no self-update available."],
            0)

    lines = ["update: current %s (package %s)"
             % (_pinned_ref(), _pkg_version(target) or "unversioned")]
    payload = {"ok": True, "applied": False, "current": _pinned_ref()}

    # --- Informational @{upstream} view (NOT the apply decision) -------------
    info = {}
    if git_has_upstream(target) and git_fetch(target).returncode == 0:
        upstream_ref = _git(target, "rev-parse", "--abbrev-ref",
                            "--symbolic-full-name", "@{upstream}").stdout.strip()
        behind_s = _git(target, "rev-list", "--count", "HEAD..@{upstream}").stdout.strip()
        ahead_s = _git(target, "rev-list", "--count", "@{upstream}..HEAD").stdout.strip()
        info = {"upstream_ref": upstream_ref,
                "behind": int(behind_s) if behind_s.isdigit() else 0,
                "ahead": int(ahead_s) if ahead_s.isdigit() else 0}
        if show_catalog:
            up_names = _catalog_skill_names_at_ref("@{upstream}")
            if up_names is not None:
                cur_names = _catalog_skill_names_at_ref("HEAD") or set()
                info["catalog_changes"] = {"added": sorted(up_names - cur_names),
                                           "removed": sorted(cur_names - up_names)}
        lines.append("update: [informational] @{upstream} %s — behind %d / ahead %d "
                     "(NOT the apply decision; the signed-tag channel is)."
                     % (info["upstream_ref"], info["behind"], info["ahead"]))
        cc = info.get("catalog_changes")
        if cc and cc["added"]:
            lines.append("update: catalog skills added upstream: %s" % ", ".join(cc["added"]))
        if cc and cc["removed"]:
            lines.append("update: catalog skills removed upstream: %s" % ", ".join(cc["removed"]))
    payload["informational_upstream"] = info

    # --- Signed-channel resolution (the REAL apply decision) ----------------
    if check_only:
        r = _pkg_resolve_verified_tag(target)
        payload.update(mode="check", channel=r)
        for w in r.get("warnings", []):
            lines.append("update: ⚠ %s" % w)
        if r["status"] == "resolved":
            lines.append("update: signed update AVAILABLE — %s (%s) @ %s"
                         % (r["tag"], r["version"], r["sha"][:9]))
        elif r["status"] == "up-to-date":
            lines.append("update: up to date on the signed channel (%s)." % r["detail"])
        elif r["status"] == "no-trust":
            lines.append("update: fail-closed — %s (pin with `scripts/agentware "
                         "trust pin`)." % r["detail"])
        elif r["status"] == "skipped":
            lines.append("update: channel check skipped — %s." % r["detail"])
        else:
            lines.append("update: no signed update available (%s)." % r["detail"])
        return emit(payload, lines, 0)

    # --- Apply: the dedicated updater sequence (locked, fail-closed) ---------
    # Serialize the manual foreground apply against a LIVE loop session exactly like
    # the tick job (_pkg_updater_run): never land an apply mid-loop, so a running
    # loop can never read a half-updated package (audit C16). --check/--status-line
    # above are read-only and unaffected; --format json still returns cleanly.
    _sess_kdir = require_knowledge_dir()
    _active = is_session_active(_sess_kdir) if _sess_kdir else None
    if _active:
        payload.update(mode="apply", applied=False,
                       result={"status": "deferred",
                               "detail": "loop session active (%s)" % _active})
        return emit(payload,
                    lines + ["update: DEFERRED — a loop session is active (%s); not "
                             "applying mid-loop (retry after it finishes, or let the "
                             "scheduled tick apply it)." % _active], 0)
    result = _pkg_run_update(target)
    payload.update(mode="apply", result=result, tag=result.get("tag"),
                   sha=result.get("sha"), version=result.get("version"),
                   applied=(result["status"] == "applied"))
    for w in result.get("warnings", []):
        lines.append("update: ⚠ %s" % w)
    lines.append("update: %s" % result["detail"])
    status = result["status"]
    # A crash-window reconcile returns the SAME outcomes as a normal apply but
    # prefixed `reconciled-` (finalized / migrate-workorder / migrate-error / cleared
    # / rolledback). Normalize the prefix so a reconcile SUCCESS exits 0 and a
    # reconcile ERROR fails loudly, identical to the non-reconcile path — otherwise a
    # successful self-heal would exit 1 and a migrate-error would exit 0 (audit).
    norm = status[len("reconciled-"):] if status.startswith("reconciled-") else status
    graceful = {"applied", "up-to-date", "none", "skipped", "no-trust", "dirty",
                "locked", "migrate-workorder", "finalized", "cleared"}
    rc = 0 if norm in graceful else 1
    if result.get("needs_manual_recovery"):
        payload["ok"] = False
        rc = 1
        lines.append("update: ⚠ needs_manual_recovery — inspect "
                     "~/.agentware/update-state.json")
    elif norm in ("error", "migrate-error"):
        payload["ok"] = False
    return emit(payload, lines, rc)


_EXPORTS = [
    "cmd_update",
]

__all__ = list(_EXPORTS)
