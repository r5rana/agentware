"""Task-11 donor staging: the exclusion policy, the copy that applies it, and
the three proofs that the copy lost nothing it was not told to drop.

Every later sanitization task (12 neutrality gate, 13 steering wording, 19 test
identities, 20 top-level document union) edits the STAGED tree, never the donor.
So staging has exactly two obligations:

  1. **Drop only the declared classes.** The policy is DATA -- a tuple of rules,
     each carrying its class, its match kind and the reason it exists -- so the
     set of things staging is allowed to lose is reviewable in one place and a
     silent widening shows up as a diff.
  2. **Lose nothing else.** ``verify_no_loss`` walks BOTH trees and asserts a
     bijection modulo the policy, comparing content by sha256 and preserving the
     user-exec bit (the donor ships executables; a mode-0644 copy of an
     entrypoint is the exact failure the plan's executable-bit invariant names).

Empty directories are pruned as a PASS rather than a rule, because most of them
are only empty *after* exclusion. Directories are mirrored first and pruned
bottom-up afterwards, so the report can name every directory that was dropped
instead of silently never creating it.

The emitted manifest describes the STAGED RESULT ONLY -- sorted relative paths, a
content digest and counts. It carries nothing source-dependent (which caches
happened to exist, which empty directories happened to survive), so staging the
same donor content from two different snapshots must produce a byte-identical
manifest. That is the source-independence proof, and it is what lets the
committed fixture be a real ratchet rather than a photograph of one directory.

Stdlib only. Never touches the real knowledge base, and never writes inside the
repository: staging lives in an out-of-tree scratch directory so no sanitization
step can be committed by accident.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(REPO)

SCHEMA = "agentware/staging-manifest@1"

# match kinds -------------------------------------------------------------
#: basename of a directory, matched case-insensitively anywhere in the tree
DIR_NAME = "dir_name"
#: basename of a file, matched case-insensitively as an fnmatch glob
FILE_GLOB = "file_glob"
#: a path anchored at the staging root, matching it and everything under it
REL_PREFIX = "rel_prefix"

#: The complete set of things staging is allowed to drop. Anything not matched
#: here MUST survive into the staged tree; `verify_no_loss` enforces that in
#: both directions.
EXCLUSIONS = (
    {"class": "vcs", "kind": DIR_NAME, "patterns": (".git", ".hg", ".svn"),
     "reason": "the donor object store is quarantined: 645 historical paths "
               "including an unrelated internal product, design documents and "
               "raw prompt logs. Working-tree copy only."},
    {"class": "vcs", "kind": FILE_GLOB, "patterns": (".gitmodules",),
     "reason": "a submodule pointer is meaningless without the quarantined "
               "object store."},
    {"class": "python_cache", "kind": DIR_NAME,
     "patterns": ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  "htmlcov", ".tox"),
     "reason": "build/test caches; regenerated on demand and machine-specific."},
    {"class": "python_cache", "kind": FILE_GLOB,
     "patterns": ("*.pyc", "*.pyo", "*.pyd", ".coverage", ".coverage.*"),
     "reason": "compiled and coverage artifacts of the caches above."},
    {"class": "node_cache", "kind": DIR_NAME,
     "patterns": ("node_modules", ".vite", ".turbo", ".parcel-cache"),
     "reason": "installed dependency trees and bundler caches; restored from "
               "the committed lockfile."},
    {"class": "log_dirs", "kind": DIR_NAME,
     "patterns": (".<neutralised>-logs", ".agentware-logs", "logs"),
     "reason": "stray per-run log directories from the donor operator's "
               "environment; never part of the package."},
    {"class": "log_dirs", "kind": FILE_GLOB, "patterns": ("*.log",),
     "reason": "loose log files, ignored by the donor's own gitignore."},
    {"class": "built_bundle", "kind": REL_PREFIX,
     "patterns": ("webui/dist", "webui/build", "webui/coverage",
                  "webui/test-results", "webui/playwright-report"),
     "reason": "the 7.4 MB built webui bundle and its test output; a compiled "
               "artifact of webui/src, which IS staged."},
    {"class": "os_noise", "kind": FILE_GLOB,
     "patterns": (".ds_store", "thumbs.db", "*.swp", "*.swo", "*~", "._*"),
     "reason": "operating-system and editor droppings."},
    {"class": "diligence_doc", "kind": FILE_GLOB,
     "patterns": ("*diligence*",),
     "reason": "the untracked diligence document the plan excludes from every "
               "copy step. Absent from both donor snapshots as of 2026-08-14, "
               "so this rule is a RATCHET protecting that state.",
     "ratchet": True},
    {"class": "diligence_doc", "kind": DIR_NAME, "patterns": ("*diligence*",),
     "reason": "the same exclusion, in case the document arrives as a folder.",
     "ratchet": True},
)

#: Resolved left-to-right; the first that exists wins. Both are donor snapshots
#: of the same working tree. The durable one is preferred because /tmp is
#: subject to periodic cleanup, which has already pruned empty directories from
#: the /tmp snapshot.
DEFAULT_SOURCES = (
    os.path.join(WORKSPACE, ".aw-migration-safety", "donor-Agentware"),
    os.path.join(os.sep, "tmp", "aw-scratch", "Agentware"),
)

#: Out of tree by construction: staging is edited by tasks 13/19/20 and must
#: never be committable.
DEFAULT_DEST = os.path.join(WORKSPACE, "agentware-scratch",
                            "260813-modular-core-adoption", "staging", "donor")

MANIFEST_FIXTURE = os.path.join(HERE, "fixtures", "staging-manifest.json")


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------

def _rules(kind):
    for rule in EXCLUSIONS:
        if rule["kind"] == kind:
            for pattern in rule["patterns"]:
                yield rule["class"], pattern


def _glob_hit(name, pattern):
    return fnmatch.fnmatchcase(name.lower(), pattern.lower())


def _prefix_hit(rel, pattern):
    return rel == pattern or rel.startswith(pattern + "/")


def classify_dir(rel):
    """Return the exclusion class of directory `rel`, or None to keep it."""
    base = os.path.basename(rel)
    for cls, pattern in _rules(DIR_NAME):
        if _glob_hit(base, pattern):
            return cls
    for cls, pattern in _rules(REL_PREFIX):
        if _prefix_hit(rel, pattern):
            return cls
    return None


def classify_file(rel):
    """Return the exclusion class of file `rel`, or None to keep it."""
    base = os.path.basename(rel)
    for cls, pattern in _rules(FILE_GLOB):
        if _glob_hit(base, pattern):
            return cls
    for cls, pattern in _rules(REL_PREFIX):
        if _prefix_hit(rel, pattern):
            return cls
    return None


def excluded_class(rel, is_dir):
    """Class excluding `rel`, counting an excluded ANCESTOR directory.

    `classify_dir`/`classify_file` answer for one entry; the walk prunes whole
    subtrees, so the loss check needs the ancestor-aware answer to agree.
    """
    parts = rel.split("/")
    for depth in range(1, len(parts)):
        cls = classify_dir("/".join(parts[:depth]))
        if cls:
            return cls
    return classify_dir(rel) if is_dir else classify_file(rel)


def policy_rows():
    """The policy as JSON-safe data, in declaration order."""
    return [{"class": r["class"], "kind": r["kind"],
             "patterns": list(r["patterns"]), "reason": r["reason"],
             "ratchet": bool(r.get("ratchet"))} for r in EXCLUSIONS]


def policy_digest():
    blob = json.dumps(policy_rows(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# filesystem helpers
# --------------------------------------------------------------------------

def _rel(root, path):
    rel = os.path.relpath(path, root)
    return "" if rel == "." else rel.replace(os.sep, "/")


def _abs(root, rel):
    return os.path.join(root, *rel.split("/")) if rel else root


def sha256_of(path):
    """Content hash of a file, or `link:<target>` for a symlink."""
    if os.path.islink(path):
        return "link:" + os.readlink(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_exec(path):
    return bool(os.lstat(path).st_mode & 0o111) and not os.path.islink(path)


def walk_files(root):
    """Every file (and symlink) under `root`, as sorted relative paths."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in filenames:
            found.append(_rel(root, os.path.join(dirpath, name)))
    return sorted(found)


