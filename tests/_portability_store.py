#!/usr/bin/env python3
"""Task-83 SIXTH surface: what did the OBJECT STORE gain, and who authored it?

The five-surface gate scans a tree, its paths, its hollow directories, its
`.git` sidecars and a COMMIT RANGE. Every one of those is reachability-bounded:
`git log aw-pre-merge..HEAD` walks the branch and stops. An object store does
not stop. It holds every commit anything ever fetched, rewrote or abandoned --
and `git push` decides what leaves by REF, not by what the branch happens to
reach.

That gap is not theoretical here. Task 82 rewrote three commits that carried a
second personal address, proved `git diff backup..HEAD` empty, and kept the
pre-rewrite tips on a BACKUP BRANCH so the rewrite stayed reversible. The
rewrite is correct and the range gate is green -- and the old identity is still
in the store, on a ref under `refs/heads/`, which is exactly the namespace
`git push --all` publishes. A gate that only walks the branch reports a clean
history over a store that would leak on the first `--all`.

So this module classifies every commit the store GAINED (present in the store,
absent from the base ref's history) by the weakest `git push` that could
publish it:

  ``branch``       reachable from the ref under review. A plain
                   ``git push origin <branch>`` sends it. ZERO TOLERANCE for a
                   foreign identity -- no ledger row may ever declare one, and
                   `schema_errors` refuses a fixture that tries.
  ``pushable``     reachable from some other ``refs/heads/*`` or ``refs/tags/*``.
                   ``git push --all`` / ``--tags`` sends it. Gated, and
                   declarable in the shrink-only residue ledger with an owner
                   and a remedy.
  ``mirror_only``  reachable only from ``refs/remotes/*`` or a private
                   namespace. Only ``git push --mirror`` sends it, and a mirror
                   push republishes UPSTREAM state verbatim -- whose authorship
                   is not this branch's claim to make. Reported, never gating.
  ``unreachable``  no ref reaches it. No push can send it; ``git gc --prune``
                   removes it. Reported, never gating.

The identity is ANCHORED, not written down. With no explicit base, the base ref
AND the identity come from the commit-series fixture through
`_commit_series.series_identity`, so this module and the task-82 history gate
cannot disagree about who "locally authored" means; an explicit `--base` anchors
at that ref's author. No address is committed here (R-LOC-03).

The ledger is a two-directional ratchet, like every other in this tree: an
undeclared foreign commit in a gated class FAILS, and a declared row the store
still HOLDS but no longer publishes ALSO fails, naming itself -- so the operator
action a row describes cannot be recorded as done while the ref is still there,
and cannot be left behind once it is.

STALE is not the same as ABSENT, and conflating them would make this gate
unrunnable anywhere but one machine. A residue row describes a ref that exists
in ONE working copy (a local backup branch); a fresh clone, a CI checkout and
the parity sweep's `git clone --shared` sandbox never receive it, and the commit
it names is simply not in their object store. "Not here at all" is reported as
`inapplicable` in the report meta and gates nothing; only a commit the store
HOLDS while no push would publish it is stale. The forcing function survives,
because deleting the ref leaves the object behind until a `gc --prune` -- and a
run that pruned the evidence has nothing left to declare.

Stdlib only. READ-ONLY: read-only git plumbing, and it writes nothing anywhere.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:  # the corpus's dual-import seam: ONE module object, whichever spelling wins
    from tests._commit_series import anchor_identity, load_fixture, series_identity
except ImportError:  # pragma: no cover - direct-script invocation
    from _commit_series import anchor_identity, load_fixture, series_identity

SCHEMA = "agentware/object-store-report@1"
LEDGER_SCHEMA = "agentware/object-store-residue@1"
DEFAULT_LEDGER = os.path.join(HERE, "fixtures", "object-store-residue.json")

BRANCH = "branch"
PUSHABLE = "pushable"
MIRROR_ONLY = "mirror_only"
UNREACHABLE = "unreachable"
REACH_CLASSES = (BRANCH, PUSHABLE, MIRROR_ONLY, UNREACHABLE)

#: Classes a `git push` without `--mirror` can publish. Only these are gated;
#: the other two are reported so the report is a census rather than a filter.
GATED = (BRANCH, PUSHABLE)

#: The one class no ledger row may ever declare. Same discipline as the
#: portability debt file's refusal to forgive the zero-tolerance needle class:
#: a finding on the ref actually being reviewed is the finding, and a file that
#: could forgive it would be an allowlist wearing a ratchet's name.
UNDECLARABLE = BRANCH

FOREIGN_AUTHOR = "foreign_author"
FOREIGN_COMMITTER = "foreign_committer"
STALE_ROW = "stale_residue_row"

FIELD_SEP = "\x1f"


# --------------------------------------------------------------------------
# git plumbing (read-only)
# --------------------------------------------------------------------------

def _git(repo, *args):
    try:
        proc = subprocess.run(["git", "-C", repo] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace")


def _lines(text):
    return [line for line in (text or "").split("\n") if line]


def store_commits(repo):
    """Every COMMIT object in the store, whatever reaches it.

    ``--batch-all-objects`` walks loose objects and every pack, which is the
    only enumeration that sees an abandoned rewrite or a fetched-then-forgotten
    branch tip. Blobs and trees are deliberately out of scope: this surface's
    question is authorship, and only a commit carries an identity.
    """
    out = _git(repo, "cat-file", "--batch-all-objects",
               "--batch-check=%(objecttype) %(objectname)")
    if out is None:
        raise ValueError("cannot enumerate the object store in %s" % repo)
    return sorted(line.split(" ", 1)[1] for line in _lines(out)
                  if line.startswith("commit "))


def reachable_from(repo, *refs):
    """The set of commits reachable from `refs` (empty for an unresolvable ref)."""
    out = _git(repo, "rev-list", *refs)
    return set(_lines(out)) if out else set()


def _ref_names(repo):
    return _lines(_git(repo, "for-each-ref", "--format=%(refname)"))


def _classify_refs(refs, head_ref):
    """Split every ref into the push mode that would publish it."""
    head, pushable, mirror = [], [], []
    for ref in refs:
        if ref == head_ref:
            head.append(ref)
        elif ref.startswith("refs/heads/") or ref.startswith("refs/tags/"):
            pushable.append(ref)
        else:
            mirror.append(ref)
    return head, pushable, mirror


def commit_identity(repo, sha):
    """(author_name, author_email, committer_name, committer_email, subject)."""
    fmt = FIELD_SEP.join(["%an", "%ae", "%cn", "%ce", "%s", "%ad"])
    out = _git(repo, "log", "-1", "--format=" + fmt, "--date=short", sha)
    if out is None:
        raise ValueError("cannot read commit %r in %s" % (sha, repo))
    parts = (out.strip("\n").split(FIELD_SEP) + [""] * 6)[:6]
    return dict(zip(("author_name", "author_email", "committer_name",
                     "committer_email", "subject", "date"), parts))


# --------------------------------------------------------------------------
# census
# --------------------------------------------------------------------------

def resolve_head_ref(repo, head_ref=None):
    """The full refname of the branch under review.

    Resolved rather than assumed: a detached HEAD has no ref under
    ``refs/heads/``, and silently treating it as one would put every commit in
    the zero-tolerance class and make the gate impossible to satisfy.
    """
    if head_ref:
        return head_ref
    out = _git(repo, "symbolic-ref", "-q", "HEAD")
    return (out or "").strip() or None


def census(repo, base_ref, head_ref=None):
    """Classify every commit the store holds beyond `base_ref`'s history.

    Returns ``(rows, meta)``. A row is one gained commit with its identity, its
    reach class and the refs that reach it -- attribution matters as much as the
    verdict, because "which ref keeps this alive" IS the remedy.
    """
    head_ref = resolve_head_ref(repo, head_ref)
    base = reachable_from(repo, base_ref)
    if not base:
        raise ValueError("base ref %r reaches no commit in %s"
                         % (base_ref, repo))
    all_refs = _ref_names(repo)
    head_refs, pushable_refs, mirror_refs = _classify_refs(all_refs, head_ref)
    # ONE rev-list per ref, not one merge-base per (ref, commit) pair. The pair
    # form is the obvious spelling and it costs a subprocess per cell: on this
    # branch that is 12 refs x 216 gained commits, ~38s for a gate CI runs on
    # every push. The per-ref walk answers both questions -- which class, and
    # WHICH refs keep it alive -- from the same 12 calls.
    per_ref = {ref: reachable_from(repo, ref) for ref in all_refs}
    reach = {
        BRANCH: _union(per_ref, head_refs),
        PUSHABLE: _union(per_ref, pushable_refs),
        MIRROR_ONLY: _union(per_ref, mirror_refs),
    }
    rows = []
    for sha in store_commits(repo):
        if sha in base:
            continue
        row = commit_identity(repo, sha)
        row["sha"] = sha
        row["reach"] = next((cls for cls in (BRANCH, PUSHABLE, MIRROR_ONLY)
                             if sha in reach[cls]), UNREACHABLE)
        row["refs"] = sorted(r for r in all_refs if sha in per_ref[r])
        rows.append(row)
    meta = {"base_ref": base_ref, "head_ref": head_ref,
            "base_commits": len(base), "refs": len(all_refs),
            "gained": len(rows)}
    return rows, meta


def _union(per_ref, refs):
    out = set()
    for ref in refs:
        out |= per_ref.get(ref, set())
    return out


def foreign_rows(rows, name, email):
    """Gained commits whose author OR committer is not the anchored identity."""
    out = []
    for row in rows:
        kinds = []
        if row["author_email"] != email or row["author_name"] != name:
            kinds.append(FOREIGN_AUTHOR)
        if row["committer_email"] != email or row["committer_name"] != name:
            kinds.append(FOREIGN_COMMITTER)
        if kinds:
            out.append((row, kinds))
    return out


# --------------------------------------------------------------------------
# the shrink-only residue ledger
# --------------------------------------------------------------------------

def _row_errors(row, source):
    errors = []
    for field in ("sha", "reach", "owner", "reason"):
        if not str(row.get(field) or "").strip():
            errors.append("%s: residue row missing %r" % (source, field))
    if row.get("reach") not in REACH_CLASSES and row.get("reach"):
        errors.append("%s: unknown reach class %r" % (source, row.get("reach")))
    if row.get("reach") == UNDECLARABLE:
        errors.append(
            "%s: a %r row may never be declared -- a foreign identity on the "
            "ref under review is the finding, not a thing to forgive"
            % (source, UNDECLARABLE))
    for field in ("author_email", "committer_email", "email", "emails"):
        if field in row:
            errors.append(
                "%s: residue rows must not carry an address; the identity is "
                "anchored at runtime and this file is committed (R-LOC-03)"
                % source)
    if len(str(row.get("reason") or "")) < 40:
        errors.append("%s: residue row %r needs a reason that states the "
                      "remedy, not a shrug" % (source, row.get("sha")))
    return errors


def schema_errors(ledger, source=DEFAULT_LEDGER):
    errors = []
    if ledger.get("schema") != LEDGER_SCHEMA:
        errors.append("%s: expected schema %r, got %r"
                      % (source, LEDGER_SCHEMA, ledger.get("schema")))
    rows = ledger.get("rows")
    if not isinstance(rows, list):
        errors.append("%s: 'rows' must be a list" % source)
        return errors
    for row in rows:
        errors.extend(_row_errors(row, source))
    return errors


def load_ledger(path=None):
    path = path or DEFAULT_LEDGER
    if not os.path.isfile(path):
        return {"schema": LEDGER_SCHEMA, "rows": []}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------

def finding(kind, sha, reach, detail):
    return {"kind": kind, "sha": sha[:12], "reach": reach, "detail": detail}


def _fixture_base():
    return (load_fixture().get("range") or {}).get("base_ref")


def resolve_anchor(repo, base_ref=None):
    """(base_ref, name, email): the fixture's base + anchor, or an explicit base."""
    if base_ref:
        return (base_ref,) + tuple(anchor_identity(repo, base_ref))
    return (_fixture_base(),) + tuple(series_identity(repo, load_fixture()))


