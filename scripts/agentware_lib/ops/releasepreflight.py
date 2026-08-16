"""agentware_lib.ops.releasepreflight -- go-live READINESS for the signed channel (layer: ops).

Owns: the read-only `release preflight` check -- the hermetic verify SELF-TEST
(`_pkg_preflight_selftest`), the publisher/client readiness report
(`_pkg_release_preflight`), the `cmd_release` handler and the `release` registrar.

WHY A SELF-TEST AND NOT JUST A REPORT
The preflight's job is to answer "if a signed release landed right now, would this
machine correctly ACCEPT a good one and REFUSE a bad one?". Reporting configuration
cannot answer that: a machine can have a pinned key, a reachable remote and a
plausible-looking git config while `gpg.ssh.allowedSignersFile` is wired to nothing,
in which case verification silently passes everything. So the self-test builds a
throwaway repo, signs a tag with a scratch key, and proves BOTH directions -- a
valid signature verifies, and a tampered one is refused -- before reporting on the
real machine.

Hermetic and READ-ONLY by construction: everything happens in a temp tree with its
own scratch key, so a preflight can be run on a production machine at any time
without touching the operator's anchor, the package tree or the knowledge base.
`release` is deliberately a preflight-only verb here: key and tag ACTIONS stay
operator-gated through docs/release-signing.md rather than becoming CLI surface.

Does NOT own: the trust anchor itself -> core.pkgtrust; the update channel that
consumes a release -> core.pkgchannel + features.pkgapply; the package-level
release GATE (`gate release`, a different verb) -> ops.releasegate.
Layer deps: ops -> core (gitio, paths, pkgchannel, pkgtrust, pkghome) plus
stdlib. All DOWNWARD.
Fan-in: 0 (see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import agentware_lib.core.gitio as gitio
import agentware_lib.core.paths as paths
from agentware_lib.core.gitio import _git
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.core.pkgchannel import (
    PKG_CHANNEL_RE,
    _pkg_tag_sha,
    _pkg_verify_tag,
)
from agentware_lib.core.pkghome import _PkgStateError
from agentware_lib.core.pkgtrust import _pkg_list_signers


def register_release(sub, fmt_parent):
    """Declare the `release` verb. Lifted verbatim from the monolith's build_parser."""
    p_release = sub.add_parser(
        "release", parents=[fmt_parent],
        help="release-signing utilities (read-only preflight; key/tag actions are "
             "operator-gated via docs/release-signing.md)")
    release_sub = p_release.add_subparsers(dest="release_command",
                                           metavar="<subcommand>")
    p_pre = release_sub.add_parser(
        "preflight", parents=[fmt_parent],
        help="read-only go-live readiness check (hermetic verify self-test + "
             "publisher/client readiness)")
    p_pre.set_defaults(func=cmd_release)