def walk_dirs(root):
    """Every directory under `root`, as sorted relative paths."""
    found = []
    for dirpath, dirnames, _files in os.walk(root):
        dirnames.sort()
        for name in dirnames:
            found.append(_rel(root, os.path.join(dirpath, name)))
    return sorted(found)


def empty_dirs(root):
    """Directories under `root` with no entries at all."""
    return sorted(rel for rel in walk_dirs(root) if not os.listdir(_abs(root, rel)))


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------

def _account(source, rel, cls, report):
    """Record one excluded entry, counting the files it takes with it."""
    bucket = report["excluded"].setdefault(cls, {"roots": [], "files": 0})
    bucket["roots"].append(rel)
    path = _abs(source, rel)
    bucket["files"] += len(walk_files(path)) if os.path.isdir(path) else 1


def _copy_entry(source, dest, rel):
    src, dst = _abs(source, rel), _abs(dest, rel)
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    else:
        shutil.copy2(src, dst)


def prune_empty_dirs(root):
    """Remove every empty directory under `root`, deepest first."""
    pruned = []
    for dirpath, _dirnames, _files in os.walk(root, topdown=False):
        if dirpath == root or os.listdir(dirpath):
            continue
        rel = _rel(root, dirpath)
        os.rmdir(dirpath)
        pruned.append(rel)
    return sorted(pruned)


