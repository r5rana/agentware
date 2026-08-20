"""Task-82 gate: the branch's commit series is publishable.

This repository is going out as a vendor-neutral, open-source package, and a
git history is the one artifact a publication review reliably forgets. The
working tree can be scrubbed perfectly while `git log` still ships a second
identity, a colleague's address in a trailer, a tool's attribution line, or a
commit dated by a clock nobody trusts. `tests/_portability_gate.py` already
scans the commit range for VENDOR TOKENS; nothing scanned the range for the
shape of the series itself.

Four arms, each answering a question a token scan cannot:

  1. ``identity``  one operator, one address -- for the author field, the
                   COMMITTER field (which is a different field, set from a
                   different config key, and is the one a rebase silently
                   rewrites), and every email-shaped token anywhere in the
                   subject, the body or a trailer. `Co-Authored-By:` is how a
                   foreign identity enters a log without ever being the author.
                   **The identity is ANCHORED, never written down**: it is read
                   at runtime from the author of the commit the branch was cut
                   from, so the range must match the history it grew out of.
                   Writing the address into the fixture would put operator data
                   in a public repository (`R-LOC-03`, and `_repo_standard`'s
                   personal-data arm duly caught the first version of this file
                   doing exactly that) -- and it would create the one field a
                   future maintainer could widen to two addresses to buy
                   silence. There is no such field. A stray identity can only
                   be cleared by removing it from history.
  2. ``window``    every date inside a DECLARED window: not before the branch
                   point, not in the future, and with the committer date never
                   preceding the author date -- the inversion a backdating
                   forgery produces and a real commit cannot.
  3. ``message``   every subject describes an engineering change: conventional
                   shape, bounded length, no junk token, no tool-attribution
                   line. Non-conforming subjects live in a SHRINK-ONLY ledger.
  4. ``shape``     the series is linear (no merge commit), starts at the
                   declared base ref, and never gets SHORTER -- the check that
                   catches a squash or a reset eating recorded history.

**Every bound is declared in `tests/fixtures/commit-series.json`, and the
declaration is deliberately hostile to being widened.** The identity block
carries an ANCHOR and no literal: a `name` or `emails` key there is a schema
error, so there is nothing to add a second address to. The window's `first` is
pinned by VALUE and asserted equal to the range's real earliest date, so
widening it to admit an out-of-window commit reddens on the other side. The
count carries a floor and no ceiling: a series may grow, and a series that
shrinks has lost history.

**What is deliberately NOT pinned: the window's end.** An end date pinned by
value must be re-stamped by every commit that follows it, including the commit
that lands the stamp -- a ratchet that must be re-cut to commit is a ratchet
nobody keeps. The end is `today` instead, which is a real bound (a commit dated
tomorrow is a broken clock or a forgery) and needs no maintenance.

Stdlib only. READ-ONLY: it runs `git log`/`git rev-parse` plumbing and writes
nothing, except through the explicit `--stamp` flag.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)

SCHEMA = "agentware/commit-series@1"
FIXTURE_REL = os.path.join("tests", "fixtures", "commit-series.json")

# arms ----------------------------------------------------------------------
IDENTITY = "identity"
WINDOW = "window"
MESSAGE = "message"
SHAPE = "shape"
ARMS = (IDENTITY, WINDOW, MESSAGE, SHAPE)

#: The conventional-commit subject shape. A scope may name several subsystems
#: (`feat(ingest,dream): ...`), which is why the comma is admitted.
CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|docs|test|refactor|perf|build|ci|chore|style|revert)"
    r"(\([a-z0-9][a-z0-9._,/-]*\))?!?: \S.*$")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

#: Trailer/attribution prefixes that can carry an identity other than the
#: author's. `Generated with` has no address at all, which is exactly why the
#: email sweep alone cannot catch it.
ATTRIBUTION_RE = re.compile(
    r"^[ \t]*(co-authored-by|signed-off-by|assisted-by|reviewed-by"
    r"|reported-by|acked-by|tested-by|generated with|authored-by)\b",
    re.IGNORECASE | re.MULTILINE)

#: A subject whose FIRST word is one of these describes no engineering change.
JUNK_FIRST_WORDS = frozenset((
    "wip", "fixup", "squash", "temp", "tmp", "asdf", "xxx", "foo", "bar",
    "stuff", "misc", "changes", "update", "updates", "final", "cleanup",
    "checkpoint", "amend", "oops", "typo", "revert"))

SUBJECT_MIN = 12
SUBJECT_MAX = 120

#: An exemption reason shorter than this is a shrug, not a reason.
MIN_REASON = 24

#: The identity is READ from the base ref's author, never written down.
BASE_AUTHOR_ANCHOR = "base_ref_author"

RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"
LOG_FORMAT = FIELD_SEP.join(
    ("%H", "%P", "%an", "%ae", "%cn", "%ce", "%aI", "%cI", "%s", "%B")) + RECORD_SEP


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------
def fixture_path(explicit=None):
    return explicit or os.path.join(REPO, FIXTURE_REL)


def load_fixture(explicit=None):
    with open(fixture_path(explicit), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _reason_errors(rows, label):
    """Every declared row owes a subject and a reason that is not a shrug."""
    errors = []
    seen = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append("%s[%d]: not an object" % (label, i))
            continue
        subject = row.get("subject") or ""
        reason = row.get("reason") or ""
        if not subject:
            errors.append("%s[%d]: no subject" % (label, i))
        elif subject in seen:
            errors.append("%s[%d]: duplicate subject %r" % (label, i, subject))
        seen.add(subject)
        if len(reason) < MIN_REASON:
            errors.append("%s[%d]: reason is a shrug (%d < %d chars)"
                          % (label, i, len(reason), MIN_REASON))
    return errors


def _identity_errors(ident):
    """The identity block carries an ANCHOR and nothing else.

    A literal name or address here would be operator data in a public
    repository (R-LOC-03) AND the one field a maintainer could widen to two
    entries to silence a finding. Refusing the key outright removes both.
    """
    errors = []
    if ident.get("anchor") != BASE_AUTHOR_ANCHOR:
        errors.append("identity.anchor: expected %r, got %r"
                      % (BASE_AUTHOR_ANCHOR, ident.get("anchor")))
    for key in ("name", "email", "emails"):
        if key in ident:
            errors.append(
                "identity.%s: the identity is ANCHORED, never declared -- a "
                "literal here is operator data (R-LOC-03) and is the field a "
                "second address would be added to" % (key,))
    return errors


def _bounds_errors(fx):
    """The window and count blocks: a pinned floor, and no ceiling anywhere."""
    errors = []
    first = (fx.get("window") or {}).get("first") or ""
    try:
        datetime.date.fromisoformat(first)
    except ValueError:
        errors.append("window.first: %r is not an ISO date" % (first,))
    count = fx.get("count") or {}
    count_min = count.get("min")
    if not isinstance(count_min, int) or count_min < 1:
        errors.append("count.min: expected a positive integer, got %r" % (count_min,))
    if "max" in count:
        errors.append("count.max: a series may grow; declare a floor only")
    if not (fx.get("range") or {}).get("base_ref"):
        errors.append("range.base_ref: no base ref declared")
    return errors


def schema_errors(fx):
    """Structural violations of the fixture contract, offender named."""
    errors = []
    if fx.get("schema") != SCHEMA:
        errors.append("schema: expected %r, got %r" % (SCHEMA, fx.get("schema")))
    errors.extend(_identity_errors(fx.get("identity") or {}))
    errors.extend(_bounds_errors(fx))
    errors.extend(_reason_errors(fx.get("subject_exemptions") or [],
                                 "subject_exemptions"))
    return errors


# ---------------------------------------------------------------------------
# reading the series
# ---------------------------------------------------------------------------
def _git(repo, *args):
    proc = subprocess.run(["git", "-C", repo] + list(args),
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout


def read_series(repo, base_ref, head_ref="HEAD"):
    """Every commit in `base_ref..head_ref`, newest first, as dicts.

    Raises rather than returning empty when the range cannot be read: an
    unreadable range and an empty range print the same "0 findings".
    """
    out = _git(repo, "log", "--format=" + LOG_FORMAT, "%s..%s" % (base_ref, head_ref))
    if out is None:
        raise ValueError("cannot read range %s..%s in %s" % (base_ref, head_ref, repo))
    series = []
    for record in out.split(RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        f = (record.split(FIELD_SEP) + [""] * 10)[:10]
        series.append({
            "sha": f[0], "parents": f[1].split(), "author_name": f[2],
            "author_email": f[3], "committer_name": f[4], "committer_email": f[5],
            "author_date": f[6], "committer_date": f[7], "subject": f[8],
            "body": f[9],
        })
    return series


def _short(commit):
    return commit["sha"][:9]


def finding(arm, kind, commit, detail):
    return {"arm": arm, "kind": kind, "commit": commit, "detail": detail}


# ---------------------------------------------------------------------------
# arm 1 -- identity
# ---------------------------------------------------------------------------
def anchor_identity(repo, base_ref):
    """(name, email) of the commit the branch was cut from.

    Raises rather than returning a default: an unresolvable anchor and a clean
    range must never print the same thing.
    """
    out = _git(repo, "log", "-1", "--format=%an" + FIELD_SEP + "%ae", base_ref)
    if out is None:
        raise ValueError("cannot resolve the identity anchor at %r in %s"
                         % (base_ref, repo))
    name, _, email = out.strip("\n").partition(FIELD_SEP)
    return name, email


def check_identity(series, fx, repo):
    base_ref = (fx.get("range") or {}).get("base_ref") or ""
    name, email = anchor_identity(repo, base_ref)
    emails = {email}
    found = []
    for c in series:
        for role in ("author", "committer"):
            if c["%s_name" % role] != name:
                found.append(finding(
                    IDENTITY, "foreign_%s_name" % role, _short(c),
                    "%s name %r is not the branch's anchored identity %r"
                    % (role, c["%s_name" % role], name)))
            if c["%s_email" % role] not in emails:
                found.append(finding(
                    IDENTITY, "foreign_%s_email" % role, _short(c),
                    "%s address %r is not the identity the branch was cut "
                    "from" % (role, c["%s_email" % role])))
        for addr in sorted(set(EMAIL_RE.findall(c["subject"] + "\n" + c["body"]))):
            if addr not in emails:
                found.append(finding(
                    IDENTITY, "foreign_address_in_message", _short(c),
                    "message carries the address %r" % (addr,)))
        for match in ATTRIBUTION_RE.finditer(c["body"]):
            found.append(finding(
                IDENTITY, "attribution_trailer", _short(c),
                "message carries the trailer %r"
                % (c["body"][match.start():match.end()].strip(),)))
    return found


# ---------------------------------------------------------------------------
# arm 2 -- window
# ---------------------------------------------------------------------------
def _day(iso):
    return (iso or "")[:10]


def _commit_window_findings(c, base_day, declared_first, today):
    """Every way ONE commit's two timestamps can fall outside the bounds."""
    found = []
    for role in ("author", "committer"):
        day = _day(c["%s_date" % role])
        if base_day and day < base_day:
            found.append(finding(
                WINDOW, "predates_branch_point", _short(c),
                "%s date %s is before the base ref's %s" % (role, day, base_day)))
        if day < declared_first:
            found.append(finding(
                WINDOW, "before_declared_window", _short(c),
                "%s date %s is before the declared first %s"
                % (role, day, declared_first)))
        if day > today:
            found.append(finding(
                WINDOW, "dated_in_the_future", _short(c),
                "%s date %s is after today %s" % (role, day, today)))
    if c["committer_date"] and c["author_date"] \
            and c["committer_date"] < c["author_date"]:
        found.append(finding(
            WINDOW, "committer_precedes_author", _short(c),
            "committed %s but authored %s" % (c["committer_date"], c["author_date"])))
    return found


