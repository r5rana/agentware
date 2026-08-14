"""Resolve a tree's ALWAYS-ON STEERING SET, and scan exactly that set (task 13).

Task 13 asks for the environment-specific wording in the *always-on steering* to
be neutralised, and accepts the work when "the gate token scan over the staged
steering files returns 0 hits". That criterion is only worth anything if
"the steering files" is a NAMED, COMPLETE set. Scoped by eye it is three
markdown files; scoped by what a session actually loads it is fourteen, and the
two files carrying the environment leak are not among the obvious three.

So the set is resolved here from a declared policy plus one derivation, and the
policy is hashed -- widening or narrowing what counts as always-on is a fixture
diff, not a one-line edit nobody re-reads (the shape task 11 used for the
staging exclusion policy).

Five classes, each with the reason it is always-on:

``harness_root``  ``CLAUDE.md`` -- auto-loaded before any prompt is read.
``import``        DERIVED: every ``@<path>`` reachable transitively from a
                  harness root. This is the class that cannot be a literal
                  list: adding one line to ``CLAUDE.md`` makes a new file
                  always-on, and nothing else in the tree would notice.
``bridge``        the non-Claude harness rules files. Same injection guarantee,
                  a different vendor reads them -- so a leak here ships to a
                  reader this repo never tests with.
``persona``       ``.claude/agents/*.md`` -- a spawned subagent's system prompt,
                  always-on for the whole of that agent's life.
``injector``      ``scripts/hooks/session-start.sh`` -- emits the status banner
                  and the knowledge entry document into every session. It is
                  not steering *prose*, it is the code that injects steering,
                  and its comments ship in the clone like any other bytes.

Read-only. No network. Standard library only.
"""

import argparse
import hashlib
import json
import os
import re
import sys

try:
    from tests import _portability_gate as pg
except ImportError:  # pragma: no cover - direct-script invocation
    import _portability_gate as pg

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)

SCHEMA = "agentware/alwayson-steering@1"

HARNESS_ROOT = "harness_root"
IMPORT = "import"
BRIDGE = "bridge"
PERSONA = "persona"
INJECTOR = "injector"

#: The declared policy. `kind` says how the class is resolved, never what it
#: means: `literal` is an exact path set, `glob` a directory + suffix, `derived`
#: a walk this module performs. `reason` is why a leak there reaches a reader.
POLICY = (
    {"class": HARNESS_ROOT, "kind": "literal", "paths": ("CLAUDE.md",),
     "reason": "auto-loaded by the harness into every session before any "
               "prompt is read"},
    {"class": IMPORT, "kind": "derived",
     "reason": "pulled in transitively by an @-import from a harness root; "
               "one added line makes a new file always-on"},
    {"class": BRIDGE, "kind": "literal",
     "paths": (".antigravity", ".clinerules", ".cursorrules", ".google.agy",
               ".windsurfrules"),
     "reason": "another harness's own always-on rules file -- same injection "
               "guarantee, read by a vendor this repo never tests with"},
    {"class": PERSONA, "kind": "glob", "dir": os.path.join(".claude", "agents"),
     "suffix": ".md", "min": 1,
     "reason": "a spawned subagent's system prompt, always-on for the whole "
               "of that agent's life"},
    {"class": INJECTOR, "kind": "literal",
     "paths": (os.path.join("scripts", "hooks", "session-start.sh"),),
     "reason": "emits the status banner and the knowledge entry document into "
               "every session; its comments ship in the clone"},
)

#: A markdown @-import: the whole line is the path, which is what the harness
#: honours. An @ mid-sentence is prose (an email, a decorator) and is not one.
IMPORT_RE = re.compile(r"^@([^\s#]+)\s*$", re.MULTILINE)

#: Problem kinds. Each is a way the SET can be wrong while every file in it is
#: clean -- the failure mode a scoped scan cannot see by itself.
MISSING = "missing_declared_path"
UNRESOLVED = "unresolved_import"
EMPTY_GLOB = "empty_glob_class"
PROBLEM_KINDS = (MISSING, UNRESOLVED, EMPTY_GLOB)


# --------------------------------------------------------------------------
# policy
# --------------------------------------------------------------------------

def _canonical(rule):
    """A rule as sorted primitives, so the digest cannot depend on dict order."""
    out = {}
    for key, value in rule.items():
        out[key] = list(value) if isinstance(value, tuple) else value
    return out