def stage(source, dest):
    """Mirror `source` into a fresh `dest`, applying the policy.

    Returns the source-dependent REPORT (what was excluded, what was pruned).
    The source-independent description of the result is `build_manifest`.
    """
    if not os.path.isdir(source):
        raise SystemExit("donor source not found: %s" % source)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    report = {"excluded": {}, "symlinks": [], "pruned_empty_dirs": []}
    for dirpath, dirnames, filenames in os.walk(source):
        rel_root = _rel(source, dirpath)
        keep = []
        for name in sorted(dirnames):
            rel = "%s/%s" % (rel_root, name) if rel_root else name
            cls = classify_dir(rel)
            if cls:
                _account(source, rel, cls, report)
            else:
                keep.append(name)
                os.makedirs(_abs(dest, rel), exist_ok=True)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = "%s/%s" % (rel_root, name) if rel_root else name
            cls = classify_file(rel)
            if cls:
                _account(source, rel, cls, report)
                continue
            if os.path.islink(_abs(source, rel)):
                report["symlinks"].append(rel)
            _copy_entry(source, dest, rel)
    report["pruned_empty_dirs"] = prune_empty_dirs(dest)
    return report


def build_manifest(dest):
    """Describe the staged tree. Source-independent by construction."""
    files = walk_files(dest)
    digest = hashlib.sha256()
    total = 0
    for rel in files:
        path = _abs(dest, rel)
        content = sha256_of(path)
        total += 0 if os.path.islink(path) else os.lstat(path).st_size
        digest.update(("%s\0%s\0%d\n" % (rel, content,
                                         int(is_exec(path)))).encode("utf-8"))
    return {
        "schema": SCHEMA,
        "policy_digest": policy_digest(),
        "policy": policy_rows(),
        "counts": {"files": len(files), "dirs": len(walk_dirs(dest)),
                   "bytes": total},
        "digest": digest.hexdigest(),
        "files": files,
    }


# --------------------------------------------------------------------------
# proofs
# --------------------------------------------------------------------------

def verify_no_loss(source, dest):
    """Assert a bijection between source and staging, modulo the policy.

    Returns a list of human-readable problems; empty means proven.
    """
    problems = []
    staged = set(walk_files(dest))
    for rel in walk_files(source):
        cls = excluded_class(rel, False)
        if cls:
            if rel in staged:
                problems.append("excluded (%s) but staged: %s" % (cls, rel))
            continue
        if rel not in staged:
            problems.append("lost in staging: %s" % rel)
            continue
        problems.extend(_compare_one(source, dest, rel))
    for rel in sorted(staged - set(walk_files(source))):
        problems.append("invented by staging: %s" % rel)
    problems.extend(_compare_dirs(source, dest))
    return problems


def _compare_one(source, dest, rel):
    src, dst = _abs(source, rel), _abs(dest, rel)
    out = []
    if sha256_of(src) != sha256_of(dst):
        out.append("content differs: %s" % rel)
    if is_exec(src) != is_exec(dst):
        out.append("exec bit differs: %s" % rel)
    return out


def _compare_dirs(source, dest):
    """Every source directory holding a KEPT file must exist in staging.

    "Holding files" is not enough: a directory whose only members are excluded
    (a `notes/` holding one diligence document, a `caches/` holding one
    `__pycache__`) is empty after the policy runs and is correctly pruned. Only
    a directory that still owns surviving content may be demanded back.
    """
    out = []
    staged = set(walk_dirs(dest))
    for rel in walk_dirs(source):
        if excluded_class(rel, True) or rel in staged:
            continue
        kept = [child for child in walk_files(_abs(source, rel))
                if not excluded_class("%s/%s" % (rel, child), False)]
        if kept:
            out.append("lost directory holding files: %s" % rel)
    return out


def assert_clean(dest):
    """The acceptance checks for task 11, as a list of problems."""
    problems = []
    if os.path.exists(os.path.join(dest, ".git")):
        problems.append("a .git directory exists under staging")
    for rel in empty_dirs(dest):
        problems.append("empty directory under staging: %s" % rel)
    for rel in walk_files(dest):
        cls = excluded_class(rel, False)
        if cls:
            problems.append("policy class %s survived: %s" % (cls, rel))
    for rel in walk_dirs(dest):
        cls = excluded_class(rel, True)
        if cls:
            problems.append("policy class %s survived: %s" % (cls, rel))
    for rel in walk_files(dest) + walk_dirs(dest):
        if "diligence" in rel.lower():
            problems.append("diligence artifact under staging: %s" % rel)
    return problems