def check_window(series, fx, repo, today=None):
    declared_first = (fx.get("window") or {}).get("first") or ""
    today = today or datetime.date.today().isoformat()
    base_ref = (fx.get("range") or {}).get("base_ref") or ""
    base_day = _day((_git(repo, "log", "-1", "--format=%cI", base_ref) or "").strip())
    found = []
    for c in series:
        found.extend(_commit_window_findings(c, base_day, declared_first, today))
    if series:
        earliest = min(_day(c["author_date"]) for c in series)
        if earliest != declared_first:
            found.append(finding(
                WINDOW, "declared_first_is_stale", _short(series[-1]),
                "the range's earliest author date is %s, the fixture declares %s"
                % (earliest, declared_first)))
    return found


# ---------------------------------------------------------------------------
# arm 3 -- message
# ---------------------------------------------------------------------------
def _subject_violations(subject):
    """Every way a subject fails to describe an engineering change."""
    out = []
    if not CONVENTIONAL_RE.match(subject):
        out.append(("non_conventional_subject",
                    "subject %r is not `type(scope): summary`" % (subject,)))
    if len(subject) < SUBJECT_MIN:
        out.append(("subject_too_short",
                    "subject is %d chars, floor is %d" % (len(subject), SUBJECT_MIN)))
    if len(subject) > SUBJECT_MAX:
        out.append(("subject_too_long",
                    "subject is %d chars, cap is %d" % (len(subject), SUBJECT_MAX)))
    if subject.endswith("."):
        out.append(("subject_trailing_period",
                    "subject %r ends in a period" % (subject,)))
    summary = subject.split(": ", 1)[-1] if ": " in subject else subject
    first = re.split(r"[^A-Za-z]+", summary.strip().lower(), maxsplit=1)[0]
    if first in JUNK_FIRST_WORDS:
        out.append(("junk_subject",
                    "subject leads with %r, which describes no change" % (first,)))
    return out


