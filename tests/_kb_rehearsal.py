"""The KB MIGRATION REHEARSAL harness — a whole-tree manifest and a losslessness differ.

Owns: the mechanical half of the migration rehearsal (feature 260813 task 64) —
capturing a byte-level manifest of a knowledge base, diffing two manifests into
added / removed / changed, and turning that diff into a LOSSLESS / ADDITIVE-ONLY
verdict against an explicit, echoed allowlist.
Does NOT own: the migration itself (`agentware_lib.features.migratecmd` holds the
runner and the registry; `agentware_lib.ops.orgmigrate` holds migration 002's
body), nor the acceptance battery run beside it (`index validate`, `audit`,
`recall` are the CLI's own verbs and are invoked as such, never re-implemented
here — a rehearsal that re-implements what it is rehearsing proves nothing).

WHY THIS EXISTS RATHER THAN `diff -rq`
--------------------------------------
The claim a migration rehearsal has to support is "no content was lost", and
`diff -rq` cannot support it on this tree for three separate reasons, each of
which has already cost this project something:

  * it reports a pruned EMPTY DIRECTORY in the same voice as a real difference
    (`Only in a: dir`), so the one loss class that decays a scratch copy silently
    reads as noise — see `learn-tmp-copies-lose-empty-dirs-not-files`, where a
    /tmp donor had lost exactly the three empty directories two plan tasks were
    written to exclude, and the exclusion gates would have passed VACUOUSLY;
  * it says nothing about MODE, so a hook or a script that loses its executable
    bit in a migration is reported as identical;
  * it FOLLOWS a symlink by default, so a retargeted link whose new target
    happens to hold the same bytes is invisible, and a dangling one is an error
    rather than a record.

So the manifest records every node — file, directory (empty or not) and symlink
— and records the properties whose change IS the loss.

WHAT EACH FIELD IS FOR, AND WHAT IS DELIBERATELY ABSENT
-------------------------------------------------------
  * ``sha256`` — content, chunked. A SIZE compare misses a same-length byte flip,
    which is precisely the shape of a frontmatter value rewritten in place.
  * ``size`` — carried for the report only; the hash is what decides.
  * ``mode`` — the permission bits, as an octal string.
  * ``target`` — for a symlink, ``os.readlink`` verbatim. Links are NEVER
    followed and never opened, so a retarget is a change and a dangling link is
    a record rather than a crash.
  * ``kind`` — ``file`` / ``dir`` / ``symlink``. A file replaced by a directory
    of the same name is a change, not a coincidence.

Deliberately NOT recorded: mtime, uid/gid, inode. A migration that rewrites a
file atomically changes its inode and mtime while preserving every byte, and a
copy step changes them wholesale; including them would make every manifest diff
red and train the reader to ignore it. Ownership and times are not the property
under test — content is.

THE ALLOWLIST IS THE DANGEROUS PART, SO IT IS ECHOED
----------------------------------------------------
A losslessness verdict is worth exactly as much as the list of differences it
forgives. A migration legitimately changes two things (the `.schema` sidecar it
stamps and the `logs/migrations.jsonl` line it journals), so the differ has to
forgive something — and an allowlist is the obvious place to bury a real loss.
Two rules keep that honest: a REMOVAL is never forgivable (there is no allowlist
key for it), and every forgiven path is reported BY NAME in the verdict under
``changed_forgiven`` / ``added_expected`` rather than being dropped from the
output. A reader can therefore see what was excused without re-deriving it.

Usage (stdlib only, runnable straight from a clone):

    python3 tests/_kb_rehearsal.py capture <kdir> --out before.json
    # ... run the migration ...
    python3 tests/_kb_rehearsal.py capture <kdir> --out after.json
    python3 tests/_kb_rehearsal.py compare before.json after.json \\
        --allow-changed .schema --allow-added logs/migrations.jsonl

`compare` exits 0 only when the verdict holds, so it is usable as a gate.
"""

import argparse
import hashlib
import json
import os
import stat
import sys

#: Nothing is hidden from a manifest unless the caller asks. A default prune set
#: would be a silent allowlist — the exact failure this module's verdict rules
#: are written to prevent — so the default is empty and `--prune` is explicit.
DEFAULT_PRUNE = ()

_CHUNK = 1 << 20


