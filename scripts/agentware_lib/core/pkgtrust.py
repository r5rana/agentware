"""agentware_lib.core.pkgtrust -- the publisher signing-key TRUST ANCHOR (layer: core).

Owns: the HOME-pinned publisher SSH signing key and everything needed to reason
about it -- the default signer identity (PKG_PUBLISHER_PRINCIPAL), the accepted
key types (_SSH_KEYTYPES), the anchor path (_pkg_allowed_signers_path), the
fail-closed security assertion applied before the anchor is consulted
(_pkg_assert_signers_secure), the fingerprint helper (_pkg_ssh_key_fingerprint),
the multi-key file parser and lister (_pkg_parse_signers / _pkg_list_signers),
the append-with-overlap pinner (_pkg_pin_publisher_key), the in-tree key locator
(_pkg_intree_signing_key_path), and the two report builders the CLI and checkup
share (_pkg_pubkey_records / _pkg_trust_fingerprint_warnings).

WHY THE ANCHOR LIVES OUTSIDE THE TREE
The trust root is `~/.agentware/allowed_signers`, NOT a file in the repository,
because the updater REWRITES the repository. An in-tree trust root would let a
malicious push rotate the very key it is then verified against -- the update
would authenticate itself. Keeping the root in HOME means an attacker who can
write the tree still cannot move the anchor.

It is a MULTI-KEY file, honouring SSH `valid-after` / `valid-before`, so a new
publisher key can be trusted during an overlap window before the old one
retires. Without that overlap a key rotation strands every fleet member that has
not yet updated -- they would reject the release that carries the new key.

Verification is plain git/SSH via `gpg.ssh.allowedSignersFile`: NO keyring, NO
network, NO `gh`. That is what keeps `trust verify` runnable on an air-gapped or
offline machine and free of any third-party trust store.

Does NOT own: the filesystem security primitives it asserts through (_pkg_home_dir
/ _pkg_assert_secure_path / _pkg_secure_atomic_write / ALLOWED_SIGNERS_NAME) ->
agentware_lib.core.pkghome; the `trust` CLI handler and registrar (list/pin/
rotate/verify) -> agentware_lib.features.trustcmd, because core holds NO command
handlers anywhere in this tree; the signed SemVer-tag update channel that CONSUMES
this anchor (task 41) and the release preflight (task 42).

Layer deps: core -> core only (agentware_lib.core.pkghome for the secure-home
primitives, agentware_lib.core.paths for paths.REPO_ROOT) plus stdlib os/re/subprocess.
Nothing above core is imported, so no cycle is reachable.
Fan-in: 1 at landing (features.trustcmd); grows with tasks 41-42 (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all
(`learn-a-pure-move-of-security-code-needs-byte-comparison-not-review`).
"""

import base64
import binascii
import hashlib
import os
import re
import subprocess

import agentware_lib.core.paths as paths
from agentware_lib.core.pkghome import (
    ALLOWED_SIGNERS_NAME,
    _PkgStateError,
    _pkg_assert_secure_path,
    _pkg_home_dir,
    _pkg_secure_atomic_write,
    _pkg_secure_home,
)


# --- Task 2: trust anchor (HOME-pinned publisher SSH signing key) ------------
# The trust root lives OUTSIDE the tree the updater rewrites (~/.agentware/
# allowed_signers), so a malicious push can never rotate the key it is checked
# against. Managed as a MULTI-KEY file (append/list, honoring SSH valid-after/
# valid-before) so a new publisher key is trusted during an overlap window before
# the old one retires — closing the fleet-stranding case. Verification uses plain
# git/SSH (`gpg.ssh.allowedSignersFile`), NO keyring, NO network, NO gh.
PKG_PUBLISHER_PRINCIPAL = "agentware-release"   # default signer identity (tagger)


_SSH_KEYTYPES = (
    "ssh-ed25519", "ssh-rsa", "ssh-dss",
    "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com",
)


def _pkg_allowed_signers_path():
    return os.path.join(_pkg_home_dir(), ALLOWED_SIGNERS_NAME)


def _pkg_assert_signers_secure():
    """lstat the allowed_signers trust anchor and REFUSE fail-closed on a
    symlink, wrong-owner, or group/world-writable file BEFORE it is consulted for
    verification (a tampered trust root must never be trusted). Returns the path
    (whether or not it exists yet)."""
    path = _pkg_allowed_signers_path()
    if os.path.lexists(path):
        _pkg_assert_secure_path(path, is_dir=False)
    return path


