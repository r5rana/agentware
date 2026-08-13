"""agentware_lib.features.trustcmd -- the `trust` verb (layer: features).

Owns: the `trust` CLI -- the single `cmd_trust` handler serving all four
subverbs (list / pin / rotate / verify) and the `register_trust` registrar that
declares their argparse surface.

WHY THE HANDLER IS HERE AND NOT IN CORE
Task 40 names a "dedicated lib core module" for the ANCHOR, and that is where the
anchor went (agentware_lib.core.pkgtrust). The HANDLER cannot follow it: core
holds no command handlers anywhere in this tree -- every `cmd_*`/`register_*`
lives in knowledge, ops, features or retrieval -- and core is the layer everything
else imports, so putting a CLI there would invert the dependency direction the
whole split exists to establish. `trust` is a PACKAGE-lifecycle verb, a sibling of
`migrate`, so it lands in features alongside migratecmd.

`pin` and `rotate` share one handler and one flag block deliberately: they are the
same operation (append a key to the multi-key anchor) distinguished only by
operator intent, and rotation is exactly a pin carrying `--valid-before` on the
retiring key. Splitting them into two implementations would let the overlap-window
semantics drift between the path that creates it and the path that ends it.

Does NOT own: the anchor itself -- key parsing, fingerprints, the signers file and
its fail-closed security assertion -> agentware_lib.core.pkgtrust; the filesystem
primitives beneath that -> agentware_lib.core.pkghome; the signed SemVer-tag
update channel that CONSUMES the anchor (task 41) -> its own module; the release
preflight (task 42).

Layer deps: features -> core (agentware_lib.core.pkgtrust for the whole anchor
surface) plus stdlib json/os/sys. All DOWNWARD; nothing at or above features is
imported.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

`cmd_trust` is a PURE MOVE from the monolith -- byte-identical, comments and all
(`learn-a-pure-move-of-security-code-needs-byte-comparison-not-review`);
`register_trust` is the monolith's own `trust` argparse block lifted verbatim out
of build_parser into a named registrar, the same shape every port takes.
"""

import json
import os
import subprocess
import sys

from agentware_lib.core.pkgtrust import (
    PKG_PUBLISHER_PRINCIPAL,
    _pkg_allowed_signers_path,
    _pkg_assert_signers_secure,
    _pkg_intree_signing_key_path,
    _pkg_list_signers,
    _pkg_pin_publisher_key,
    _pkg_pubkey_records,
    _pkg_ssh_key_fingerprint,
    _pkg_trust_fingerprint_warnings,
)
from agentware_lib.core.pkghome import _PkgStateError


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("trust", 3600, "register_trust"),
)


def register_trust(sub, fmt_parent):
    """Declare the `trust` verb and its four subverbs.

    Lifted verbatim from the monolith's build_parser. Every leaf sets
    `func=cmd_trust`: the handler dispatches on `args.trust_command` itself, so
    the parser's job is only to declare the surface.
    """
    p_trust = sub.add_parser(
        "trust", parents=[fmt_parent],
        help="manage the HOME-pinned publisher SSH signing-key trust anchor "
             "(~/.agentware/allowed_signers)")
    trust_sub = p_trust.add_subparsers(dest="trust_command", metavar="<subcommand>")
    p_tr_list = trust_sub.add_parser("list", parents=[fmt_parent],
                                     help="list pinned publisher keys")
    p_tr_list.set_defaults(func=cmd_trust)
    for _verb, _help in (("pin", "trust a publisher key (onboarding / first clone)"),
                         ("rotate", "trust a NEW key during an overlap window "
                                    "(operator-confirmed rotation)")):
        p_tr = trust_sub.add_parser(_verb, parents=[fmt_parent], help=_help)
        p_tr.add_argument("--principal", help="signer identity (default: %s)"
                          % PKG_PUBLISHER_PRINCIPAL)
        p_tr.add_argument("--key-file", help="path to the publisher SSH pubkey file")
        p_tr.add_argument("--from-repo", action="store_true",
                          help="pin the in-tree published key (catalog/agentware-release.pub)")
        p_tr.add_argument("--valid-after", dest="valid_after",
                          help="SSH valid-after (e.g. 20260101 or 20260101000000)")
        p_tr.add_argument("--valid-before", dest="valid_before",
                          help="SSH valid-before (overlap-window retirement)")
        p_tr.add_argument("--comment", help="optional key comment")
        p_tr.set_defaults(func=cmd_trust)
    p_tr_verify = trust_sub.add_parser(
        "verify", parents=[fmt_parent],
        help="check trust-anchor integrity + in-tree fingerprint match")
    p_tr_verify.set_defaults(func=cmd_trust)
    p_trust.set_defaults(func=cmd_trust)