def resolve_source(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("AGENTWARE_DONOR_SOURCE")
    if env:
        return env
    for candidate in DEFAULT_SOURCES:
        if os.path.isdir(candidate):
            return candidate
    return DEFAULT_SOURCES[0]


def resolve_dest(explicit=None):
    return explicit or os.environ.get("AGENTWARE_DONOR_STAGING") or DEFAULT_DEST


def write_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def load_json(path):
    with open(path) as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cmd_stage(args):
    source, dest = resolve_source(args.source), resolve_dest(args.dest)
    report = stage(source, dest)
    manifest = build_manifest(dest)
    problems = verify_no_loss(source, dest) + assert_clean(dest)
    if args.manifest_out:
        write_json(args.manifest_out, manifest)
    print("source  %s" % source)
    print("dest    %s" % dest)
    print("staged  %(files)d files / %(dirs)d dirs / %(bytes)d bytes"
          % manifest["counts"])
    print("digest  %s" % manifest["digest"])
    for cls in sorted(report["excluded"]):
        bucket = report["excluded"][cls]
        print("drop    %-14s %4d files via %d root(s)"
              % (cls, bucket["files"], len(bucket["roots"])))
    print("pruned  %d empty dir(s)" % len(report["pruned_empty_dirs"]))
    for rel in report["pruned_empty_dirs"]:
        print("        %s" % rel)
    return _report_problems(problems)


def _cmd_verify(args):
    source, dest = resolve_source(args.source), resolve_dest(args.dest)
    problems = verify_no_loss(source, dest) + assert_clean(dest)
    print("verify  %s -> %s" % (source, dest))
    return _report_problems(problems)


def _cmd_manifest(args):
    dest = resolve_dest(args.dest)
    manifest = build_manifest(dest)
    if args.out:
        write_json(args.out, manifest)
        print("wrote %s" % args.out)
    else:
        print(json.dumps(manifest, indent=2))
    return 0


def _cmd_check(args):
    """Re-stage into a scratch dir and compare against a committed manifest."""
    import tempfile
    source = resolve_source(args.source)
    expected = load_json(args.manifest or MANIFEST_FIXTURE)
    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, "donor")
        stage(source, dest)
        actual = build_manifest(dest)
    problems = diff_manifests(expected, actual)
    print("check   %s against %s" % (source, args.manifest or MANIFEST_FIXTURE))
    return _report_problems(problems)


def diff_manifests(expected, actual):
    """Human-readable differences between two staging manifests."""
    problems = []
    for key in ("schema", "policy_digest", "digest"):
        if expected.get(key) != actual.get(key):
            problems.append("%s: expected %s, got %s"
                            % (key, expected.get(key), actual.get(key)))
    if expected.get("counts") != actual.get("counts"):
        problems.append("counts: expected %s, got %s"
                        % (expected.get("counts"), actual.get("counts")))
    missing = sorted(set(expected.get("files", [])) - set(actual.get("files", [])))
    extra = sorted(set(actual.get("files", [])) - set(expected.get("files", [])))
    problems.extend("missing from staging: %s" % rel for rel in missing[:20])
    problems.extend("unexpected in staging: %s" % rel for rel in extra[:20])
    return problems


def _report_problems(problems):
    if not problems:
        print("OK")
        return 0
    for problem in problems[:40]:
        print("FAIL  %s" % problem)
    print("FAIL  %d problem(s)" % len(problems))
    return 1


def build_argparser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="cmd", required=True)
    stage_p = subs.add_parser("stage", help="stage the donor working tree")
    stage_p.add_argument("--source")
    stage_p.add_argument("--dest")
    stage_p.add_argument("--manifest-out", dest="manifest_out")
    stage_p.set_defaults(func=_cmd_stage)
    verify_p = subs.add_parser("verify", help="re-prove staging against source")
    verify_p.add_argument("--source")
    verify_p.add_argument("--dest")
    verify_p.set_defaults(func=_cmd_verify)
    man_p = subs.add_parser("manifest", help="print or write the manifest")
    man_p.add_argument("--dest")
    man_p.add_argument("--out")
    man_p.set_defaults(func=_cmd_manifest)
    check_p = subs.add_parser("check", help="re-stage and compare to a manifest")
    check_p.add_argument("--source")
    check_p.add_argument("--manifest")
    check_p.set_defaults(func=_cmd_check)
    return parser


def main(argv=None):
    args = build_argparser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
