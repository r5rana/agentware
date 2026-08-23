"""agentware_lib.features.skillsync -- the vetted skill INSTALL GATE: reconcile, verdict, approve (layer: features).

Owns: the reconciliation between the KB's `skills/` and the per-machine execution
cache (`_skill_reconcile`), the per-skill verdict (`_skill_verdict`), the install
(`_skill_install_snapshot`), the roster views (`_skill_status`,
`_skill_status_line`), the name sources (`_skill_kb_names`,
`_package_skill_names`), the cross-process lock (`_skill_sync_lock`) and the
`skill sync` handler (`cmd_skill_sync`).
Does NOT own: the lockfile, the tree hash and the ownership test ->
features.skilllock; the KB-side authoring verbs (`skill add` / `remove` /
`validate` / `list` / `search`) -> features.skills; the proposal lifecycle ->
features.proposals; the `skill` registrar -> features.proposals.

THE ONE PROPERTY THIS WHOLE MODULE EXISTS FOR
---------------------------------------------
`--approve` is the ONLY path that installs. Nothing else — not a sync, not a
status, not a pull that changed a skill's body — puts a skill where a harness
will run it. A skill in the knowledge base is text that arrived over an unsigned
pull, authored by whoever could write to the corpus; a skill in the execution
cache is something a harness will EXECUTE without asking. The gap between those
two is the only place a human can stand, and this module is that gap.

QUARANTINE-ON-CHANGE
--------------------
Approval is bound to a CONTENT HASH, not to a name. When a skill's tree changes
after approval, `_skill_verdict` returns it to PENDING rather than carrying the
old approval forward — because the operator approved a body, not a title, and an
auto-carried approval would make "approve once" mean "approve everything this
name ever becomes". That is the supply-chain shape: the second version is the
one worth attacking precisely because the first was reviewed.

WHAT IS NOT A SECURITY SIGNAL
-----------------------------
The git author is DISPLAY-ONLY and shown under an "(UNVERIFIED)" label. It is
attacker-controllable over the same unsigned pull that delivered the skill, so
gating on it would be theatre — and theatre is worse than nothing here, because
it reads like verification.

FOREIGN COPIES ARE LEFT ALONE
-----------------------------
The cache is a directory a harness also writes. A cache dir this toolkit does not
own (per the lockfile) is never removed or overwritten without `--force-conflict`,
and a name that shadows a PACKAGE skill needs `--force-shadow`. Deleting someone
else's skill because it shares a name is the kind of destructive surprise that
ends trust in a tool permanently, and it would be silent.

Layer deps: features -> features (`skilllock` for the lockfile/hash/ownership
primitives, `skills` for the KB-side vetting), features -> core (`config`,
`paths`). Determinism: the reconcile is a pure fold over (KB names, cache state,
lockfile) — the same three inputs produce the same verdicts on every run (INV-1).
"""

import contextlib
import json
import os
import shutil
import sys
import tempfile

from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.core.dates import utc_now_iso
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.skilllock import (
    _SkillHashError,
    _empty_skill_lock,
    _skill_author,
    _skill_cache_dir,
    _skill_entry,
    _skill_is_binary,
    _skill_lock_path,
    _skill_name_ok,
    _skill_owned,
    _skill_rmtree,
    _skill_tree_link,
    _skill_tree_sha256,
    load_skill_lock,
    save_skill_lock,
)
from agentware_lib.features.skills import load_skill_catalog, vet_skill_dir


