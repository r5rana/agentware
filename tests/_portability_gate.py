"""Task-12 five-surface portability gate: is this tree safe to publish?

agentware is an open-source, VENDOR-NEUTRAL package. Every artifact this
migration copies in comes from a working tree that lived inside a specific
company's environment and next to the operator's own private products, so
"neutral" has to be a gate rather than a habit. This module is that gate.

Five surfaces, because a token that is scrubbed from file bodies still ships in
four other places nobody greps:

  1. ``contents``     -- the bytes of every file the tree ships.
  2. ``filenames``    -- every relative path; a body can be clean while the path
                         that carries it names the vendor (a stray per-run
                         ``.<vendor>-logs/`` directory, a
                         ``skills/<vendor>-ops-report/`` skill).
  3. ``empty_dirs``   -- directories holding nothing shippable. Git cannot
                         represent one, so it is both a clone divergence and the
                         residue a half-finished deletion leaves behind -- and
                         its NAME is scanned like any other path.
  4. ``git_metadata`` -- remote URLs, ref names, ``.git/description`` and the
                         EDITOR SIDECARS (``COMMIT_EDITMSG``, ``MERGE_MSG``,
                         ``SQUASH_MSG``, ``TAG_EDITMSG``) plus the reflog. None
                         of this is a tracked file; all of it travels with a
                         cloned or copied ``.git``.
  5. ``commit_range`` -- author and committer identity, the message body and the
                         touched paths of every commit in a range. A file
                         deleted in the working tree is still in its history.

TWO TOKEN CLASSES, TWO SOURCES -- and the split is deliberate:

* ``operator_product`` is ZERO TOLERANCE and lives HERE, in code, assembled from
  string parts so this file is not itself the hit it is looking for. It names
  the operator's private bridge product, which must never appear in this
  repository in any form. It admits no exemption: a needle in this class that
  carries one is a schema violation, asserted by the suite.
* ``environment`` names a specific company's internal build system, consoles and
  hosts. Those are OPERATOR-SPECIFIC environment knowledge, and R-LOC-03 forbids
  committing that here -- so, exactly like ``_audit_personal_data_check`` in the
  CLI, the needles are resolved at RUNTIME from a private file outside the
  repository and nothing is hardcoded. When that file is absent the class
  reports ``unavailable`` in the report and in the exit banner; it never
  silently contributes zero needles and reads like a pass.

Stdlib only. READ-ONLY: it opens files, runs read-only git plumbing, and writes
nothing anywhere. Never touches the knowledge base.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(REPO)

SCHEMA = "agentware/portability-report@1"
NEEDLE_SCHEMA = "agentware/portability-needles@1"
NEEDLE_BASENAME = "portability-needles.json"
NEEDLE_ENV = "AGENTWARE_PORTABILITY_NEEDLES"

# surfaces ------------------------------------------------------------------
CONTENTS = "contents"
FILENAMES = "filenames"
EMPTY_DIRS = "empty_dirs"
GIT_METADATA = "git_metadata"
COMMIT_RANGE = "commit_range"
SURFACES = (CONTENTS, FILENAMES, EMPTY_DIRS, GIT_METADATA, COMMIT_RANGE)

# token classes -------------------------------------------------------------
ZERO_TOLERANCE = "operator_product"
ENVIRONMENT = "environment"
CLASSES = (ZERO_TOLERANCE, ENVIRONMENT)

SUBSTRING = "substring"
WORD = "word"
MATCH_KINDS = (SUBSTRING, WORD)

#: Directory basenames pruned from a filesystem walk. Deliberately NARROW: it
#: covers only machine artifacts and the object store (which surface 4 inspects
#: properly). Stray log directories, built bundles and vendor sidecars are NOT
#: pruned -- they are exactly what this gate exists to find.
WALK_PRUNE_DIRS = (".git", ".hg", ".svn", "__pycache__", ".pytest_cache",
                   ".mypy_cache", ".ruff_cache", "node_modules")

#: Read as text; anything that fails to decode is skipped, not guessed at.
MAX_SCAN_BYTES = 8 * 1024 * 1024

#: The git sidecars that carry prose an editor wrote. Relative to `.git/`.
GIT_SIDECARS = ("COMMIT_EDITMSG", "MERGE_MSG", "SQUASH_MSG", "TAG_EDITMSG",
                "description", os.path.join("info", "exclude"),
                os.path.join("logs", "HEAD"))


# --------------------------------------------------------------------------
# needles
# --------------------------------------------------------------------------

def zero_tolerance_needles():
    """The operator-directed zero-tolerance class, ASSEMBLED FROM PARTS.

    Written as fragments joined at runtime so this module -- and every report,
    test and traceback quoting it -- carries no literal hit for the very scan it
    performs. Case-insensitive substring, no exemption, no allowlist.
    """
    parts = (("whats", "app"), ("wa", "bridge"), ("wa-", "bridge"),
             ("wa_", "bridge"))
    return [{"class": ZERO_TOLERANCE, "token": "".join(p), "match": SUBSTRING,
             "hint": "the operator's private bridge product; a separate "
                     "codebase that must never appear in this repository",
             "exempt": []} for p in parts]


def needle_search_path(explicit=None):
    """Ordered candidate paths for the private ``environment`` needle file."""
    cands = []
    for value in (explicit, os.environ.get(NEEDLE_ENV)):
        if value:
            cands.append(value)
    kdir = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
    if kdir:
        cands.append(os.path.join(kdir, "config", NEEDLE_BASENAME))
    cands.append(os.path.join(WORKSPACE, ".aw-migration-safety",
                              NEEDLE_BASENAME))
    return cands


def resolve_needle_file(explicit=None):
    for cand in needle_search_path(explicit):
        if os.path.isfile(cand):
            return cand
    return None


def _validate_row(row, source):
    """Reject a malformed needle loudly; a bad row must never mean zero rows."""
    for field in ("token", "match"):
        if not isinstance(row.get(field), str) or not row[field]:
            raise ValueError("%s: needle missing %r" % (source, field))
    if row["match"] not in MATCH_KINDS:
        raise ValueError("%s: unknown match kind %r" % (source, row["match"]))
    cls = row.get("class", ENVIRONMENT)
    if cls != ENVIRONMENT:
        raise ValueError(
            "%s: the private needle file may only declare %r needles (got %r);"
            " the zero-tolerance class is defined in code so it cannot be"
            " weakened, redefined or disabled from outside the repository"
            % (source, ENVIRONMENT, cls))
    exempt = row.get("exempt") or []
    if not isinstance(exempt, list) or any(not isinstance(e, str)
                                           for e in exempt):
        raise ValueError("%s: 'exempt' must be a list of path prefixes"
                         % source)
    return {"class": cls, "token": row["token"], "match": row["match"],
            "hint": row.get("hint", ""), "exempt": list(exempt)}


def load_needles(explicit=None):
    """Return ``(rows, meta)``: the merged needle set and per-class provenance.

    ``meta`` always names every class, and an unsourced class carries the paths
    that were searched -- so "no hits" and "nothing to look for" can never read
    the same way.
    """
    rows = zero_tolerance_needles()
    meta = {ZERO_TOLERANCE: {"status": "builtin", "source": "builtin",
                             "count": len(rows)}}
    path = resolve_needle_file(explicit)
    if not path:
        meta[ENVIRONMENT] = {"status": "unavailable", "source": None,
                             "count": 0,
                             "searched": needle_search_path(explicit)}
        return rows, meta
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema") != NEEDLE_SCHEMA:
        raise ValueError("%s: expected schema %r, got %r"
                         % (path, NEEDLE_SCHEMA, payload.get("schema")))
    extra = [_validate_row(row, path) for row in payload.get("needles", [])]
    rows.extend(extra)
    meta[ENVIRONMENT] = {"status": "sourced", "source": path,
                         "count": len(extra)}
    return rows, meta


def compile_needles(rows):
    """Pair each needle with the case-insensitive pattern that finds it."""
    compiled = []
    for row in rows:
        token = re.escape(row["token"].lower())
        if row["match"] == WORD:
            token = r"(?<![0-9a-z])%s(?![0-9a-z])" % token
        compiled.append((row, re.compile(token)))
    return compiled


def _exempt(row, rel):
    """True iff `rel` sits under one of this needle's declared exemptions."""
    if not rel:
        return False
    norm = rel.replace(os.sep, "/")
    return any(norm == e.rstrip("/") or norm.startswith(e.rstrip("/") + "/")
               for e in row.get("exempt") or [])


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------

