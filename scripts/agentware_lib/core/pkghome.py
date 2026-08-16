"""agentware_lib.core.pkghome -- the secure package HOME primitives (layer: core).

Owns: the machine-local `~/.agentware` state directory and the fail-closed
filesystem rules every package-trust consumer depends on -- the directory
resolver (_pkg_home_dir), the ownership/permission assertion applied BEFORE any
trusted file is read (_pkg_assert_secure_path), the secured-home constructor
(_pkg_secure_home), the atomic writer that never widens a mode
(_pkg_secure_atomic_write), the trust-anchor filename (ALLOWED_SIGNERS_NAME) and
the error type they raise (_PkgStateError).

Why this is CORE, and separate from the anchor that uses it: these rules are
about the FILESYSTEM, not about signing. "Is this path owned by me, not a
symlink, not group-writable?" is the same question whether the file being
protected is a trust anchor, an update-channel state file or a release
preflight record -- tasks 40, 41 and 42 respectively, all of which consume this
module. Keeping it separate is what stops each of those from re-deriving its own
notion of "secure enough".

The trust root deliberately lives OUTSIDE the tree the updater rewrites, so a
malicious push can never rotate the key it is checked against. That property is
only as strong as these checks: a symlinked or group-writable `~/.agentware` is
an anchor an attacker can swap, which is why every reader asserts BEFORE it
trusts, and refuses rather than repairing.

Does NOT own: the publisher signing-key anchor built on top of it (key parsing,
fingerprints, pin/list) -> agentware_lib.core.pkgtrust; the `trust` CLI ->
agentware_lib.features.trustcmd; the update channel (task 41) and the release
preflight (task 42), which consume this module rather than extend it.

Layer deps: core -> core only (agentware_lib.core.config, reached by ATTRIBUTE
so the suite's HOME_CONFIG patch is seen) plus
stdlib os/stat/tempfile. Nothing above core is imported.
Fan-in: 0 at landing; grows to 2 with tasks 41-42 (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all
(`learn-a-pure-move-of-security-code-needs-byte-comparison-not-review`).
"""

import os
import stat
import tempfile

import agentware_lib.core.config as config


ALLOWED_SIGNERS_NAME = "allowed_signers"


class _PkgStateError(Exception):
    """Raised for a fail-closed security condition on the HOME state/trust files
    (symlink, wrong-owner, group/world-writable) OR an un-surfaceable write
    failure. Callers MUST treat this as 'do not apply' — never fall through."""


def _pkg_home_dir():
    """The operator's ~/.agentware directory (derived from HOME_CONFIG so tests
    that patch HOME_CONFIG get a fully isolated state/trust root)."""
    # LATE-BOUND through the config aggregator, never `from ... import
    # HOME_CONFIG`: configkeys.py declares HOME_CONFIG one of the two mutable
    # globals the suite monkeypatches, and a by-value import would snapshot it
    # at import time so every patched test would silently read the REAL home.
    return os.path.dirname(config.HOME_CONFIG)


def _pkg_assert_secure_path(path, is_dir):
    """Fail-closed integrity guard: lstat `path` and REFUSE (raise _PkgStateError)
    if it is a symlink, the wrong type, not owned by this euid, or group/world-
    writable. Used before consulting or overwriting any HOME state/trust file so a
    malicious local actor cannot redirect or tamper with the trust root."""
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise _PkgStateError("%s is a symlink — refusing (fail-closed)" % path)
    if is_dir and not stat.S_ISDIR(st.st_mode):
        raise _PkgStateError("%s is not a directory — refusing" % path)
    if not is_dir and not stat.S_ISREG(st.st_mode):
        raise _PkgStateError("%s is not a regular file — refusing" % path)
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and st.st_uid != geteuid():
        raise _PkgStateError(
            "%s owned by uid %d, not %d — refusing (fail-closed)"
            % (path, st.st_uid, geteuid()))
    if st.st_mode & 0o022:
        raise _PkgStateError("%s is group/world-writable — refusing (fail-closed)"
                             % path)


def _pkg_secure_home():
    """Ensure ~/.agentware exists as a 0700 directory owned by this euid and is
    not a symlink. Raises _PkgStateError on a tampered/symlinked directory."""
    d = _pkg_home_dir()
    if os.path.lexists(d):
        _pkg_assert_secure_path(d, is_dir=True)
    else:
        os.makedirs(d, mode=0o700, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass


def _pkg_secure_atomic_write(path, text, mode=0o600):
    """Atomic, symlink-safe write of `text` to a HOME state/trust file at `mode`.

    Refuses to overwrite a tampered/symlinked target (fail-closed), writes via an
    O_EXCL+O_NOFOLLOW temp then `os.replace`, and SURFACES every failure by
    re-raising so a full/read-only HOME can never silently drop a write."""
    if os.path.lexists(path):
        _pkg_assert_secure_path(path, is_dir=False)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(tmp, flags, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


_EXPORTS = [
    "ALLOWED_SIGNERS_NAME",
    "_PkgStateError",
    "_pkg_home_dir",
    "_pkg_assert_secure_path",
    "_pkg_secure_home",
    "_pkg_secure_atomic_write",
]

__all__ = list(_EXPORTS)