def _skill_verdict(skill_dir, name=None):
    """Map a skill tree to (verdict, findings) with BLOCK > REVIEW > PASS.

    `name` is the INTENDED install name to validate (defaults to the source dir
    basename for the sync/approve path, where basename IS the canonical name). A
    caller installing under a DIFFERENT target name (cmd_skill_add --source) MUST
    pass the target `name` so a non-conforming SOURCE basename does not falsely
    BLOCK a legitimately-named install (feature 260713-p4).

    BLOCK (never installable, even via --approve) if ANY of: a symlink is present
    (reuse _skill_tree_link), the install `name` fails _skill_name_ok, or
    vet_skill_dir reports any danger finding. BLOCK DOMINATES. REVIEW (quarantine;
    --approve installs with a loud unscannable-content warning) iff the ONLY issue
    is a binary/undecodable file (no danger, no symlink, valid name). PASS iff
    every file is text-decodable, no danger marker, no symlink, valid name.
    vet_skill_dir PASS is a filter signal, NOT a safety certificate — the operator
    approval is the real control. Read-only; reuses vet_skill_dir (no re-scan)."""
    findings = []
    if not os.path.isdir(skill_dir):
        return "BLOCK", ["skill source directory not found: %s" % skill_dir]
    if name is None:
        name = os.path.basename(os.path.normpath(skill_dir))
    if not _skill_name_ok(name):
        findings.append("invalid skill name: %r" % name)
    link = _skill_tree_link(skill_dir)
    if link is not None:
        findings.append("symlink present (refused): %s" % link)
    danger = vet_skill_dir(skill_dir)
    if danger:
        findings.extend(danger)
    if findings:
        # Any danger / symlink / invalid-name -> BLOCK dominates.
        return "BLOCK", findings
    # No danger: is there an undecidable (binary) file? -> REVIEW, else PASS.
    binaries = []
    for root, _dirs, files in os.walk(skill_dir, followlinks=False):
        for fn in files:
            p = os.path.join(root, fn)
            if os.path.islink(p):
                continue  # already excluded above
            if _skill_is_binary(p):
                binaries.append(os.path.relpath(p, skill_dir))
    if binaries:
        return "REVIEW", ["binary/undecodable file(s) (unscannable): %s"
                          % ", ".join(sorted(binaries))]
    return "PASS", []


def _skill_kb_names(kdir):
    """{name: skill_dir} for every KB skills/<name>/ that holds a SKILL.md."""
    base = os.path.join(kdir, "skills")
    out = {}
    if not os.path.isdir(base):
        return out
    for nm in os.listdir(base):
        d = os.path.join(base, nm)
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "SKILL.md")):
            out[nm] = d
    return out


def _package_skill_names():
    """Names of shipped PACKAGE skills (repo .claude/skills/ dirs + catalog) —
    reserved so a KB skill cannot shadow/impersonate a trusted package skill."""
    names = set()
    pkg = os.path.join(REPO_ROOT, ".claude", "skills")
    if os.path.isdir(pkg):
        for nm in os.listdir(pkg):
            if os.path.isdir(os.path.join(pkg, nm)):
                names.add(nm)
    data, _err = load_skill_catalog()
    if data:
        for s in data.get("skills", []):
            if isinstance(s, dict) and s.get("name"):
                names.add(s["name"])
    return names


