"""Markdown link check for the committed documentation surface.

Feature 260813-modular-core-adoption, task 20. The migration composes the
top-level documents as a UNION of two trees, and a union is exactly where a
dangling link is born: a section lifted from the donor points at a file that
lives in the donor's layout and not (yet) in this one. That failure is silent --
markdown renders a broken relative link as ordinary blue text -- so it needs a
gate rather than a reviewer.

What it checks, per markdown file this repo actually ships:

  * every INLINE link and image target ``[text](target)`` / ``![alt](target)``
  * every REFERENCE definition ``[id]: target``

resolving each LOCAL target against the filesystem, and each ``#fragment``
against the headings of the file it points into (GitHub's slug rules). Targets
carrying a URL scheme, a protocol-relative prefix or a ``mailto:`` are external
and are NOT fetched -- this gate never touches the network (R-RET-08 is about
information needs, not about a test reaching out mid-suite).

Deliberately NOT checked: links inside fenced code blocks (they are samples,
not navigation) and bare autolinks to external hosts.

Usage::

    python3 tests/_markdown_links.py                # this repo
    python3 tests/_markdown_links.py --tree <path>  # any tree
    python3 tests/_markdown_links.py --json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)

#: Directories never walked when a tree has no git index.
WALK_PRUNE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist",
                   "build", ".pytest_cache", ".mypy_cache"}

#: A target with one of these prefixes is external: not resolved, never fetched.
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "//", "ftp://", "tel:",
                     "data:", "irc:", "file:")

#: Path prefixes whose relative links are resolved somewhere ELSE by design, so
#: resolving them against this repo would report a defect that does not exist.
#: Each row carries the destination that DOES resolve them, and the exemption is
#: by PREFIX + reason, never by "this one link is fine" -- an unclaimed file
#: cannot ride an exemption (`unclaimed_exemptions` proves it).
RESOLVED_ELSEWHERE = (
    {"prefix": "templates/",
     "resolves_in": "the external knowledge directory",
     "reason": "a template is COPIED into <knowledge-dir> and its relative "
               "links (../MAIN.md, ./index.md, ../../learnings/<topic>.md) "
               "point at that layout, which is the one the reader lands in. "
               "They are unresolvable here by construction, not broken."},
)

#: A target carrying one of these is a PLACEHOLDER the reader substitutes, not a
#: path: `<knowledge-dir>`, `…`, `<feature>`, `{{name}}`. Checking it would
#: assert that a documentation example is a real file.
PLACEHOLDER_MARKS = ("<", ">", "…", "{{", "}}", "$")

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_RE = re.compile(r"!?\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]*)"
                        r"(?:\s+[\"'][^\"']*[\"'])?\)")
_REFDEF_RE = re.compile(r"^\s{0,3}\[(?P<id>[^\]^][^\]]*)\]:\s*(?P<target>\S+)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_ANCHOR_RE = re.compile(r"<a\s+[^>]*name=[\"'](?P<name>[^\"']+)[\"']", re.I)
_ID_RE = re.compile(r"<[^>]*\sid=[\"'](?P<name>[^\"']+)[\"']", re.I)


# --------------------------------------------------------------------------
# tree selection
# --------------------------------------------------------------------------

def _git(root, *args):
    try:
        proc = subprocess.run(["git", "-C", root] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return None
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None


def markdown_files(root):
    """Every markdown file the tree ships: tracked files, else a walk."""
    tracked = _git(root, "ls-files", "-z", "*.md", "*.markdown")
    if tracked is not None:
        return sorted(rel for rel in tracked.split("\0")
                      if rel and os.path.isfile(os.path.join(root, rel)))
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in WALK_PRUNE_DIRS)
        for name in sorted(filenames):
            if name.lower().endswith((".md", ".markdown")):
                out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(out)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def strip_fences(text):
    """Blank out fenced code blocks, preserving line numbering."""
    out, fence = [], None
    for line in text.split("\n"):
        match = _FENCE_RE.match(line)
        if fence is None and match:
            fence = match.group(1)
            out.append("")
            continue
        if fence is not None:
            out.append("")
            if match and match.group(1) == fence:
                fence = None
            continue
        out.append(line)
    return "\n".join(out)


def slugify(title):
    """GitHub's heading-anchor slug: strip markup, lower, non-word -> dashes."""
    text = re.sub(r"`([^`]*)`", r"\1", title)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_~]", "", text)
    text = unicodedata.normalize("NFKD", text).lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.strip().replace(" ", "-")