def _pkg_ssh_key_fingerprint(keydata):
    """OpenSSH SHA256 fingerprint ('SHA256:<b64-no-pad>') of a base64 key blob,
    computed in pure stdlib (matches `ssh-keygen -lf`). None on a non-base64 blob."""
    try:
        raw = base64.b64decode(keydata, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not raw:
        return None
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _pkg_parse_signers(text):
    """Parse allowed_signers content into records
    {principal, options, keytype, keydata, comment, fingerprint, raw}. Skips
    blank/comment/malformed lines. Robust to optional valid-after/valid-before
    options by locating the keytype token."""
    records = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        toks = s.split()
        kt_idx = next((i for i, t in enumerate(toks) if t in _SSH_KEYTYPES), None)
        if kt_idx is None or kt_idx == 0 or kt_idx + 1 >= len(toks):
            continue  # malformed / no key — skip (never trust an unparseable line)
        records.append({
            "principal": toks[0],
            "options": toks[1:kt_idx],
            "keytype": toks[kt_idx],
            "keydata": toks[kt_idx + 1],
            "comment": " ".join(toks[kt_idx + 2:]),
            "fingerprint": _pkg_ssh_key_fingerprint(toks[kt_idx + 1]),
            "raw": s,
        })
    return records


def _pkg_list_signers():
    """Parsed records of the HOME-pinned trust anchor ([] if unpinned).
    Fail-closed on a tampered file."""
    path = _pkg_assert_signers_secure()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _pkg_parse_signers(f.read())
    except OSError:
        return []


def _pkg_pin_publisher_key(principal, keytype, keydata, valid_after=None,
                           valid_before=None, comment=None):
    """Append a publisher signer to ~/.agentware/allowed_signers (multi-key,
    honoring valid-after/valid-before overlap windows). Idempotent: dedup by
    keydata. Returns (fingerprint, added:bool). Fail-closed on a tampered anchor
    or unwritable HOME (raises)."""
    if keytype not in _SSH_KEYTYPES:
        raise _PkgStateError("unsupported SSH key type %r" % keytype)
    if _pkg_ssh_key_fingerprint(keydata) is None:
        raise _PkgStateError("key data is not valid base64 — refusing to pin")
    _pkg_secure_home()
    path = _pkg_assert_signers_secure()
    existing = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
    for rec in _pkg_parse_signers(existing):
        if rec["keydata"] == keydata:
            return rec["fingerprint"], False   # already pinned (idempotent)
    opts = []
    if valid_after:
        opts.append('valid-after="%s"' % valid_after)
    if valid_before:
        opts.append('valid-before="%s"' % valid_before)
    parts = [principal] + opts + [keytype, keydata]
    if comment:
        parts.append(comment)
    line = " ".join(parts)
    text = existing
    if text and not text.endswith("\n"):
        text += "\n"
    text += line + "\n"
    _pkg_secure_atomic_write(path, text, mode=0o600)
    return _pkg_ssh_key_fingerprint(keydata), True


def _pkg_intree_signing_key_path():
    """Conventional in-repo publisher pubkey copy (committed for discovery), or
    None. There is no in-tree key until the publisher ships one (go-live)."""
    for rel in ("catalog/agentware-release.pub", ".agentware-release.pub"):
        p = os.path.join(paths.REPO_ROOT, rel)
        if os.path.isfile(p):
            return p
    return None


def _pkg_pubkey_records(path):
    """Parse an OpenSSH pubkey file ('keytype keydata [comment]' per line) into
    (keytype, keydata, fingerprint) tuples."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return out
    for line in text.splitlines():
        toks = line.split()
        if len(toks) >= 2 and toks[0] in _SSH_KEYTYPES:
            fp = _pkg_ssh_key_fingerprint(toks[1])
            if fp:
                out.append((toks[0], toks[1], fp))
    return out


def _pkg_trust_fingerprint_warnings():
    """Loud warnings when an in-tree publisher key copy differs from the
    HOME-pinned trust anchor: a rotation MUST be operator-confirmed via
    `scripts/agentware trust rotate`, never silently auto-trusted from a push."""
    intree = _pkg_intree_signing_key_path()
    if not intree:
        return []
    intree_fps = {fp for _kt, _kd, fp in _pkg_pubkey_records(intree)}
    if not intree_fps:
        return []
    pinned = {r["fingerprint"] for r in _pkg_list_signers() if r.get("fingerprint")}
    if not pinned:
        return []   # nothing pinned yet — onboarding/go-live performs the first pin
    warnings = []
    for fp in sorted(intree_fps - pinned):
        warnings.append(
            "in-tree signing key %s is NOT in the HOME-pinned trust anchor — a key "
            "rotation must be operator-confirmed via `scripts/agentware trust "
            "rotate` (refusing to auto-trust a pushed key)" % fp)
    return warnings


_EXPORTS = [
    "PKG_PUBLISHER_PRINCIPAL",
    "_SSH_KEYTYPES",
    "_pkg_allowed_signers_path",
    "_pkg_assert_signers_secure",
    "_pkg_ssh_key_fingerprint",
    "_pkg_parse_signers",
    "_pkg_list_signers",
    "_pkg_pin_publisher_key",
    "_pkg_intree_signing_key_path",
    "_pkg_pubkey_records",
    "_pkg_trust_fingerprint_warnings",
]

__all__ = list(_EXPORTS)