def sha256_file(path):
    """The sha256 of a file's bytes, read in chunks (a KB holds multi-GB logs)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def node_record(path):
    """The manifest record for ONE node, classified by `lstat` (never followed)."""
    st = os.lstat(path)
    mode = "%04o" % stat.S_IMODE(st.st_mode)
    if stat.S_ISLNK(st.st_mode):
        return {"kind": "symlink", "mode": mode, "target": os.readlink(path)}
    if stat.S_ISDIR(st.st_mode):
        return {"kind": "dir", "mode": mode}
    return {"kind": "file", "mode": mode, "size": st.st_size,
            "sha256": sha256_file(path)}


def capture(root, prune=DEFAULT_PRUNE):
    """{relative path: record} for every node under `root`, `root` itself excluded.

    Directories are recorded whether or not they hold anything, which is what
    makes a pruned EMPTY DIRECTORY a `removed` row instead of an invisible one.
    A symlink to a directory is recorded as a symlink and never descended into.
    `prune` names top-of-listing basenames to omit entirely (their subtrees too).
    """
    root = os.path.abspath(root)
    prune = set(prune)
    out = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in prune)
        for name in dirnames + sorted(n for n in filenames if n not in prune):
            path = os.path.join(dirpath, name)
            out[os.path.relpath(path, root)] = node_record(path)
    return out


def fingerprint(manifest):
    """A single sha256 over the canonical JSON of a manifest.

    So a report can CITE a manifest in one line and a reader can re-derive it,
    rather than pasting 13,000 rows into a worklog to prove which tree was read.
    """
    blob = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _field_delta(before, after):
    """The record fields that differ, as `field: before -> after` strings."""
    keys = sorted(set(before) | set(after))
    return ["%s: %r -> %r" % (k, before.get(k), after.get(k))
            for k in keys if before.get(k) != after.get(k)]


def diff_manifests(before, after):
    """added / removed / changed between two manifests, sorted, with field deltas."""
    added = sorted(k for k in after if k not in before)
    removed = sorted(k for k in before if k not in after)
    changed = {}
    for key in sorted(before):
        if key in after and before[key] != after[key]:
            changed[key] = _field_delta(before[key], after[key])
    return {"added": added, "removed": removed, "changed": changed}


def _under(path, prefixes):
    """True when `path` IS one of `prefixes` or sits beneath one of them."""
    return any(path == p or path.startswith(p.rstrip("/") + "/")
               for p in prefixes)


def verdict(delta, allow_changed=(), allow_added=()):
    """Judge a diff. LOSSLESS = nothing removed and nothing changed off the list.

    `allow_changed` and `allow_added` are path prefixes (a bare path matches
    itself and its subtree). `additive_only` additionally requires every ADDED
    path to be expected, and is meaningful only when `allow_added` is non-empty —
    with no prefixes given every addition is unexpected, which is the honest
    reading of "prove this migration added exactly what it said it would".
    Removals are never forgivable: there is deliberately no allowlist for them.
    """
    changed = list(delta["changed"])
    forgiven = [p for p in changed if _under(p, allow_changed)]
    unexpected_changed = [p for p in changed if p not in forgiven]
    expected_added = [p for p in delta["added"] if _under(p, allow_added)]
    unexpected_added = [p for p in delta["added"] if p not in expected_added]
    lossless = not delta["removed"] and not unexpected_changed
    return {"lossless": lossless,
            "additive_only": lossless and not unexpected_added,
            "removed": list(delta["removed"]),
            "changed_unexpected": unexpected_changed,
            "changed_forgiven": forgiven,
            "added_expected": expected_added,
            "added_unexpected": unexpected_added,
            "allow_changed": sorted(allow_changed),
            "allow_added": sorted(allow_added)}


def render(delta, judged, limit=40):
    """The text report: counts, then the verdict's own lists, each capped.

    A truncated list says so and says by how much — a silently cut report is how
    a losslessness claim gets made against evidence nobody finished reading.
    """
    lines = ["added %d   removed %d   changed %d"
             % (len(delta["added"]), len(delta["removed"]),
                len(delta["changed"]))]
    for label in ("removed", "changed_unexpected", "changed_forgiven",
                  "added_expected", "added_unexpected"):
        rows = judged[label]
        if not rows:
            continue
        lines.append("%s (%d):" % (label, len(rows)))
        lines.extend("  %s" % r for r in rows[:limit])
        if len(rows) > limit:
            lines.append("  ... %d more" % (len(rows) - limit))
    lines.append("LOSSLESS: %s   ADDITIVE-ONLY: %s"
                 % (judged["lossless"], judged["additive_only"]))
    return "\n".join(lines)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _emit(text, out):
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
    else:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")


def _cmd_capture(args):
    man = capture(args.root, prune=tuple(args.prune or ()))
    _emit(json.dumps(man, sort_keys=True, indent=1), args.out)
    sys.stderr.write("captured %d nodes from %s (fingerprint %s)\n"
                     % (len(man), os.path.abspath(args.root),
                        fingerprint(man)))
    return 0


def _cmd_compare(args):
    before, after = _read_json(args.before), _read_json(args.after)
    delta = diff_manifests(before, after)
    judged = verdict(delta, tuple(args.allow_changed or ()),
                     tuple(args.allow_added or ()))
    payload = dict(judged, counts={"before": len(before), "after": len(after)},
                   diff=delta)
    text = (json.dumps(payload, sort_keys=True, indent=1)
            if args.format == "json" else render(delta, judged))
    _emit(text, args.out)
    ok = judged["additive_only"] if args.allow_added else judged["lossless"]
    return 0 if ok else 1


def build_parser():
    parser = argparse.ArgumentParser(
        prog="_kb_rehearsal", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="write a whole-tree manifest as JSON")
    cap.add_argument("root")
    cap.add_argument("--out", help="write here instead of stdout")
    cap.add_argument("--prune", action="append", metavar="NAME",
                     help="omit this basename and its subtree (repeatable)")
    cap.set_defaults(func=_cmd_capture)
    cmp_ = sub.add_parser("compare", help="judge two manifests; rc=1 if not lossless")
    cmp_.add_argument("before")
    cmp_.add_argument("after")
    cmp_.add_argument("--allow-changed", action="append", metavar="PATH")
    cmp_.add_argument("--allow-added", action="append", metavar="PREFIX")
    cmp_.add_argument("--format", choices=("text", "json"), default="text")
    cmp_.add_argument("--out")
    cmp_.set_defaults(func=_cmd_compare)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