def policy_digest(policy=POLICY):
    """sha256 over the declared policy: widening it is a visible fixture diff."""
    blob = json.dumps([_canonical(r) for r in policy], sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _is_file(root, rel):
    return os.path.isfile(os.path.join(root, rel.replace("/", os.sep)))


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------

def read_imports(root, rel):
    """The @-import targets declared by `rel`, as tree-relative paths."""
    path = os.path.join(root, rel.replace("/", os.sep))
    try:
        with open(path, "r", encoding="utf-8") as handle:
            body = handle.read()
    except (OSError, UnicodeDecodeError):
        return []
    base = os.path.dirname(rel)
    out = []
    for target in IMPORT_RE.findall(body):
        joined = os.path.normpath(os.path.join(base, target)) if base else target
        out.append(joined.replace(os.sep, "/"))
    return out


def _entry(rel, cls, via, reason):
    return {"rel": rel, "class": cls, "via": via, "reason": reason}


def _problem(kind, cls, rel, detail):
    return {"kind": kind, "class": cls, "rel": rel, "detail": detail}


def _resolve_literal(root, rule, entries, problems):
    for rel in rule["paths"]:
        rel = rel.replace(os.sep, "/")
        if not _is_file(root, rel):
            problems.append(_problem(MISSING, rule["class"], rel,
                                     "declared always-on, absent from the tree"))
            continue
        entries.append(_entry(rel, rule["class"], "declared", rule["reason"]))


def _resolve_glob(root, rule, entries, problems):
    base = os.path.join(root, rule["dir"])
    names = sorted(os.listdir(base)) if os.path.isdir(base) else []
    hits = [n for n in names if n.endswith(rule["suffix"])]
    for name in hits:
        rel = "/".join((rule["dir"].replace(os.sep, "/"), name))
        entries.append(_entry(rel, rule["class"], rule["dir"], rule["reason"]))
    if len(hits) < rule.get("min", 0):
        problems.append(_problem(
            EMPTY_GLOB, rule["class"], rule["dir"],
            "matched %d file(s), policy requires at least %d"
            % (len(hits), rule.get("min", 0))))


def _resolve_imports(root, roots, rule, entries, problems):
    """Breadth-first over @-imports; a root is never re-listed as an import."""
    seen = set(roots)
    queue = list(roots)
    while queue:
        parent = queue.pop(0)
        for rel in read_imports(root, parent):
            if not _is_file(root, rel):
                problems.append(_problem(
                    UNRESOLVED, rule["class"], rel,
                    "@-imported by %s but no such file" % parent))
                continue
            if rel in seen:
                continue
            seen.add(rel)
            queue.append(rel)
            entries.append(_entry(rel, rule["class"], parent, rule["reason"]))


def resolve(root, policy=POLICY):
    """The always-on steering set of `root`, plus every way the set is wrong."""
    entries, problems = [], []
    literals = [r for r in policy if r["kind"] == "literal"]
    for rule in literals:
        _resolve_literal(root, rule, entries, problems)
    roots = [e["rel"] for e in entries if e["class"] == HARNESS_ROOT]
    for rule in policy:
        if rule["kind"] == "derived":
            _resolve_imports(root, roots, rule, entries, problems)
        elif rule["kind"] == "glob":
            _resolve_glob(root, rule, entries, problems)
    entries.sort(key=lambda e: (e["class"], e["rel"]))
    problems.sort(key=lambda p: (p["kind"], p["rel"]))
    return {"schema": SCHEMA, "root": root, "policy_digest": policy_digest(policy),
            "entries": entries, "problems": problems,
            "counts": _class_counts(entries)}


def _class_counts(entries):
    counts = {"total": len(entries)}
    for rule in POLICY:
        counts[rule["class"]] = sum(1 for e in entries
                                    if e["class"] == rule["class"])
    return counts


# --------------------------------------------------------------------------
# the scoped scan
# --------------------------------------------------------------------------

def scan(root, needles_path=None, policy=POLICY):
    """A portability report over the always-on set ALONE.

    Contents and filenames only: `empty_dirs`, `git_metadata` and
    `commit_range` are whole-tree surfaces with no meaning for a file set, and
    reporting them here would claim a coverage this scope does not have.
    """
    resolved = resolve(root, policy)
    rows, meta = pg.load_needles(needles_path)
    compiled = pg.compile_needles(rows)
    rels = [e["rel"] for e in resolved["entries"]]
    findings = pg.scan_contents(root, rels, compiled)
    findings.extend(pg.scan_filenames(rels, compiled))
    by_rel = {e["rel"]: e["class"] for e in resolved["entries"]}
    for item in findings:
        item["steering_class"] = by_rel.get(item["location"].split(":")[0])
    return {"schema": SCHEMA, "root": root,
            "policy_digest": resolved["policy_digest"],
            "classes": meta, "needles": len(rows),
            "scanned": len(rels), "entries": resolved["entries"],
            "problems": resolved["problems"], "findings": findings,
            "ok": not findings and not resolved["problems"]}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def render(report, stream):
    stream.write("tree %s\n" % report["root"])
    stream.write("policy %s\n" % report["policy_digest"][:16])
    for cls, meta in sorted(report["classes"].items()):
        stream.write("class %-16s %-12s %d needle(s)\n"
                     % (cls, meta["status"], meta["count"]))
    for entry in report["entries"]:
        stream.write("  on %-14s %s\n" % (entry["class"], entry["rel"]))
    for problem in report["problems"]:
        stream.write("PROBLEM %-22s %s -- %s\n"
                     % (problem["kind"], problem["rel"], problem["detail"]))
    for item in report["findings"]:
        stream.write("HIT %-12s %-14s %s -- %s\n"
                     % (item["surface"], item.get("steering_class"),
                        item["location"], item["detail"]))
    stream.write("%s %d file(s) scanned, %d finding(s), %d problem(s)\n"
                 % ("OK" if report["ok"] else "FAIL", report["scanned"],
                    len(report["findings"]), len(report["problems"])))


def build_argparser():
    parser = argparse.ArgumentParser(
        description="Scan a tree's always-on steering set (read-only).")
    parser.add_argument("--tree", default=REPO, help="tree to resolve and scan")
    parser.add_argument("--needles", help="explicit private needle file")
    parser.add_argument("--require-class", choices=pg.CLASSES, action="append",
                        default=[], dest="required",
                        help="fail if this needle class resolved no source")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv=None):
    args = build_argparser().parse_args(argv)
    report = scan(args.tree, needles_path=args.needles)
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        render(report, sys.stdout)
    missing = [c for c in args.required
               if report["classes"].get(c, {}).get("status") == "unavailable"]
    if missing:
        sys.stderr.write("required class(es) resolved no needles: %s\n"
                         % ", ".join(missing))
        return 2
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