def check_message(series, fx):
    exemptions = {row.get("subject"): row
                  for row in (fx.get("subject_exemptions") or [])
                  if isinstance(row, dict)}
    subjects = set()
    found = []
    for c in series:
        subject = c["subject"]
        subjects.add(subject)
        violations = _subject_violations(subject)
        if subject in exemptions:
            if not violations:
                found.append(finding(
                    MESSAGE, "stale_exemption", _short(c),
                    "subject %r is declared as an exemption but now conforms; "
                    "delete the row" % (subject,)))
            continue
        for kind, detail in violations:
            found.append(finding(MESSAGE, kind, _short(c), detail))
    for subject in sorted(exemptions):
        if subject not in subjects:
            found.append(finding(
                MESSAGE, "orphan_exemption", "-",
                "no commit in the range carries the exempted subject %r"
                % (subject,)))
    return found


# ---------------------------------------------------------------------------
# arm 4 -- shape
# ---------------------------------------------------------------------------
def check_shape(series, fx, repo):
    found = []
    floor = (fx.get("count") or {}).get("min") or 0
    if len(series) < floor:
        found.append(finding(
            SHAPE, "series_shrank", "-",
            "the range holds %d commits, the declared floor is %d -- history "
            "was squashed, reset or dropped" % (len(series), floor)))
    for c in series:
        if len(c["parents"]) != 1:
            found.append(finding(
                SHAPE, "not_linear", _short(c),
                "commit has %d parents; the series must be linear"
                % (len(c["parents"]),)))
    base_ref = (fx.get("range") or {}).get("base_ref") or ""
    base_sha = (_git(repo, "rev-parse", "%s^{commit}" % base_ref) or "").strip()
    if series and base_sha:
        root = series[-1]
        if base_sha not in root["parents"]:
            found.append(finding(
                SHAPE, "wrong_base", _short(root),
                "the range's oldest commit does not descend from %s (%s)"
                % (base_ref, base_sha[:9])))
    return found


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------
def run_checks(repo=None, fx=None, arms=ARMS, today=None):
    """Report dict: schema errors plus every arm's findings. Never raises on a
    finding; raises only when the range itself cannot be read."""
    repo = repo or REPO
    fx = fx if fx is not None else load_fixture()
    errors = schema_errors(fx)
    base_ref = (fx.get("range") or {}).get("base_ref") or ""
    head_ref = (fx.get("range") or {}).get("head_ref") or "HEAD"
    series = read_series(repo, base_ref, head_ref)
    findings = []
    if IDENTITY in arms:
        findings.extend(check_identity(series, fx, repo))
    if WINDOW in arms:
        findings.extend(check_window(series, fx, repo, today=today))
    if MESSAGE in arms:
        findings.extend(check_message(series, fx))
    if SHAPE in arms:
        findings.extend(check_shape(series, fx, repo))
    return {"schema": SCHEMA, "repo": repo, "range": "%s..%s" % (base_ref, head_ref),
            "commits": len(series), "schema_errors": errors,
            "findings": findings, "arms": list(arms)}