def check(repo=None, base_ref=None, head_ref=None, ledger_path=None):
    """The whole surface: census, identity verdict, ratchet reconciliation."""
    repo = repo or REPO
    explicit, base_ref = base_ref, base_ref or _fixture_base()
    ledger = load_ledger(ledger_path)
    errors = schema_errors(ledger, ledger_path or DEFAULT_LEDGER)
    if errors:
        return {"schema": SCHEMA, "ok": False, "schema_errors": errors,
                "findings": [], "rows": [], "meta": {}}
    rows, meta = census(repo, base_ref, head_ref)  # an unknown base fails HERE
    _, name, email = resolve_anchor(repo, explicit)
    declared = {row["sha"]: row for row in ledger.get("rows", [])}
    present = {row["sha"] for row in rows} | reachable_from(repo, base_ref)
    findings, used = _identity_findings(rows, name, email, declared)
    inapplicable = sorted(sha for sha in declared
                          if sha not in used and sha not in present)
    findings.extend(_stale_findings(declared, used, set(inapplicable)))
    meta["identity_anchor"] = base_ref
    meta["declared"] = len(declared)
    meta["inapplicable"] = inapplicable
    return {"schema": SCHEMA, "ok": not findings, "schema_errors": [],
            "findings": findings, "rows": rows, "meta": meta}