def anchors_of(text):
    """Every fragment a link may target inside one markdown document."""
    found = set()
    for line in strip_fences(text).split("\n"):
        match = _HEADING_RE.match(line)
        if match:
            found.add(slugify(match.group("title")))
        for pattern in (_ANCHOR_RE, _ID_RE):
            for hit in pattern.finditer(line):
                found.add(hit.group("name"))
    return {a for a in found if a}


def links_in(text):
    """[(line_number, target)] for every inline link and reference definition."""
    out = []
    for number, line in enumerate(strip_fences(text).split("\n"), start=1):
        for match in _INLINE_RE.finditer(line):
            out.append((number, match.group("target")))
        match = _REFDEF_RE.match(line)
        if match:
            out.append((number, match.group("target")))
    return out


def is_external(target):
    lowered = target.lower()
    return any(lowered.startswith(prefix) for prefix in EXTERNAL_PREFIXES)


def split_target(target):
    """(path, fragment) with angle brackets, %20 escapes and quotes removed."""
    cleaned = target.strip()
    if cleaned.startswith("<") and cleaned.endswith(">"):
        cleaned = cleaned[1:-1].strip()
    path, _, fragment = cleaned.partition("#")
    return path.replace("%20", " "), fragment


def is_placeholder(path):
    """True when the target is a shape for the reader to fill in, not a path."""
    return any(mark in path for mark in PLACEHOLDER_MARKS)


def exemption_for(rel):
    """The RESOLVED_ELSEWHERE row covering `rel`, or None."""
    normalised = rel.replace(os.sep, "/")
    for row in RESOLVED_ELSEWHERE:
        if normalised.startswith(row["prefix"]):
            return row
    return None


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------

def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def _resolve(root, rel, path):
    if path.startswith("/"):
        return os.path.join(root, path.lstrip("/"))
    return os.path.normpath(os.path.join(root, os.path.dirname(rel), path))


def _problem(rel, line, target, detail):
    return {"file": rel, "line": line, "target": target, "detail": detail}


def check_one(root, rel, anchor_cache):
    """Broken links in one markdown file, as a list of problem dicts."""
    body = _read(os.path.join(root, rel))
    if body is None:
        return [_problem(rel, 0, "", "unreadable file")]
    problems = []
    for line, target in links_in(body):
        if not target or is_external(target):
            continue
        path, fragment = split_target(target)
        if is_placeholder(path):
            continue
        if path and exemption_for(rel):
            continue
        if not path:
            if fragment and fragment not in anchors_of(body):
                problems.append(_problem(rel, line, target,
                                         "no such anchor in this file"))
            continue
        resolved = _resolve(root, rel, path)
        if not os.path.exists(resolved):
            problems.append(_problem(rel, line, target, "no such file"))
            continue
        if fragment and resolved.lower().endswith((".md", ".markdown")):
            if resolved not in anchor_cache:
                other = _read(resolved)
                anchor_cache[resolved] = anchors_of(other) if other else set()
            if fragment not in anchor_cache[resolved]:
                problems.append(_problem(rel, line, target,
                                         "no such anchor in target file"))
    return problems


def check_tree(root):
    """(problems, files_checked) over every markdown file the tree ships."""
    cache = {}
    problems = []
    files = markdown_files(root)
    for rel in files:
        problems.extend(check_one(root, rel, cache))
    return problems, files


def unclaimed_exemptions(root):
    """RESOLVED_ELSEWHERE prefixes matching no markdown file in `root`.

    A stale exemption is the failure mode of every allowlist: it silently
    forgives a path class that no longer exists, and the next file to land
    under that prefix inherits the pass. Non-empty means the table lies.
    """
    files = [rel.replace(os.sep, "/") for rel in markdown_files(root)]
    return [row["prefix"] for row in RESOLVED_ELSEWHERE
            if not any(rel.startswith(row["prefix"]) for rel in files)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tree", default=REPO_ROOT, help="tree to check")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    root = os.path.abspath(args.tree)
    problems, files = check_tree(root)
    if args.as_json:
        print(json.dumps({"tree": root, "files": len(files),
                          "problems": problems}, indent=2, sort_keys=True))
    else:
        print("markdown link check %s (%d file(s))" % (root, len(files)))
        for item in problems:
            print("BROKEN %s:%d -> %s -- %s"
                  % (item["file"], item["line"], item["target"],
                     item["detail"]))
        print("%s %d broken link(s)" % ("FAIL" if problems else "OK",
                                        len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
