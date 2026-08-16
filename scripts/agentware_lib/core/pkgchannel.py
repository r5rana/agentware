"""agentware_lib.core.pkgchannel -- SIGNED SemVer-tag resolution (layer: core).

Owns: the release CHANNEL -- what counts as a version tag (PKG_CHANNEL_RE), how
versions order (_pkg_version_tuple / _pkg_ver_str), which tags the channel offers
(_pkg_channel_tags), signature verification of a tag against the HOME-pinned
anchor (_pkg_verify_tag), tag->sha resolution (_pkg_tag_sha) and the single entry
point that answers "what is the newest tag I may move to, and is it trustworthy?"
(_pkg_resolve_verified_tag), plus the fetch timeout that bounds all of it.

WHY SIGNED TAGS AND NOT A PLAIN FETCH
This REPLACES the donor's plain-fetch updater (recorded as a superseded parity
row, task 41). A plain fetch trusts whatever the remote serves: anyone who can
write the remote, or intercept the transport, chooses the code that runs on the
operator's machine on the next tick. Here every candidate tag must carry a
signature verified against `~/.agentware/allowed_signers` -- an anchor deliberately
outside the tree the updater rewrites -- so a compromised remote cannot supply both
the payload and the key that blesses it.

Resolution is CONSERVATIVE by construction: only tags matching the channel pattern
are considered, they are ordered by parsed SemVer rather than by string or by
commit date (so `v1.10.0` beats `v1.9.0`, and a back-dated tag cannot jump the
queue), quarantined tags are skipped, and an unverifiable tag is not "skipped with
a warning" -- it stops the resolution.

Does NOT own: the anchor itself -> core.pkgtrust; state and quarantine ->
core.pkgstate; APPLYING a resolved tag (fetch/checkout/smoke/rollback) ->
features.pkgapply; deciding WHEN to look -> features.pkgupdater.
Layer deps: core -> core only (core.gitio, core.pkgstate, core.pkgtrust, core.paths).
Fan-in: 2 at landing (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import os
import re
import subprocess

from agentware_lib.core.dates import _now_iso
from agentware_lib.core.gitio import _git, _git_noninteractive_env, _git_timed
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.core.pkghome import _PkgStateError
from agentware_lib.core.pkgstate import (
    _pkg_is_quarantined,
    _pkg_quarantine_tag,
    _pkg_state_read,
    _pkg_state_update,
)
from agentware_lib.core.pkgtrust import (
    _pkg_allowed_signers_path,
    _pkg_assert_signers_secure,
    _pkg_list_signers,
)


# Signed SemVer STABLE channel: vMAJOR.MINOR.PATCH only (no pre-release/build).
PKG_CHANNEL_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


PKG_FETCH_TIMEOUT = 120       # unattended tick fetch bound; rc-124 => graceful skip (no apply)


def _pkg_version_tuple(s):
    """(MAJOR, MINOR, PATCH) ints from 'vX.Y.Z' or 'X.Y.Z', else None. Gives the
    TOTAL ORDER the no-downgrade guard needs."""
    if not s:
        return None
    t = s if s.startswith("v") else "v" + s
    m = PKG_CHANNEL_RE.match(t.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _pkg_ver_str(ver):
    return ".".join(str(x) for x in ver) if ver else None


def _pkg_channel_tags(target):
    """Stable SemVer channel tags (vX.Y.Z) present in `target`, newest-version
    first. Sorted by our OWN version tuple (not git's -v:refname) for a
    deterministic total order regardless of git version."""
    cp = _git(target, "tag", "--list")
    if cp.returncode != 0:
        return []
    tags = [t.strip() for t in cp.stdout.splitlines()
            if PKG_CHANNEL_RE.match(t.strip())]
    return sorted(tags, key=_pkg_version_tuple, reverse=True)


def _pkg_verify_tag(target, tag, signers_path):
    """Verify `tag`'s signature against the HOME-pinned allowed_signers using
    plain git/SSH (`gpg.format=ssh` injected per-invocation — never mutates repo
    config). Returns (ok, detail). FAIL-CLOSED: an unsigned, tampered, or
    unknown-signer tag (any non-zero `git tag -v`) is ok=False."""
    if not signers_path or not os.path.isfile(signers_path):
        return False, "no HOME-pinned allowed_signers — cannot verify (fail-closed)"
    cp = _git(target, "-c", "gpg.format=ssh",
              "-c", "gpg.ssh.allowedSignersFile=%s" % signers_path,
              "tag", "-v", tag)
    if cp.returncode == 0:
        return True, "verified signed tag"
    detail = ((cp.stderr or "") + (cp.stdout or "")).strip().splitlines()
    return False, (detail[-1] if detail else "signature verification failed")


def _pkg_tag_sha(target, tag):
    """Resolve a tag NAME to its concrete commit SHA (dereferencing an annotated
    tag to its commit). Called ONCE per resolution so verify and apply pin the
    exact same object (TOCTOU-safe)."""
    cp = _git(target, "rev-list", "-n", "1", tag)
    if cp.returncode == 0 and cp.stdout.strip():
        return cp.stdout.strip().splitlines()[0].strip()
    return None


def _resolve_precondition(target, now_iso, result):
    """Trust-anchor guard + tag fetch. True when resolution must STOP.

    Both halves fail CLOSED and write `result` in place: an absent or tampered
    anchor means nothing can be verified, and a failed fetch means the candidate
    set is unknown rather than empty. Neither is allowed to read as "no update".
    `last_check`/`last_fetch` are recorded on every ATTEMPT (including a timeout)
    so a slept or offline machine self-corrects and the layer-2 staleness pre-hook
    does not hammer the remote.
    """
    try:
        _pkg_assert_signers_secure()
    except _PkgStateError as exc:
        _pkg_state_update(last_check_ts=now_iso)
        result.update(status="no-trust", detail=str(exc))
        return True
    if not _pkg_list_signers():
        _pkg_state_update(last_check_ts=now_iso)
        result.update(status="no-trust",
                      detail="no publisher key pinned (~/.agentware/allowed_signers)"
                             " — updater fail-closed until go-live")
        return True
    # Read-only fetch of tags through the timeout-bounded runner. NEVER touches
    # the work tree. rc-124 = graceful skip.
    env = _git_noninteractive_env()
    fetch = _git_timed(target, "fetch", "--tags", "--quiet",
                       env=env, timeout=PKG_FETCH_TIMEOUT)
    _pkg_state_update(last_check_ts=now_iso, last_fetch_ts=now_iso)
    if fetch.returncode == 124:
        result.update(status="skipped",
                      detail="fetch --tags timed out (%ds) — graceful skip"
                             % PKG_FETCH_TIMEOUT)
        return True
    if fetch.returncode != 0:
        first = ((fetch.stderr or "") + (fetch.stdout or "")).strip().splitlines()
        result.update(status="skipped",
                      detail="fetch --tags failed (offline?): %s"
                             % (first[0] if first else "unknown"))
        return True
    return False


def _resolve_candidate(target, tag, signers, result):
    """Vet ONE candidate tag; its commit SHA when acceptable, else None.

    An unverifiable tag that is NEWER than what is applied is an anomaly, not a
    silent skip: it emits a loud warning and is quarantined so the next tick does
    not re-litigate it, and resolution falls through to the next candidate rather
    than freezing delivery behind one stray tag.
    """
    if _pkg_is_quarantined(tag):
        return None   # a previously smoke-failed / unverifiable tag (anti-thrash)
    ok, detail = _pkg_verify_tag(target, tag, signers)
    if not ok:
        result["warnings"].append(
            "anomalous newer tag %s is UNVERIFIABLE (%s) — refusing "
            "fail-closed and quarantining; falling back to the next "
            "verified tag" % (tag, detail))
        _pkg_quarantine_tag(tag, "unverifiable: %s" % detail)
        return None
    sha = _pkg_tag_sha(target, tag)
    if not sha:
        result["warnings"].append("verified %s but could not resolve its SHA "
                                  "— skipping" % tag)
        return None
    return sha


def _pkg_resolve_verified_tag(target=None, now_iso=None):
    """Read-only fetch + resolve the newest VERIFIED, no-downgrade signed tag to a
    concrete SHA. Executes NO pulled code. Records last_check_ts + last_fetch_ts.

    Returns a dict {status, tag, sha, version, warnings, detail} where status is:
      resolved   — a verified tag NEWER than last_applied_version; {tag,sha,version} set
      up-to-date — the newest verified tag is not newer than last_applied
      none       — no channel tags, or no verifiable tag and never applied
      skipped    — fetch timed out / offline (graceful; no apply, no rollback)
      no-trust   — trust anchor absent/tampered — fail-closed, cannot verify

    Audit-hardening: iterates version-sorted candidates newest->down and picks the
    FIRST that BOTH verifies AND satisfies no-downgrade, so one stray/unsigned
    higher tag cannot freeze delivery; a newer-than-applied tag that fails
    verification emits a LOUD anomaly warning and is quarantined (bounded)."""
    target = target or REPO_ROOT
    now_iso = now_iso or _now_iso()
    result = {"status": "none", "tag": None, "sha": None, "version": None,
              "warnings": [], "detail": ""}

    signers = _pkg_allowed_signers_path()
    blocked = _resolve_precondition(target, now_iso, result)
    if blocked:
        return result

    tags = _pkg_channel_tags(target)
    if not tags:
        result.update(status="none", detail="no signed SemVer channel tags present")
        return result

    applied = _pkg_version_tuple(_pkg_state_read().get("last_applied_version"))
    for tag in tags:
        ver = _pkg_version_tuple(tag)
        # No-downgrade: tags are sorted DESC, so the first tag <= applied means
        # nothing newer remains — refuse (defends anti-rewind / re-served old CVE).
        if applied is not None and ver <= applied:
            result.update(status="up-to-date",
                          detail="no verified tag newer than applied %s"
                                 % _pkg_ver_str(applied))
            return result
        sha = _resolve_candidate(target, tag, signers, result)
        if sha:
            result.update(status="resolved", tag=tag, sha=sha,
                          version=_pkg_ver_str(ver))
            return result

    # Exhausted with no verifiable newer tag.
    if applied is not None:
        result.update(status="up-to-date",
                      detail="no verifiable tag newer than applied %s"
                             % _pkg_ver_str(applied))
    else:
        result.update(status="none",
                      detail="no verifiable signed tag in the channel (fail-closed)")
    return result


_EXPORTS = [
    "PKG_CHANNEL_RE",
    "PKG_FETCH_TIMEOUT",
    "_pkg_version_tuple",
    "_pkg_ver_str",
    "_pkg_channel_tags",
    "_pkg_verify_tag",
    "_pkg_tag_sha",
    "_pkg_resolve_verified_tag",
]

__all__ = list(_EXPORTS)