def finding(surface, kind, cls, token, location, detail):
    return {"surface": surface, "kind": kind, "class": cls, "token": token,
            "location": location, "detail": detail}


def scan_text(text, compiled, surface, location, rel=None):
    """Every needle that fires in `text`, as one finding per needle."""
    out = []
    low = text.lower()
    for row, pattern in compiled:
        if rel is not None and _exempt(row, rel):
            continue
        spots = list(pattern.finditer(low))
        if not spots:
            continue
        line = low.count("\n", 0, spots[0].start()) + 1
        out.append(finding(
            surface, "token", row["class"], row["token"],
            "%s:%d" % (location, line) if surface == CONTENTS else location,
            "%d occurrence(s); first at line %d" % (len(spots), line)))
    return out


# --------------------------------------------------------------------------
# tree selection
# --------------------------------------------------------------------------

def _git(root, *args):
    """Run read-only git plumbing; return stdout or None if git cannot."""
    try:
        proc = subprocess.run(["git", "-C", root] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def is_git_worktree(root):
    top = _git(root, "rev-parse", "--show-toplevel")
    if not top:
        return False
    return os.path.realpath(top.strip()) == os.path.realpath(root)


def _walk(root):
    """Every file and directory under `root`, pruned by WALK_PRUNE_DIRS."""
    files, dirs = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d.lower() not in WALK_PRUNE_DIRS)
        for name in dirnames:
            dirs.append(os.path.relpath(os.path.join(dirpath, name), root))
        for name in filenames:
            files.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(files), sorted(dirs)