def cmd_trust(args):
    """Manage the HOME-pinned publisher trust anchor (~/.agentware/allowed_signers).

    Subcommands: `list` (show pinned signers), `pin` (trust a publisher key —
    trust-on-first-clone / onboarding), `rotate` (trust a NEW key during an
    overlap window; both remain valid so the fleet is never stranded — the
    operator confirms the fingerprint out of band), `verify` (integrity + in-tree
    fingerprint-compare). Fail-closed on a tampered anchor."""
    action = getattr(args, "trust_command", None) or "list"
    as_json = getattr(args, "format", "text") == "json"
    try:
        if action == "list":
            return _trust_list(as_json)
        if action in ("pin", "rotate"):
            return _trust_pin(args, action)
        if action == "verify":
            return _trust_verify(as_json)
        sys.stderr.write("trust: unknown action %r\n" % action)
        return 2
    except _PkgStateError as exc:
        # ONE fail-closed boundary for all four subverbs: any anchor-integrity
        # refusal raised anywhere below surfaces here as a clean exit 1 rather
        # than a traceback. Keeping it on the dispatcher is what lets the three
        # helpers stay linear.
        sys.stderr.write("trust: %s\n" % exc)
        return 1


def _trust_list(as_json):
    """`trust list` — every pinned key with its fingerprint and SSH options."""
    recs = _pkg_list_signers()
    if as_json:
        json.dump({"ok": True, "path": _pkg_allowed_signers_path(),
                   "signers": [{"principal": r["principal"],
                                "keytype": r["keytype"],
                                "fingerprint": r["fingerprint"],
                                "options": r["options"]} for r in recs]},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if not recs:
            print("trust: no publisher keys pinned in %s"
                  % _pkg_allowed_signers_path())
        for r in recs:
            print("trust: %s %s %s%s" % (
                r["principal"], r["keytype"], r["fingerprint"],
                (" [" + " ".join(r["options"]) + "]") if r["options"] else ""))
    return 0


def _trust_resolve_key_file(args, action):
    """Resolve which pubkey file to pin. Returns (path, error_rc); exactly one is
    None. `--from-repo` is a convenience for the in-tree published key and is only
    consulted when no explicit `--key-file` was given."""
    key_file = getattr(args, "key_file", None)
    if getattr(args, "from_repo", False) and not key_file:
        key_file = _pkg_intree_signing_key_path()
        if not key_file:
            sys.stderr.write("trust %s: no in-tree publisher key found "
                             "(catalog/agentware-release.pub) — pass "
                             "--key-file.\n" % action)
            return None, 1
    if not key_file:
        sys.stderr.write("trust %s: --key-file or --from-repo required.\n" % action)
        return None, 2
    return key_file, None


def _trust_pin(args, action):
    """`trust pin` / `trust rotate` — append key(s) to the multi-key anchor.

    ONE implementation for both: a rotation IS a pin, distinguished only by the
    operator's intent and by carrying `--valid-before` on the retiring key. Two
    implementations would let the overlap-window semantics drift between the path
    that opens the window and the path that closes it.
    """
    principal = getattr(args, "principal", None) or PKG_PUBLISHER_PRINCIPAL
    key_file, err = _trust_resolve_key_file(args, action)
    if err is not None:
        return err
    recs = _pkg_pubkey_records(key_file)
    if not recs:
        sys.stderr.write("trust %s: %s has no parseable SSH public key.\n"
                         % (action, key_file))
        return 1
    added_any = False
    for keytype, keydata, fp in recs:
        _fp, added = _pkg_pin_publisher_key(
            principal, keytype, keydata,
            valid_after=getattr(args, "valid_after", None),
            valid_before=getattr(args, "valid_before", None),
            comment=getattr(args, "comment", None))
        added_any = added_any or added
        verb = "pinned" if added else "already trusted"
        if action == "rotate" and added:
            verb = "pinned (ROTATION — overlap window; old key still valid)"
        print("trust %s: %s %s %s" % (action, verb, principal, fp))
    if action == "rotate" and added_any:
        print("trust rotate: confirm this fingerprint with the publisher "
              "OUT OF BAND, then retire the old key after the overlap "
              "window (see docs/release-signing.md).")
    return 0


def _trust_verify(as_json):
    """`trust verify` — anchor integrity plus the in-tree fingerprint compare.

    Exits non-zero when EITHER the anchor fails its security assertion or the
    in-tree published key is not among the pinned ones: an unconfirmed rotation is
    a finding, not a warning to scroll past.
    """
    path = _pkg_allowed_signers_path()
    try:
        _pkg_assert_signers_secure()
        integrity = "ok"
    except _PkgStateError as exc:
        integrity = "FAIL: %s" % exc
    recs = _pkg_list_signers()
    warnings = _pkg_trust_fingerprint_warnings()
    ok = integrity == "ok" and not warnings
    if as_json:
        json.dump({"ok": ok, "path": path, "integrity": integrity,
                   "pinned_count": len(recs), "warnings": warnings},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("trust verify: integrity=%s, %d key(s) pinned"
              % (integrity, len(recs)))
        for w in warnings:
            print("  ⚠ %s" % w)
    return 0 if ok else 1


_EXPORTS = [
    "cmd_trust",
]

__all__ = list(_EXPORTS)