@contextlib.contextmanager
def _skill_sync_lock():
    """Advisory flock around a sync/approve run so concurrent runs never lose
    lockfile updates (mirrors _index_lock). Best-effort: never blocks work if the
    lock cannot be taken. The sidecar lives beside the lockfile (machine-local)."""
    lockpath = _skill_lock_path() + ".lock"
    fcntl_mod = None
    try:
        import fcntl as fcntl_mod  # noqa: F401
    except Exception:  # noqa: BLE001 - non-posix: degrade to no lock
        fcntl_mod = None
    f = None
    try:
        try:
            os.makedirs(os.path.dirname(lockpath) or ".", exist_ok=True)
            f = open(lockpath, "w")
            if fcntl_mod is not None:
                fcntl_mod.flock(f.fileno(), fcntl_mod.LOCK_EX)
        except OSError:
            f = None  # best-effort; proceed unlocked rather than block work
        yield
    finally:
        if f is not None:
            try:
                if fcntl_mod is not None:
                    fcntl_mod.flock(f.fileno(), fcntl_mod.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
            f.close()


def _skill_reconcile(kdir, lock, cache_dir):
    """The TOTAL reconcile state machine. NEVER installs (D1): it only vets,
    hashes, records lockfile status, enforces the quarantined/blocked =>
    cache-absent invariant for OWNED copies, and GC-revokes skills gone from the
    KB. Ownership is by provenance (D12). Mutates `lock` in place; returns a
    counts summary. Deterministic, no network, no KB write."""
    summary = {"installed": 0, "quarantined": 0, "blocked": 0, "unchanged": 0,
               "revoked": 0, "rejected_name": 0}
    skills = lock.setdefault("skills", {})
    kb = _skill_kb_names(kdir)
    for name in sorted(kb):
        if not _skill_name_ok(name):
            summary["rejected_name"] += 1  # NEVER record the raw (crafted) name
            continue
        src = kb[name]
        verdict, findings = _skill_verdict(src)
        try:
            tree_hash = None if verdict == "BLOCK" else _skill_tree_sha256(src)
        except _SkillHashError:
            verdict, tree_hash, findings = "BLOCK", None, (findings or ["unhashable tree"])
        entry = skills.get(name)
        # Per-entry cache dir (S3 multi-cache): an installed entry is checked against
        # the dir it was installed into, not the reconcile's default cache_dir.
        entry_cache_dir = (entry.get("harness_dir") if entry else None) or cache_dir
        cache_path = os.path.join(entry_cache_dir, name)
        if entry is None:  # never seen -> NEVER install
            if verdict == "BLOCK":
                skills[name] = _skill_entry(kdir, name, None, "blocked", verdict, findings)
                summary["blocked"] += 1
            else:
                skills[name] = _skill_entry(kdir, name, tree_hash, "quarantined", verdict, findings)
                summary["quarantined"] += 1
            continue
        status = entry.get("status")
        recorded = entry.get("tree_sha256")
        if status == "installed":
            if verdict == "BLOCK":  # KB source turned dangerous
                _skill_rmtree(cache_path)  # owned by provenance -> remove
                skills[name] = _skill_entry(kdir, name, None, "blocked", verdict, findings)
                summary["blocked"] += 1
            elif tree_hash != recorded:  # KB source changed -> ALWAYS requarantine
                _skill_rmtree(cache_path)
                skills[name] = _skill_entry(kdir, name, tree_hash, "quarantined", verdict, findings)
                summary["quarantined"] += 1
            else:  # KB matches recorded -> cache-integrity check on-disk copy
                try:
                    on_disk = _skill_tree_sha256(cache_path)
                except _SkillHashError:
                    on_disk = None  # symlink/unhashable injected out-of-band -> divergent
                if on_disk == recorded and os.path.isdir(cache_path):
                    summary["unchanged"] += 1
                else:  # missing / divergent / unhashable -> drop owned copy + requarantine
                    _skill_rmtree(cache_path)
                    skills[name] = _skill_entry(kdir, name, tree_hash, "quarantined", verdict, findings)
                    summary["quarantined"] += 1
        elif status == "quarantined":
            if verdict == "BLOCK":
                skills[name] = _skill_entry(kdir, name, None, "blocked", verdict, findings)
                summary["blocked"] += 1
            elif tree_hash != recorded:
                skills[name] = _skill_entry(kdir, name, tree_hash, "quarantined", verdict, findings)
                summary["quarantined"] += 1
            else:
                summary["unchanged"] += 1
        elif status == "blocked":
            if verdict == "BLOCK":
                summary["unchanged"] += 1  # stay blocked, not re-prompted
            elif tree_hash != recorded:
                skills[name] = _skill_entry(kdir, name, tree_hash, "quarantined", verdict, findings)
                summary["quarantined"] += 1
            else:
                summary["unchanged"] += 1
        else:  # unknown/legacy status -> re-adjudicate as newly seen (totality)
            if verdict == "BLOCK":
                skills[name] = _skill_entry(kdir, name, None, "blocked", verdict, findings)
                summary["blocked"] += 1
            else:
                skills[name] = _skill_entry(kdir, name, tree_hash, "quarantined", verdict, findings)
                summary["quarantined"] += 1
    # Reverse-GC pass, PROVENANCE-scoped: a lockfile entry whose KB source is gone
    # is revoked; its OWNED (installed) cache dir is removed unconditionally. A
    # cache dir with NO lockfile entry is NEVER scanned/touched here (R2-1).
    for name in list(skills.keys()):
        if name not in kb:
            if skills[name].get("status") == "installed":
                gc_dir = skills[name].get("harness_dir") or cache_dir  # per-entry (S3)
                _skill_rmtree(os.path.join(gc_dir, name))
            del skills[name]
            summary["revoked"] += 1
    return summary


def _skill_install_snapshot(kdir, name, cache_dir, lock, force_conflict):
    """SNAPSHOT-BIND install (D7, R2-2): copy KB skills/<name> into ONE immutable
    temp snapshot (symlinks PRESERVED for vetting), vet + hash THAT snapshot, and
    os.replace THAT SAME snapshot into the cache — so vet, hash, and the installed
    bytes are provably identical even under a concurrent kb-git pull. Returns
    (ok, verdict, note). Ownership-by-provenance conflict handling (D12, R2-8)."""
    src = os.path.join(kdir, "skills", name)
    # ROOT-symlink guard (feature 260713-p4): `shutil.copytree(symlinks=True)`
    # preserves INNER symlinks but DEREFERENCES a symlinked ROOT `src`, which would
    # materialize the target's content into a symlink-free snapshot that then vets
    # PASS — the exact bypass reconcile blocks. Refuse a symlinked source root (and
    # any inner symlink) BEFORE copytree so --approve shares the boundary.
    link = _skill_tree_link(src)
    if link is not None:
        return False, "BLOCK", "BLOCK (refused): symlink present in source (%s)" % link
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        return False, None, "no such KB skill '%s'" % name
    os.makedirs(cache_dir, exist_ok=True)
    snap_parent = tempfile.mkdtemp(prefix=".skill-snap-", dir=cache_dir)
    snap = os.path.join(snap_parent, name)
    try:
        # symlinks=True PRESERVES inner symlinks so _skill_verdict can BLOCK them (a
        # dereferencing copy would hide a symlink by materializing its target). The
        # root was already refused above.
        shutil.copytree(src, snap, symlinks=True)
        # Post-copy re-check (S1 TOCTOU): the pre-copy guard reads the LIVE tree, so
        # a real-dir->symlink swap landing DURING copytree would be dereferenced into
        # a symlink-free snapshot. A single ff-pull leaves `src` a symlink afterward,
        # so re-checking the root here catches any swap that affected the copy — the
        # snapshot then provably binds a symlink-free source (no unlocked-pull race).
        link_after = _skill_tree_link(src)
        if link_after is not None:
            return False, "BLOCK", ("BLOCK (refused): source changed to a symlink "
                                    "during copy (%s)" % link_after)
        verdict, findings = _skill_verdict(snap)
        if verdict == "BLOCK":
            return False, verdict, "BLOCK (refused): %s" % "; ".join(findings)
        tree_hash = _skill_tree_sha256(snap)  # snapshot is symlink-free here
        dest = os.path.join(cache_dir, name)
        if os.path.lexists(dest):  # lexists so a (possibly broken) symlink dest is seen
            if _skill_owned(lock, name):
                _skill_rmtree(dest)  # ours by provenance -> remove + reinstall
            elif force_conflict:
                _skill_rmtree(dest)  # explicit foreign-dest override (R3-3)
            else:
                return False, verdict, ("CONFLICT: a skill dir already exists at %s "
                                        "that agentware did not install; remove it or "
                                        "re-run with --force-conflict" % dest)
        os.replace(snap, dest)  # atomic move of the SAME vetted snapshot bytes
        lock.setdefault("skills", {})[name] = {
            "tree_sha256": tree_hash,
            "status": "installed",
            "verdict": verdict,
            "authored_by": _skill_author(kdir, name),
            "vetted_at": utc_now_iso(),
            "source_rel": os.path.join("skills", name),
            "reason": ("REVIEW: unscannable/binary content" if verdict == "REVIEW" else None),
            # Per-entry install dir so reconcile/GC check the RIGHT cache even when
            # different skills were installed to different --harness-path targets (S3
            # multi-cache). Falls back to the pass/top-level dir when absent (legacy).
            "harness_dir": cache_dir,
        }
        # Also record the last-used dir at top level so the argless post-pull reconcile
        # defaults to it (S3 single-cache). Env/flag still override; per-entry wins for
        # the integrity/GC check.
        lock["harness_dir"] = cache_dir
        note = "installed" + (" (REVIEW: unscannable content approved)" if verdict == "REVIEW" else "")
        return True, verdict, note
    finally:
        _skill_rmtree(snap_parent)  # snap moved out on success; cleans temp either way


def _skill_status_line(lock):
    """Read-only, FIXED-FORMAT, sanitized session-start roster (installed +
    pending-approval). Prints NOTHING when empty (byte-identical no-op). Emits
    only counts + _skill_name_ok-validated names + the approve hint — NO author,
    NO reason, NO raw attacker metadata (R2-9)."""
    skills = (lock or {}).get("skills", {})
    installed = sorted(n for n, e in skills.items()
                       if e.get("status") == "installed" and _skill_name_ok(n))
    pending = sorted(n for n, e in skills.items()
                     if e.get("status") == "quarantined" and _skill_name_ok(n))
    if not installed and not pending:
        return 0  # power-user / empty -> print nothing
    lines = ["AGENTWARE_SKILLS: %d installed, %d pending-approval"
             % (len(installed), len(pending))]
    if installed:
        lines.append("  installed: " + ", ".join(installed))
    if pending:
        lines.append("  pending-approval: " + ", ".join(pending))
        lines.append("  approve with: scripts/agentware skill sync --approve <name>")
    print("\n".join(lines))
    return 0


def _skill_status(lock, fmt):
    """Full roster (installed / quarantined / blocked). Renders ONLY the enum
    status/verdict + the _skill_name_ok-validated name — never the attacker-
    controllable authored_by, reason, or findings. (authored_by is git %an<%ae>
    from an unsigned pull; even sanitized to a charset it can still form prose, so
    it is NOT rendered at ANY sink — matching --status-line. It stays in the
    lockfile as internal metadata only.)"""
    skills = (lock or {}).get("skills", {})
    buckets = {"installed": [], "quarantined": [], "blocked": []}
    for n in sorted(skills):
        if not _skill_name_ok(n):
            continue
        e = skills[n]
        st = e.get("status")
        row = {"name": n, "verdict": e.get("verdict")}
        buckets.setdefault(st, []).append(row)
    if fmt == "json":
        json.dump({"ok": True, "installed": buckets.get("installed", []),
                   "quarantined": buckets.get("quarantined", []),
                   "blocked": buckets.get("blocked", [])},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    total = sum(len(v) for v in buckets.values())
    if total == 0:
        print("No skills recorded in the execution-cache lockfile.")
        return 0
    for bucket in ("installed", "quarantined", "blocked"):
        rows = buckets.get(bucket, [])
        print("%s (%d):" % (bucket, len(rows)))
        for r in rows:
            print("  - %s [%s]" % (r["name"], r["verdict"] or "?"))
        if bucket == "quarantined" and rows:
            print("  approve with: scripts/agentware skill sync --approve <name>")
    return 0


def _skill_sync_reset(lock, cache_dir, fmt):
    """`--reset`: drop every OWNED cache copy and clear the lockfile. Ownership
    is by provenance, so only `installed` entries are removed — each from its OWN
    `harness_dir`, never the pass default, or a multi-cache install (S3) leaves a
    live copy behind an emptied lockfile that no longer knows where it is."""
    removed = []
    for name, e in list(lock.get("skills", {}).items()):
        edir = e.get("harness_dir") or cache_dir  # per-entry (S3)
        if e.get("status") == "installed" and _skill_rmtree(os.path.join(edir, name)):
            removed.append(name)
    save_skill_lock(_empty_skill_lock())
    if fmt == "json":
        json.dump({"ok": True, "reset": True, "removed": sorted(removed)},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("skill sync: reset — cleared lockfile + removed %d owned cache dir(s)%s"
              % (len(removed), (": " + ", ".join(sorted(removed))) if removed else ""))
    return 0


def _skill_sync_approve(args, kdir, lock, cache_dir, fmt):
    """`--approve`: THE ONLY INSTALL PATH (D1), over one comma-separated batch.

    Every name is attempted and its outcome recorded IN ORDER; rc is 1 if ANY
    failed. The batch does not stop at the first refusal — a partial approve
    whose shape the operator cannot see is worse than either extreme. Two
    refusals never reach the installer: an invalid name (never recorded raw) and
    a name shadowing a shipped PACKAGE skill."""
    names = [n.strip() for n in args.approve.split(",") if n.strip()]
    pkg = _package_skill_names()
    results = []
    rc = 0
    for name in names:
        if not _skill_name_ok(name):
            results.append((name, "refused", "invalid name")); rc = 1; continue
        if name in pkg and not getattr(args, "force_shadow", False):
            results.append((name, "refused",
                            "shadows a shipped package skill (use --force-shadow)"))
            rc = 1; continue
        ok, _verdict, note = _skill_install_snapshot(
            kdir, name, cache_dir, lock, getattr(args, "force_conflict", False))
        results.append((name, "installed" if ok else "refused", note))
        if not ok:
            rc = 1
    save_skill_lock(lock)
    if fmt == "json":
        json.dump({"ok": rc == 0, "results": [
            {"name": n, "outcome": o, "note": note} for (n, o, note) in results]},
            sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for n, o, note in results:
            print("%s: %s — %s" % (n, o.upper(), note))
    return rc


def _skill_sync_reject(name, lock, cache_dir):
    """`--reject`: record a skill BLOCKED so a later sync never re-prompts it. A
    copy this toolkit OWNS is removed; a FOREIGN dir at the same path is left
    alone and warned about, never deleted — removing someone else's skill because
    it shares a name is the destructive surprise that ends trust in a tool."""
    if not _skill_name_ok(name):
        sys.stderr.write("agentware: invalid skill name %r\n" % name)
        return 1
    entry0 = lock.get("skills", {}).get(name, {})
    cache_path = os.path.join(entry0.get("harness_dir") or cache_dir, name)
    removed = False
    conflict = False
    if _skill_owned(lock, name):
        removed = _skill_rmtree(cache_path)  # provenance -> remove owned copy
    elif os.path.lexists(cache_path):
        conflict = True  # foreign dir/symlink -> never delete, surface (lexists)
    entry = lock.setdefault("skills", {}).get(name, {})
    entry.update({"status": "blocked", "reason": "operator-rejected",
                  "source_rel": os.path.join("skills", name),
                  "vetted_at": utc_now_iso()})
    entry.setdefault("verdict", "BLOCK")
    lock["skills"][name] = entry
    save_skill_lock(lock)
    msg = "blocked '%s'%s" % (name, " + removed owned cache copy" if removed else "")
    if conflict:
        msg += " (WARNING: a foreign dir at %s was NOT removed)" % cache_path
    print("skill sync: " + msg)
    return 0


def _skill_sync_uninstall(name, lock, cache_dir):
    """`--uninstall`: remove an owned copy AND forget the entry entirely. Unlike
    `--reject`, which REMEMBERS the decision, an uninstalled skill is unknown
    again, so the next reconcile re-quarantines it as newly seen."""
    if not _skill_name_ok(name):
        sys.stderr.write("agentware: invalid skill name %r\n" % name)
        return 1
    udir = lock.get("skills", {}).get(name, {}).get("harness_dir") or cache_dir
    removed = _skill_rmtree(os.path.join(udir, name)) if _skill_owned(lock, name) else False
    existed = name in lock.get("skills", {})
    lock.get("skills", {}).pop(name, None)
    save_skill_lock(lock)
    print("skill sync: uninstalled '%s'%s%s" % (
        name, " (removed cache copy)" if removed else "",
        "" if existed else " (no lockfile entry)"))
    return 0


def _skill_sync_reconcile_emit(kdir, lock, cache_dir, fmt):
    """The BARE reconcile (also the pull-trigger path): NEVER installs (D1)."""
    summary = _skill_reconcile(kdir, lock, cache_dir)
    save_skill_lock(lock)
    if fmt == "json":
        json.dump({"ok": True, "summary": summary}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("skill sync: %s" % ", ".join("%s=%d" % (k, summary[k])
              for k in ("installed", "quarantined", "blocked", "unchanged",
                        "revoked", "rejected_name")))
    return 0


def cmd_skill_sync(args):
    """Reconcile the canonical KB skills/ against the per-machine vetted execution
    cache. The BARE reconcile NEVER installs (D1); explicit --approve is the ONLY
    install path. Read-only status modes are lock-free + fast (used by the hook).
    The mode order is a CHAIN, not a set: the two roster modes answer BEFORE a
    knowledge dir is required (the hook calls them on machines that have none),
    and `--reset` answers before the three per-name modes, so a reset is never
    half-applied by an approve that ran first."""
    fmt = getattr(args, "format", "text")
    if getattr(args, "status_line", False):
        return _skill_status_line(load_skill_lock())
    if getattr(args, "status", False):
        return _skill_status(load_skill_lock(), fmt)
    kdir = require_knowledge_dir()
    if not kdir:
        return 1
    with _skill_sync_lock():
        lock = load_skill_lock()
        # Resolve the cache dir with the lock so a flag-less run reconciles the
        # SAME cache a prior flag-based --approve recorded (S3).
        cache_dir = _skill_cache_dir(args, lock)
        if getattr(args, "reset", False):
            return _skill_sync_reset(lock, cache_dir, fmt)
        if getattr(args, "approve", None):
            return _skill_sync_approve(args, kdir, lock, cache_dir, fmt)
        reject = getattr(args, "reject", None)
        if reject:
            return _skill_sync_reject(reject.strip(), lock, cache_dir)
        uninstall = getattr(args, "uninstall", None)
        if uninstall:
            return _skill_sync_uninstall(uninstall.strip(), lock, cache_dir)
        return _skill_sync_reconcile_emit(kdir, lock, cache_dir, fmt)


_EXPORTS = [
    "_skill_kb_names",
    "_package_skill_names",
    "_skill_sync_lock",
    "_skill_verdict",
    "_skill_reconcile",
    "_skill_install_snapshot",
    "_skill_status",
    "_skill_status_line",
    "_skill_sync_reset",
    "_skill_sync_approve",
    "_skill_sync_reject",
    "_skill_sync_uninstall",
    "_skill_sync_reconcile_emit",
    "cmd_skill_sync",
]

__all__ = list(_EXPORTS)