def _identity_findings(rows, name, email, declared):
    """Foreign identities in a GATED class, minus the ones a row declares."""
    findings, used = [], set()
    for row, kinds in foreign_rows(rows, name, email):
        if row["reach"] not in GATED:
            continue
        if row["sha"] in declared and row["reach"] != UNDECLARABLE:
            used.add(row["sha"])
            continue
        findings.extend(finding(
            kind, row["sha"], row["reach"],
            "%s is not the identity the branch was cut from; reachable from %s"
            % (kind.replace("foreign_", ""),
               ", ".join(row["refs"]) or "no ref")) for kind in kinds)
    return findings, used


def _stale_findings(declared, used, inapplicable):
    """Rows this store still HOLDS while no push would publish them."""
    return [finding(STALE_ROW, sha, row.get("reach", "?"),
                    "declared residue the store no longer publishes; remove "
                    "the row (owner: %s)" % row.get("owner"))
            for sha, row in sorted(declared.items())
            if sha not in used and sha not in inapplicable]


def by_reach(rows):
    counts = {cls: 0 for cls in REACH_CLASSES}
    for row in rows:
        counts[row["reach"]] += 1
    return counts


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_argparser():
    parser = argparse.ArgumentParser(
        prog="_portability_store.py",
        description="Object-store authorship census and gate (read-only).")
    parser.add_argument("--repo", default=REPO, help="repository to inspect")
    parser.add_argument("--base", default=None,
                        help="base ref whose history is NOT 'gained' (default: "
                             "the commit-series fixture's base and anchor)")
    parser.add_argument("--head", default=None,
                        help="full refname under review (default: current HEAD)")
    parser.add_argument("--ledger", default=None,
                        help="explicit residue ledger path")
    parser.add_argument("--census", action="store_true",
                        help="print every gained commit, not just the findings")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def render(report, stream, census_mode=False):
    for error in report["schema_errors"]:
        stream.write("SCHEMA %s\n" % error)
    meta = report["meta"]
    if meta:
        stream.write("base %s (%d commit(s))  head %s  refs %d\n"
                     % (meta["base_ref"], meta["base_commits"],
                        meta["head_ref"], meta["refs"]))
        counts = by_reach(report["rows"])
        if meta.get("inapplicable"):
            stream.write("inapplicable %d declared row(s) absent from this "
                         "store\n" % len(meta["inapplicable"]))
        stream.write("gained %d: %s\n"
                     % (meta["gained"], "  ".join(
                         "%s=%d" % (c, counts[c]) for c in REACH_CLASSES)))
    if census_mode:
        for row in report["rows"]:
            stream.write("  %-12s %-8s %s  %s\n"
                         % (row["sha"][:12], row["reach"], row["date"],
                            row["subject"][:60]))
    for found in report["findings"]:
        stream.write("HIT  %-22s %-12s %-12s %s\n"
                     % (found["kind"], found["sha"], found["reach"],
                        found["detail"]))
    stream.write("%s %d finding(s)\n"
                 % ("OK" if report["ok"] else "FAIL", len(report["findings"])))


def main(argv=None):
    args = build_argparser().parse_args(argv)
    try:
        report = check(args.repo, args.base, args.head, args.ledger)
    except (ValueError, OSError) as exc:
        sys.stderr.write("object-store gate error: %s\n" % exc)
        return 2
    if args.json:
        json.dump(report, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        render(report, sys.stdout, args.census)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