def stamp(repo=None, fx=None):
    """Recompute the LIVE bounds. Only ever tightens `first` and raises the
    floor; it never invents an identity or forgives a subject."""
    repo = repo or REPO
    fx = fx if fx is not None else load_fixture()
    base_ref = (fx.get("range") or {}).get("base_ref") or ""
    head_ref = (fx.get("range") or {}).get("head_ref") or "HEAD"
    series = read_series(repo, base_ref, head_ref)
    fx.setdefault("window", {})["first"] = min(_day(c["author_date"]) for c in series)
    fx.setdefault("count", {})["min"] = max(len(series),
                                            (fx.get("count") or {}).get("min") or 0)
    return fx


def _render(report):
    lines = []
    for err in report["schema_errors"]:
        lines.append("  [schema] %s" % err)
    for f in report["findings"]:
        lines.append("  [%s] %s %s: %s" % (f["arm"], f["kind"], f["commit"], f["detail"]))
    bad = len(report["schema_errors"]) + len(report["findings"])
    head = ("commit series %s: %d commit(s), %d finding(s) across %s\n"
            % (report["range"], report["commits"], bad, ",".join(report["arms"])))
    return head + ("\n".join(lines) + "\n" if lines else "")


def main(argv=None):
    parser = argparse.ArgumentParser(description="commit-series publication gate")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--arm", action="append", choices=list(ARMS), default=None)
    parser.add_argument("--report", action="store_true", help="emit JSON")
    parser.add_argument("--stamp", action="store_true",
                        help="rewrite the fixture's window floor and count floor")
    args = parser.parse_args(argv)
    fx = load_fixture(args.fixture)
    if args.stamp:
        fx = stamp(args.repo, fx)
        with open(fixture_path(args.fixture), "w", encoding="utf-8") as fh:
            json.dump(fx, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        sys.stdout.write("stamped %s\n" % fixture_path(args.fixture))
        return 0
    try:
        report = run_checks(args.repo, fx, tuple(args.arm or ARMS))
    except ValueError as exc:
        # Deliberately rc 1, not a skip. The CI workflow pins `fetch-depth: 0`
        # precisely so the tag is present, so an unreadable range is a
        # misconfigured checkout -- and a gate that answers "I could not look"
        # with a green is the failure this whole corpus exists to prevent.
        sys.stderr.write("commit series FAILED: %s\n"
                         "  the base ref must be fetched (git fetch --tags)\n"
                         % (exc,))
        return 1
    if args.report:
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(_render(report))
    return 1 if (report["schema_errors"] or report["findings"]) else 0


if __name__ == "__main__":
    sys.exit(main())