def _ignored_dirs(root, rels):
    """Subset of `rels` git considers ignored (batched into one call)."""
    if not rels:
        return set()
    try:
        proc = subprocess.run(["git", "-C", root, "check-ignore", "--stdin"],
                              input="\n".join(rels).encode("utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return set()
    return {line for line in proc.stdout.decode("utf-8", "replace").split("\n")
            if line}


def select_paths(root):
    """Resolve what a tree ships: tracked files for a git work tree, else a walk.

    A git work tree is scanned through ``git ls-files`` because that is exactly
    what a clone receives -- an ignored build cache is not this gate's business.
    A staged or copied tree has no index, so it is walked instead.
    """
    if is_git_worktree(root):
        out = _git(root, "ls-files", "-z") or ""
        files = sorted(rel for rel in out.split("\0") if rel)
        _, dirs = _walk(root)
        return {"mode": "tracked", "files": files, "dirs": dirs}
    files, dirs = _walk(root)
    return {"mode": "walk", "files": files, "dirs": dirs}


def _readable(path):
    try:
        if os.path.getsize(path) > MAX_SCAN_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


# --------------------------------------------------------------------------
# surfaces
# --------------------------------------------------------------------------

def scan_contents(root, files, compiled):
    out = []
    for rel in files:
        body = _readable(os.path.join(root, rel))
        if body is None:
            continue
        out.extend(scan_text(body, compiled, CONTENTS, rel, rel=rel))
    return out


def scan_filenames(paths, compiled):
    out = []
    for rel in paths:
        out.extend(scan_text(rel, compiled, FILENAMES, rel, rel=rel))
    return out


def _dirs_owning_a_file(files):
    """Every directory with an in-scope file somewhere beneath it."""
    owned = set()
    for rel in files:
        part = os.path.dirname(rel)
        while part:
            owned.add(part)
            part = os.path.dirname(part)
    return owned


def scan_empty_dirs(root, selection, compiled):
    """Directories that ship nothing: a clone divergence, and a name to scan.

    One rule for both modes -- a directory with no in-scope file ANYWHERE below
    it -- so a chain of hollow directories is reported at every level rather
    than only at its leaf. In a git work tree, ignored directories are dropped
    first: an ignored build cache is not a divergence, it is the point.
    """
    owned = _dirs_owning_a_file(selection["files"])
    candidates = [d for d in selection["dirs"] if d not in owned]
    if selection["mode"] == "tracked":
        ignored = _ignored_dirs(root, candidates)
        candidates = [d for d in candidates if d not in ignored]
    out = []
    for rel in sorted(candidates):
        out.append(finding(EMPTY_DIRS, "structural", None, None, rel,
                           "directory ships no file; absent from a fresh clone"))
        out.extend(scan_text(rel, compiled, EMPTY_DIRS, rel, rel=rel))
    return out


def git_metadata_items(repo):
    """(location, text) pairs for every non-tracked thing a `.git` carries."""
    items = []
    config = _git(repo, "config", "--list", "--local")
    if config:
        for line in config.splitlines():
            key = line.split("=", 1)[0]
            items.append(("git-config:%s" % key, line))
    refs = _git(repo, "for-each-ref", "--format=%(refname)")
    for ref in (refs or "").splitlines():
        if ref:
            items.append(("git-ref:%s" % ref, ref))
    gitdir = _git(repo, "rev-parse", "--absolute-git-dir")
    gitdir = gitdir.strip() if gitdir else os.path.join(repo, ".git")
    for name in GIT_SIDECARS:
        body = _readable(os.path.join(gitdir, name))
        if body is not None:
            items.append(("git-sidecar:.git/%s" % name.replace(os.sep, "/"),
                          body))
    return items


def scan_git_metadata(repo, compiled):
    out = []
    for location, text in git_metadata_items(repo):
        out.extend(scan_text(text, compiled, GIT_METADATA, location))
    return out


def commit_range_items(repo, rng):
    """(location, text) pairs for identity, message and paths in `rng`."""
    sep = "\x1e"
    fmt = "%H%x1f%an <%ae>%x1f%cn <%ce>%x1f%B"
    out = _git(repo, "log", "--format=%s%s" % (fmt, sep), rng)
    if out is None:
        raise ValueError("cannot read commit range %r in %s" % (rng, repo))
    items = []
    for record in out.split(sep):
        record = record.strip("\n")
        if not record:
            continue
        sha, author, committer, message = (record.split("\x1f") + ["", "", ""])[:4]
        short = sha[:12]
        items.append(("commit:%s:author" % short, author))
        items.append(("commit:%s:committer" % short, committer))
        items.append(("commit:%s:message" % short, message))
    paths = _git(repo, "log", "--format=", "--name-only", rng) or ""
    for rel in sorted({p for p in paths.splitlines() if p}):
        items.append(("commit-path:%s" % rel, rel))
    return items


def scan_commit_range(repo, rng, compiled):
    out = []
    for location, text in commit_range_items(repo, rng):
        out.extend(scan_text(text, compiled, COMMIT_RANGE, location))
    return out


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def scan_tree(root, compiled, surfaces=SURFACES):
    """Surfaces 1-3 over any tree, plus surface 4 when it carries a `.git`."""
    selection = select_paths(root)
    findings = []
    if CONTENTS in surfaces:
        findings.extend(scan_contents(root, selection["files"], compiled))
    if FILENAMES in surfaces:
        paths = sorted(set(selection["files"]) | set(selection["dirs"]))
        findings.extend(scan_filenames(paths, compiled))
    if EMPTY_DIRS in surfaces:
        findings.extend(scan_empty_dirs(root, selection, compiled))
    if GIT_METADATA in surfaces and os.path.exists(os.path.join(root, ".git")):
        findings.extend(scan_git_metadata(root, compiled))
    return selection, findings


def build_report(trees=(), repo=None, rng=None, needles_path=None,
                 surfaces=SURFACES):
    if bool(repo) != bool(rng):
        # Half a commit-range target would scan four surfaces and report OK on
        # the fifth without ever looking at it.
        raise ValueError("the commit-range surface needs BOTH a repo and a "
                         "range; got repo=%r range=%r" % (repo, rng))
    rows, meta = load_needles(needles_path)
    compiled = compile_needles(rows)
    findings, targets = [], []
    for root in trees:
        selection, found = scan_tree(root, compiled, surfaces)
        targets.append({"kind": "tree", "path": root,
                        "mode": selection["mode"],
                        "files": len(selection["files"]),
                        "dirs": len(selection["dirs"])})
        findings.extend(found)
    if repo and rng and COMMIT_RANGE in surfaces:
        found = scan_commit_range(repo, rng, compiled)
        targets.append({"kind": "commit_range", "path": repo, "range": rng,
                        "commits": _range_count(repo, rng)})
        findings.extend(found)
    return {"schema": SCHEMA, "targets": targets, "classes": meta,
            "needles": len(rows), "counts": _counts(findings),
            "findings": findings, "ok": not findings}


def _range_count(repo, rng):
    out = _git(repo, "rev-list", "--count", rng)
    return int(out.strip()) if out and out.strip().isdigit() else None


def _counts(findings):
    counts = {"total": len(findings)}
    for surface in SURFACES:
        counts[surface] = sum(1 for f in findings if f["surface"] == surface)
    for cls in CLASSES:
        counts[cls] = sum(1 for f in findings if f["class"] == cls)
    counts["structural"] = sum(1 for f in findings if f["kind"] == "structural")
    return counts


def filter_class(report, cls):
    """A view of `report` holding only findings in `cls` (used by --only-class)."""
    kept = [f for f in report["findings"] if f["class"] == cls]
    view = dict(report)
    view["findings"] = kept
    view["counts"] = _counts(kept)
    view["ok"] = not kept
    return view


def surfaces_hit(report):
    return sorted({f["surface"] for f in report["findings"]})


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def render(report, stream, limit=40):
    for target in report["targets"]:
        stream.write("target %s %s\n" % (target["kind"], target["path"]))
    for cls, meta in sorted(report["classes"].items()):
        extra = "" if meta["status"] != "unavailable" else \
            " (searched: %s)" % ", ".join(meta.get("searched", []))
        stream.write("class %-16s %-12s %d needle(s)%s\n"
                     % (cls, meta["status"], meta["count"], extra))
    for f in report["findings"][:limit]:
        stream.write("HIT  %-13s %-16s %s -- %s\n"
                     % (f["surface"], f["class"] or f["kind"], f["location"],
                        f["detail"]))
    if len(report["findings"]) > limit:
        stream.write("... %d more\n" % (len(report["findings"]) - limit))
    stream.write("surfaces hit: %s\n" % (", ".join(surfaces_hit(report))
                                         or "none"))
    stream.write("%s %d finding(s)\n"
                 % ("OK" if report["ok"] else "FAIL", len(report["findings"])))


def build_argparser():
    parser = argparse.ArgumentParser(
        prog="_portability_gate.py",
        description="Five-surface vendor-neutrality gate (read-only).")
    parser.add_argument("--tree", action="append", default=[],
                        help="tree to scan (repeatable; default: this repo)")
    parser.add_argument("--repo", default=None,
                        help="git repo for the commit-range surface")
    parser.add_argument("--range", dest="rng", default=None,
                        help="commit range, e.g. aw-pre-merge..HEAD")
    parser.add_argument("--needles", default=None,
                        help="explicit private needle file")
    parser.add_argument("--only-class", choices=CLASSES, default=None,
                        help="exit non-zero for this class alone")
    parser.add_argument("--debt", nargs="?", const=True, default=None,
                        help="subtract the shrink-only debt ratchet (bare flag "
                             "uses tests/fixtures/portability-debt.json)")
    parser.add_argument("--require-class", action="append", default=[],
                        choices=list(CLASSES),
                        help="fail if this class resolved no needle source")
    parser.add_argument("--surface", action="append", default=[],
                        choices=list(SURFACES), help="limit to these surfaces")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def apply_debt_option(report, value):
    """Subtract the shrink-only debt ratchet; deferred import breaks the cycle."""
    try:
        from tests import _portability_debt as debt
    except ImportError:  # pragma: no cover - direct-script invocation
        import _portability_debt as debt
    return debt.apply_debt(report, debt.load_debt(
        None if value is True else value))


def unavailable_required(report, required):
    """The required classes that resolved no needle source, in order."""
    return [cls for cls in required
            if report["classes"][cls]["status"] == "unavailable"]


def main(argv=None):
    args = build_argparser().parse_args(argv)
    surfaces = tuple(args.surface) if args.surface else SURFACES
    try:
        report = build_report(args.tree or [REPO], args.repo, args.rng,
                              args.needles, surfaces)
        if args.debt:
            report = apply_debt_option(report, args.debt)
    except (ValueError, OSError) as exc:
        sys.stderr.write("portability gate error: %s\n" % exc)
        return 2
    view = filter_class(report, args.only_class) if args.only_class else report
    if args.json:
        json.dump(view, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        render(view, sys.stdout)
    missing = unavailable_required(report, args.require_class)
    if missing:
        sys.stderr.write("required class(es) resolved no needles: %s\n"
                         % ", ".join(missing))
        return 2
    return 0 if view["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
