#!/usr/bin/env python3
"""Runnable contract check for the REPOSITORY STANDARD -- the community-health
files a public open-source repository is judged by, and the licensing claims
they make (feature 260813-modular-core-adoption, task 77).

Underscore-prefixed so `unittest discover` never COLLECTS it (the convention
`tests/_guidance_sync.py` and `tests/_doc_sync.py` already carry); a standalone,
stdlib-only checker meant to be run directly:

    python3 tests/_repo_standard.py [--json]

WHY A GATE AND NOT A CHECKLIST. `CONTRIBUTING.md`, `SECURITY.md`,
`CODE_OF_CONDUCT.md`, a changelog and the issue/PR templates are the files that
decay fastest, because nothing executes them: a contributing guide can name a
command that hangs, a NOTICE can attribute nothing while the tree vendors
adapted text, a changelog can invent a version the release channel would refuse,
and a template can ask for a field nobody reads. Each of those is a REAL defect
this repository has shipped or nearly shipped, so each gets an arm that fails.

Six arms, each exiting non-zero and naming every offender:

  A. **Every required file exists, is substantive, and is ROUTED.** A row
     declares its minimum size and the sections it must carry, so a stub that
     satisfies a file-exists check still fails; and the README must link every
     one of them, because a community file nothing points at is a file nobody
     finds.
  B. **One licence, declared identically everywhere.** `LICENSE` must be the
     Apache-2.0 text (including the patent grant the README advertises), and
     every surface that declares a licence -- the README badge and footer, the
     NOTICE, the contributing guide, the web dashboard's `package.json` -- must
     declare THAT one. Two-directional: a FOREIGN licence identifier anywhere in
     those surfaces is a failure unless an `ADOPTED` row declares it, so
     third-party text cannot be adopted silently and cannot be forbidden either.
  C. **The NOTICE is accurate in both directions.** Every adopted third-party
     text is named in `NOTICE` with its licence and canonical URL, AND the file
     that adopts it still carries its own attribution witness; a NOTICE block
     whose file has stopped adopting it fails just as loudly as a missing one.
     The copyright holder must also agree with the README's own footer.
  D. **The issue and PR templates are well-formed and load-bearing.** Frontmatter
     with a non-empty `name`/`about` (GitHub's chooser reads exactly those), a
     parsable `config.yml`, and -- the arm that matters -- if blank issues are
     DISABLED there must be a template to file instead, or the repository has
     quietly closed its own front door.
  E. **The changelog is what the release channel would accept.** Keep a Changelog
     shape, `[Unreleased]` present, every other heading a SemVer version the
     signed-tag channel regex matches, dated ISO, ordered newest-first, with only
     the six declared section names and no empty section.
  F. **The shipped tree carries no operator identity.** Delegates to the same
     `privacy.scan_personal_data` that `scripts/agentware audit` runs, resolved
     live so it hardcodes nothing (`R-LOC-03`).

Read-only. No network. Standard library only.
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------- arm A

#: (path, what it is, minimum bytes, required section markers). The size floor
#: is what stops a one-line placeholder from satisfying "the file exists"; the
#: markers are the sections whose ABSENCE would make the document misleading
#: rather than merely short.
REQUIRED = (
    ("README.md", "the public face", 2000, ()),
    ("LICENSE", "the licence text", 10000, ()),
    ("NOTICE", "the attribution notice", 200, ()),
    ("CONTRIBUTING.md", "how to contribute", 3000,
     ("## Ground rules", "## Running the checks",
      "## Commits and pull requests", "## Licensing of contributions")),
    ("SECURITY.md", "the security policy", 2000,
     ("## Supported versions", "## Reporting a vulnerability", "## Scope")),
    ("CODE_OF_CONDUCT.md", "the code of conduct", 3000,
     ("## Enforcement", "## Attribution")),
    ("CHANGELOG.md", "the changelog", 500, ("## [Unreleased]",)),
    (".github/PULL_REQUEST_TEMPLATE.md", "the pull-request template", 500, ()),
    (".github/ISSUE_TEMPLATE/bug_report.md", "the bug template", 500, ()),
    (".github/ISSUE_TEMPLATE/feature_request.md", "the feature template", 300, ()),
    (".github/ISSUE_TEMPLATE/config.yml", "the issue chooser config", 50, ()),
)

#: The community files the README must LINK. A file nothing routes to is a file
#: nobody reads, which is the failure mode a "does it exist" check cannot see.
README_ROUTES = ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md",
                 "CHANGELOG.md", "LICENSE", "NOTICE")

#: Claims the PR template must still make. Each is a rule a reviewer would
#: otherwise have to remember unaided.
PR_TEMPLATE_CLAIMS = ("R-MOD-11", "personal_data", "tests/baselines/")

MIN_PR_CHECKBOXES = 8

# --------------------------------------------------------------------- arm B

LICENCE_ID = "Apache-2.0"
LICENCE_URL = "http://www.apache.org/licenses/LICENSE-2.0"
#: Phrases that must be in `LICENSE` for it to BE the licence the tree claims --
#: including the patent grant, which the README advertises by name.
LICENCE_MARKERS = ("Apache License", "Version 2.0, January 2004",
                   "3. Grant of Patent License", "APPENDIX: How to apply")
#: (surface, needle, what the needle is). Every one of these declares the
#: licence; all of them must declare the SAME one.
LICENCE_DECLARERS = (
    ("README.md", "badge/license-Apache--2.0-blue", "the licence badge"),
    ("README.md", "[Apache-2.0](LICENSE)", "the licence footer"),
    ("NOTICE", "Apache License, Version 2.0", "the NOTICE licence line"),
    ("NOTICE", LICENCE_URL, "the NOTICE licence URL"),
    ("CONTRIBUTING.md", "[Apache-2.0](LICENSE)", "the contribution terms"),
)
#: Package manifests that carry a machine-readable licence field.
LICENCE_MANIFESTS = (("webui/package.json", "license"),)
#: (regex, SPDX id). A hit in a licence surface whose id is neither the project
#: licence nor declared by an ADOPTED row is a licensing contradiction.
FOREIGN_LICENCES = (
    (r"\bMIT License\b|\bSPDX-License-Identifier:\s*MIT\b", "MIT"),
    (r"\bGNU General Public License\b|\bA?GPL-[23]\.0\b", "GPL"),
    (r"\bBSD [23]-Clause\b", "BSD"),
    (r"\bMozilla Public License\b|\bMPL-2\.0\b", "MPL-2.0"),
    (r"\bCreative Commons Attribution 4\.0\b|\bCC[ -]BY[ -]4\.0\b", "CC-BY-4.0"),
    (r"\bThe Unlicense\b|\bWTFPL\b", "public-domain-dedication"),
    (r"\bAll rights reserved\b", "proprietary"),
)
#: The surfaces arm B reads for both halves.
LICENCE_SURFACES = ("README.md", "NOTICE", "CONTRIBUTING.md", "SECURITY.md",
                    "CODE_OF_CONDUCT.md", "CHANGELOG.md")

# --------------------------------------------------------------------- arm C

#: (file that adopts it, work, SPDX id, canonical URL, witness the FILE must
#: still carry). Two-directional by construction: the witness proves the file
#: still adopts the text, the NOTICE fields prove the attribution is published.
ADOPTED = (
    ("CODE_OF_CONDUCT.md", "Contributor Covenant, version 2.1", "CC-BY-4.0",
     "https://www.contributor-covenant.org/version/2/1/code_of_conduct.html",
     "Contributor Covenant"),
)
COPYRIGHT_RE = re.compile(r"Copyright (\d{4}) (.+)")
README_COPYRIGHT_RE = re.compile(r"© (\d{4}) ([^—\n]+)")

# --------------------------------------------------------------------- arm E

#: Mirrors `agentware_lib.core.pkgchannel.PKG_CHANNEL_RE` minus its `v` prefix:
#: a changelog heading that the signed-tag channel would refuse is a version
#: that can never ship. The agreement is asserted by the owning gate test rather
#: than by importing the package, so this checker stays standalone.
CHANGELOG_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
CHANGELOG_HEADING_RE = re.compile(r"^## \[([^\]]+)\](?: - (\S+))?\s*$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KAC_SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
UNRELEASED = "Unreleased"


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def _read_or_none(root, rel):
    try:
        return _read(root, rel)
    except (OSError, UnicodeDecodeError):
        return None


def check_required(root):
    """A. every required file exists, is substantive, and is routed."""
    errors = []
    for rel, what, floor, sections in REQUIRED:
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            errors.append("A: %s (%s) is missing" % (rel, what))
            continue
        size = os.path.getsize(path)
        if size < floor:
            errors.append("A: %s (%s) is %d bytes, under the %d-byte floor -- a "
                          "stub satisfies 'the file exists' and nothing else"
                          % (rel, what, size, floor))
            continue
        errors.extend(_check_case(root, rel))
        body = _read(root, rel)
        for marker in sections:
            if marker not in body:
                errors.append("A: %s is missing the %r section" % (rel, marker))
    errors.extend(_check_routes(root))
    errors.extend(_check_pr_template(root))
    return errors


def _check_case(root, rel):
    """`os.path.isfile` is CASE-INSENSITIVE on macOS and case-sensitive on the
    Linux runner GitHub uses, so a file named `pull_request_template.md` here
    satisfies a check for `PULL_REQUEST_TEMPLATE.md` locally and vanishes in CI
    -- and GitHub's own tooling only recognises the exact names. Compare against
    the directory listing so the answer does not depend on the filesystem.
    """
    parent, name = os.path.split(os.path.join(root, rel))
    try:
        listing = os.listdir(parent)
    except OSError:
        return ["A: %s has no parent directory" % rel]
    if name in listing:
        return []
    near = [n for n in listing if n.lower() == name.lower()]
    return ["A: %s is on disk as %r -- the case must match exactly, or the "
            "file is invisible on a case-sensitive filesystem" % (rel, near[0])
            for _n in near[:1]]


def _check_routes(root):
    readme = _read_or_none(root, "README.md")
    if readme is None:
        return []
    return ["A: README.md does not link %s -- a community file nothing points "
            "at is one nobody finds" % rel
            for rel in README_ROUTES if "(%s)" % rel not in readme]


def _check_pr_template(root):
    body = _read_or_none(root, ".github/PULL_REQUEST_TEMPLATE.md")
    if body is None:
        return []
    errors = []
    boxes = body.count("- [ ]")
    if boxes < MIN_PR_CHECKBOXES:
        errors.append("A: the PR template has %d checkboxes, under the %d "
                      "minimum" % (boxes, MIN_PR_CHECKBOXES))
    for claim in PR_TEMPLATE_CLAIMS:
        if claim not in body:
            errors.append("A: the PR template no longer names %r" % claim)
    return errors


def check_licence(root):
    """B. one licence, declared identically on every surface that declares one."""
    errors = []
    text = _read_or_none(root, "LICENSE")
    if text is None:
        return ["B: LICENSE is missing or unreadable"]
    for marker in LICENCE_MARKERS:
        if marker not in text:
            errors.append("B: LICENSE does not carry %r -- it is not the %s "
                          "text the tree claims" % (marker, LICENCE_ID))
    for rel, needle, what in LICENCE_DECLARERS:
        body = _read_or_none(root, rel)
        if body is None:
            errors.append("B: %s is missing, so %s declares nothing"
                          % (rel, what))
        elif needle not in body:
            errors.append("B: %s no longer declares %s (%r)"
                          % (rel, what, needle))
    errors.extend(_check_manifests(root))
    errors.extend(_check_foreign(root))
    return errors


def _check_manifests(root):
    errors = []
    for rel, field in LICENCE_MANIFESTS:
        body = _read_or_none(root, rel)
        if body is None:
            continue
        try:
            declared = json.loads(body).get(field)
        except ValueError as exc:
            errors.append("B: %s is not parsable JSON (%s)" % (rel, exc))
            continue
        if declared != LICENCE_ID:
            errors.append("B: %s declares %r in its %r field, not %r"
                          % (rel, declared, field, LICENCE_ID))
    return errors


def _check_foreign(root):
    """A foreign licence id is legal ONLY where an ADOPTED row declares it."""
    allowed = set(row[2] for row in ADOPTED)
    errors = []
    for rel in LICENCE_SURFACES:
        body = _read_or_none(root, rel)
        if body is None:
            continue
        for pattern, spdx in FOREIGN_LICENCES:
            if spdx in allowed or not re.search(pattern, body):
                continue
            errors.append(
                "B: %s names the foreign licence %s, which no ADOPTED row "
                "declares -- adopt it in the ledger or stop naming it"
                % (rel, spdx))
    return errors


def check_notice(root):
    """C. the NOTICE is accurate in both directions."""
    notice = _read_or_none(root, "NOTICE")
    if notice is None:
        return ["C: NOTICE is missing or unreadable"]
    errors = []
    for rel, work, spdx, url, witness in ADOPTED:
        body = _read_or_none(root, rel)
        if body is None:
            errors.append("C: NOTICE attributes %s to %s, which is not in the "
                          "tree -- retire the block or restore the file"
                          % (work, rel))
            continue
        if witness not in body:
            errors.append("C: %s no longer carries its %r attribution, but "
                          "NOTICE still publishes one for it" % (rel, witness))
        for field, what in ((rel, "the adopting file"), (work, "the work"),
                            (url, "the canonical URL")):
            if field not in notice:
                errors.append("C: NOTICE does not name %s (%r) for the %s "
                              "attribution" % (what, field, spdx))
    errors.extend(_check_copyright(root, notice))
    return errors


def _check_copyright(root, notice):
    match = COPYRIGHT_RE.search(notice)
    if not match:
        return ["C: NOTICE carries no `Copyright <year> <holder>` line"]
    readme = _read_or_none(root, "README.md") or ""
    found = README_COPYRIGHT_RE.search(readme)
    if not found:
        return ["C: README.md carries no `© <year> <holder>` footer to "
                "agree with NOTICE"]
    if (found.group(1), found.group(2).strip()) != (match.group(1),
                                                    match.group(2).strip()):
        return ["C: the copyright holder disagrees -- NOTICE says %r %r, "
                "README.md says %r %r" % (match.group(1), match.group(2).strip(),
                                          found.group(1), found.group(2).strip())]
    return []


def _frontmatter(body):
    """The leading `--- ... ---` block as a dict, or None when absent."""
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip("'\"")
    return None


def _yaml_flat(body):
    """`key: value` pairs from a FLAT yaml document, ignoring comments."""
    fields = {}
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[:1].isspace() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def check_templates(root):
    """D. the issue and PR templates are well-formed and load-bearing."""
    tdir = os.path.join(root, ".github", "ISSUE_TEMPLATE")
    if not os.path.isdir(tdir):
        return ["D: .github/ISSUE_TEMPLATE/ is missing"]
    errors = []
    templates = sorted(n for n in os.listdir(tdir) if n.endswith(".md"))
    for name in templates:
        rel = ".github/ISSUE_TEMPLATE/%s" % name
        fields = _frontmatter(_read(root, rel))
        if fields is None:
            errors.append("D: %s has no closed `---` frontmatter block, so "
                          "GitHub's chooser will not offer it" % rel)
            continue
        for key in ("name", "about"):
            if not fields.get(key):
                errors.append("D: %s frontmatter has no non-empty %r" % (rel, key))
    errors.extend(_check_chooser(root, templates))
    return errors


def _check_chooser(root, templates):
    body = _read_or_none(root, ".github/ISSUE_TEMPLATE/config.yml")
    if body is None:
        return ["D: .github/ISSUE_TEMPLATE/config.yml is missing"]
    blank = _yaml_flat(body).get("blank_issues_enabled")
    if blank not in ("true", "false"):
        return ["D: config.yml does not set blank_issues_enabled to true or "
                "false (found %r)" % blank]
    if blank == "false" and not templates:
        return ["D: blank issues are disabled and there is no template to file "
                "instead -- the repository has closed its own front door"]
    return []


def check_changelog(root):
    """E. the changelog is what the signed-tag release channel would accept."""
    body = _read_or_none(root, "CHANGELOG.md")
    if body is None:
        return ["E: CHANGELOG.md is missing or unreadable"]
    errors = []
    if not body.lstrip().startswith("# Changelog"):
        errors.append("E: CHANGELOG.md does not open with `# Changelog`")
    headings = [(i, m) for i, m in
                ((i, CHANGELOG_HEADING_RE.match(line))
                 for i, line in enumerate(body.splitlines(), 1)) if m]
    labels = [m.group(1) for _i, m in headings]
    if UNRELEASED not in labels:
        errors.append("E: CHANGELOG.md has no `## [Unreleased]` section")
    errors.extend(_check_versions(headings))
    errors.extend(_check_sections(body))
    return errors


def _check_versions(headings):
    errors, seen = [], []
    for line, match in headings:
        label, date = match.group(1), match.group(2)
        if label == UNRELEASED:
            if date:
                errors.append("E: [Unreleased] carries a release date (line %d)"
                              % line)
            continue
        if not CHANGELOG_VERSION_RE.match(label):
            errors.append("E: CHANGELOG.md:%d version %r is not the X.Y.Z the "
                          "signed-tag channel accepts" % (line, label))
            continue
        if not date or not ISO_DATE_RE.match(date):
            errors.append("E: CHANGELOG.md:%d version %s has no ISO date"
                          % (line, label))
        seen.append(tuple(int(p) for p in label.split(".")))
    if seen != sorted(seen, reverse=True):
        errors.append("E: CHANGELOG.md versions are not ordered newest-first")
    return errors


def _check_sections(body):
    errors = []
    current, filled = None, True
    for line in body.splitlines():
        if line.startswith("### "):
            if current and not filled:
                errors.append("E: the %r section is empty" % current)
            current, filled = line[4:].strip(), False
            if current not in KAC_SECTIONS:
                errors.append("E: %r is not a Keep a Changelog section (%s)"
                              % (current, ", ".join(KAC_SECTIONS)))
        elif line.startswith("## "):
            if current and not filled:
                errors.append("E: the %r section is empty" % current)
            current, filled = None, True
        elif current and line.strip():
            filled = True
    if current and not filled:
        errors.append("E: the %r section is empty" % current)
    return errors


def check_personal_data(root, needles=None):
    """F. the shipped tree carries no operator identity (`R-LOC-03`).

    Delegates to the SAME scanner `scripts/agentware audit` runs rather than
    re-implementing it, and resolves the identity live so this file hardcodes
    none. `needles` is injectable so the owning test can prove the arm bites
    without depending on whoever runs it having a knowledge base.
    """
    try:
        sys.path.insert(0, os.path.join(root, "scripts"))
        import agentware_lib.core.config as config
        import agentware_lib.knowledge.privacy as privacy
    except ImportError as exc:                      # pragma: no cover
        return ["F: the personal-data scanner is unavailable (%s)" % exc]
    if needles is None:
        needles = privacy.operator_identity_strings(config.resolve_knowledge_dir())
    return ["F: %s" % hit for hit in privacy.scan_personal_data(root, needles)]


def problems(root=None):
    root = root or _REPO_ROOT
    found = []
    for check in (check_required, check_licence, check_notice, check_templates,
                  check_changelog, check_personal_data):
        found.extend(check(root))
    return found


def main(argv):
    as_json = "--json" in argv
    root = _REPO_ROOT
    if "--tree" in argv:
        root = argv[argv.index("--tree") + 1]
    found = problems(root)
    if as_json:
        print(json.dumps({"problems": found, "ok": not found}, indent=2))
    elif found:
        print("REPO STANDARD: %d problem(s)" % len(found))
        for line in found:
            print("  " + line)
    else:
        print("REPO STANDARD: ok")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