def _pkg_preflight_selftest():
    """Hermetic end-to-end machinery test with a THROWAWAY key: temp repo -> SSH-sign
    a v-tag -> verify against a temp allowed_signers -> resolve name->SHA. Proves the
    sign->pin->verify->resolve path works on THIS machine's git/ssh. NEVER touches a
    real key/tag/remote. Returns (ok, detail)."""
    if not shutil.which("ssh-keygen"):
        return False, "ssh-keygen not available — cannot verify SSH-signed tags"
    tmp = tempfile.mkdtemp(prefix="agentware-preflight-")
    try:
        key = os.path.join(tmp, "k")
        if subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", key,
                           "-C", "preflight@test", "-q"], check=False).returncode != 0:
            return False, "throwaway key generation failed"
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)

        def g(*a):
            return subprocess.run(["git", "-C", repo] + list(a), check=False,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "preflight@test")
        g("config", "user.name", "Preflight")
        g("config", "gpg.format", "ssh")
        g("config", "user.signingkey", key + ".pub")
        with open(os.path.join(repo, "f"), "w") as f:
            f.write("x")
        # DELIBERATELY a blanket add, and the only one left in the lib. `repo` is
        # a throwaway git repo this function just created in a tempdir, holding
        # one file named "f", purely to find out whether this git can sign a tag.
        # It is not a knowledge base: there is no team to scope to, no derived set
        # to drop and no entry the allowlist could recognise, so
        # `kb_git_stage_scoped` would correctly stage NOTHING and the probe would
        # report "signing unsupported" on every machine. The allowlist guards KB
        # writes; this is a capability probe over a synthetic repo that is deleted
        # a few lines later.
        g("add", "-A")
        g("commit", "-qm", "c")
        tagr = g("tag", "-s", "-m", "preflight v2.0.0", "v2.0.0")
        if tagr.returncode != 0:
            return False, ("SSH tag signing unsupported by this git: %s"
                           % (tagr.stderr or "").strip()[:120])
        with open(key + ".pub") as f:
            keytype, keydata = f.read().split()[:2]
        signers = os.path.join(tmp, "allowed_signers")
        with open(signers, "w") as f:
            f.write("preflight@test %s %s\n" % (keytype, keydata))
        if not PKG_CHANNEL_RE.match("v2.0.0"):
            return False, "channel regex rejected v2.0.0"
        ok, detail = _pkg_verify_tag(repo, "v2.0.0", signers)
        if not ok:
            return False, "hermetic verify failed: %s" % detail
        if not _pkg_tag_sha(repo, "v2.0.0"):
            return False, "hermetic name->SHA resolution failed"
        return True, "sign->pin->verify->resolve OK (throwaway key)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _pkg_release_preflight():
    """READ-ONLY go-live readiness. Returns (machinery_ok, report). Gates exit 0 on
    the HERMETIC machinery self-test; also reports publisher-signing-config and
    client-trust-anchor readiness for the operator to complete the gated steps."""
    report = {"checks": []}

    def add(name, ok, detail):
        report["checks"].append({"name": name, "ok": ok, "detail": detail})

    fmt = _git(REPO_ROOT, "config", "--get", "gpg.format").stdout.strip()
    sign = _git(REPO_ROOT, "config", "--get", "tag.gpgsign").stdout.strip()
    skey = _git(REPO_ROOT, "config", "--get", "user.signingkey").stdout.strip()
    pub_ready = fmt == "ssh" and sign in ("true", "1") and bool(skey)
    add("publisher-signing-config", pub_ready,
        "gpg.format=%s tag.gpgsign=%s signingkey=%s"
        % (fmt or "unset", sign or "unset", "set" if skey else "unset"))

    try:
        pinned = _pkg_list_signers()
        anchor_ok = bool(pinned)
        add("client-trust-anchor", anchor_ok,
            ("%d publisher key(s) pinned" % len(pinned)) if pinned
            else "no key pinned (pin at onboarding / go-live)")
    except _PkgStateError as exc:
        anchor_ok = False
        add("client-trust-anchor", False, "tampered: %s" % exc)

    machinery_ok, detail = _pkg_preflight_selftest()
    add("hermetic-verify-selftest", machinery_ok, detail)

    report["ok"] = machinery_ok
    report["publisher_ready"] = pub_ready
    report["client_ready"] = anchor_ok
    report["go_live_ready"] = machinery_ok and pub_ready and anchor_ok
    return machinery_ok, report


def cmd_release(args):
    """Release-signing utilities. `release preflight` is a READ-ONLY go-live
    readiness check. The sensitive production actions (generating the real signing
    key, cutting+pushing the first signed tag) are OPERATOR-GATED (R-GIT-01) and
    performed via docs/release-signing.md — NEVER by this command."""
    action = getattr(args, "release_command", None) or "preflight"
    as_json = getattr(args, "format", "text") == "json"
    if action == "preflight":
        ok, report = _pkg_release_preflight()
        if as_json:
            json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("release preflight: %s" % ("READY" if ok else "NOT READY"))
            for c in report["checks"]:
                print("  [%s] %s — %s"
                      % ("ok" if c["ok"] else "x", c["name"], c["detail"]))
            print("  publisher_ready=%s client_ready=%s go_live_ready=%s"
                  % (report["publisher_ready"], report["client_ready"],
                     report["go_live_ready"]))
        return 0 if ok else 1
    sys.stderr.write("release: unknown action %r\n" % action)
    return 2


_EXPORTS = [
    "_pkg_preflight_selftest",
    "_pkg_release_preflight",
    "cmd_release",
]

__all__ = list(_EXPORTS)
