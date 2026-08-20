"""agentware_lib.ops.templaterefresh — migration 003's body: refresh INSTALLED templates (layer: ops).

Owns: the refresh of an ALREADY-INSTALLED template corpus in an operator's
knowledge dir — the pristine-shipped digest table (LEGACY_TEMPLATE_DIGESTS,
which is what lets "unmodified" be decided rather than guessed), the per-file
classifier (_classify_template -> refresh | current | modified | foreign |
unreadable | source-not-conformant), the file-level refresh
(refresh_installed_templates), the change-only notice (_report) and the
migration entry point run_template_refresh.
Does NOT own: the migration REGISTRY, the schema sidecar or the runner that calls
this (agentware_lib.features.migratecmd — one schema authority, per its interlock
docstring); the FIRST install of the corpus into a new knowledge dir
(agentware_lib.features.setupcmd's `init`, whose copy is deliberately
create-if-absent); the template CONTRACT this makes satisfiable
(agentware_lib.ops.fleet.fleettemplates' T1..T5 `templates lint`); the shipped
templates themselves (`templates/*.md` at the repo root); the frontmatter grammar
(agentware_lib.core.frontmatter); the atomic write (agentware_lib.ops.planauthor.
planedit).
Layer deps: ops.templaterefresh -> core (frontmatter, paths) DOWNWARD + the
SAME-LAYER sibling ops.planauthor.planedit (for the atomic write). Nothing imports
it at module level: features.migratecmd resolves it LAZILY at call time, so the
CLI's hot import path never pays for it.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

THE DEFECT THIS EXISTS FOR
--------------------------
`init` installs `templates/*.md` into the knowledge dir so the operator's working
instance is self-contained, and it copies each file ONLY when the destination does
not already exist (`if not os.path.exists(dst)`) — correct, because clobbering a
live knowledge base on every `init` is the worse failure. The consequence is that
a knowledge base created BEFORE the template corpus was reconciled (task 51 gave
each template a YAML frontmatter block and added the decision template) keeps its
frontmatter-less originals forever: `templates lint` reports a TRUE T1 finding
("template has NO YAML frontmatter block") and nothing in the package can clear
it. A finding with no path to clearing it trains an operator to ignore the lint.

WHY THE DETECTOR IS TWO QUESTIONS, NOT ONE
------------------------------------------
The shape alone ("no opening `---`") is NOT sufficient authority to overwrite a
file in someone else's knowledge base. An operator who edited their own copy —
and never added frontmatter — has a legacy-SHAPED template that is nonetheless
THEIRS, and silently replacing it with the package's is the worse failure of the
two this step is between. So a file is refreshed only when BOTH hold:

  1. it is legacy-shaped (carries no frontmatter block), AND
  2. its bytes are byte-identical to a version this package ITSELF shipped.

(2) is decided against LEGACY_TEMPLATE_DIGESTS, a table of the sha256 of every
frontmatter-less generation of each template this package has ever installed,
read out of the repository's own history and pinned here by value (a clone may be
shallow, so the table cannot be derived from git at run time). The table is the
whole authority for the word "unmodified": with it, "this is our file, unedited"
is a fact; without it, it would be an assumption about what an operator did.

The table is deliberately per-FILENAME and MULTI-VALUED, because one generation
is not enough — measured, not supposed: the knowledge base that motivated this
step carries `learning-template.md` and `project-template.md` from the generation
that the reconciliation replaced, and `skill-template.md` from an OLDER one still
(the skill template gained its frontmatter one commit earlier, with the skills
pack). A single "the last legacy version" pin would have left that file behind.

The classification FAILS SAFE in the direction that matters: a pristine template
from a generation nobody enumerated is read as `modified` and left alone. That
costs an unclearable lint finding — the state this step is improving — where the
opposite error costs an operator their edits.
"""

import hashlib
import os
import sys

import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.paths as paths
import agentware_lib.ops.planauthor.planedit as planedit


#: The knowledge-dir (and package-root) subdirectory holding the entry templates.
TEMPLATES_DIRNAME = "templates"


#: sha256 of every FRONTMATTER-LESS generation of each shipped template, by
#: filename. A file matching one of these is provably this package's own,
#: unedited, and therefore safe to refresh; anything else legacy-shaped is the
#: operator's and is left alone.
#:
#: Provenance (read from this repository's own history — `git show
#: <commit>:templates/<file> | shasum -a 256`):
#:   fbaa142 "Hello, world"            — the original three templates
#:   98ec297 "default skills pack"     — replaced skill-template.md with a
#:                                       frontmatter (agentskills.io) one; left
#:                                       learning/project untouched
#:   6ab22df "reconcile the corpus"    — gave learning/project frontmatter and
#:                                       added decision-template.md; this is the
#:                                       generation the step refreshes TO, so its
#:                                       digests must NEVER appear below
#: decision-template.md has no row: it was born with frontmatter, so no knowledge
#: base can hold a legacy copy of it.
LEGACY_TEMPLATE_DIGESTS = {
    "learning-template.md": frozenset((
        # fbaa142 and 98ec297 ship the same blob.
        "925c1269e59adb6f8930d0fa0cb945693e1247a6ed2c53973d6775da09618099",
    )),
    "project-template.md": frozenset((
        # fbaa142 and 98ec297 ship the same blob.
        "81aaf819d0bb7d86d87a832297df8a297d783076611813829aae9a6d5f930593",
    )),
    "skill-template.md": frozenset((
        # fbaa142 only — 98ec297 already replaced it with a conformant one.
        "ed0645edae0c627cbd31dc1e324f6149ee8aac3af986a8ff7a5ef6b1fc7dacf4",
    )),
}


#: Every outcome _classify_template can return, and the summary key it lands in.
#: Declared as a table so the summary shape and the classifier cannot drift: the
#: owning test asserts every value here is a key of the empty summary. Named for
#: THIS step deliberately — `ops.orgmigrate` already publishes a differently-shaped
#: `_OUTCOME_KEYS`, and the constant differ records a shared name by its LONGEST
#: literal, so reusing the spelling would pin a multi-literal row that describes
#: neither map.
_REFRESH_OUTCOME_KEYS = {"refresh": "refreshed", "current": "current",
                         "modified": "modified", "foreign": "foreign",
                         "unreadable": "unreadable",
                         "source-not-conformant": "source_not_conformant"}


def shipped_templates_dir():
    """The package's own template corpus directory (the refresh SOURCE)."""
    return os.path.join(paths.REPO_ROOT, TEMPLATES_DIRNAME)


def _empty_summary():
    """The step's report shape. Every key is present whether or not the step
    engaged, so a caller (and the journal) never has to branch on absence."""
    return {"engaged": False, "templates_dir": None, "reason": None,
            "refreshed": [], "current": [], "modified": [], "foreign": [],
            "unreadable": [], "source_not_conformant": []}


def _sha256_bytes(data):
    """sha256 hex of `data`. Hashed over BYTES, so the comparison is with what
    git recorded and not with what a decoder chose to make of it."""
    return hashlib.sha256(data).hexdigest()


def _has_frontmatter(text):
    """True when `text` opens with a YAML frontmatter block.

    Asks the frontmatter module rather than testing for a leading `---`, so this
    step and `templates lint`'s T1 rule answer the SAME question with the same
    parser — a template this refuses to touch is exactly a template T1 passes.
    """
    fields, _body = frontmatter.split_frontmatter(text)
    return fields is not None


def _classify_template(src, dst):
    """Decide what to do with ONE installed template. Pure apart from two reads.

    Returns one of:
      `foreign`                — no shipped counterpart: an operator's own
                                 template, never ours to refresh;
      `unreadable`             — the installed file cannot be read or decoded;
      `source-not-conformant`  — the SHIPPED file is itself frontmatter-less, so
                                 refreshing would rewrite an operator's file for
                                 no gain (a package regression, not their fault);
      `current`                — the installed file already carries frontmatter;
      `refresh`                — legacy-shaped AND byte-identical to a generation
                                 this package shipped;
      `modified`               — legacy-shaped but not one of ours: left alone.
    """
    if not os.path.isfile(src):
        return "foreign"
    try:
        with open(dst, "rb") as handle:
            raw = handle.read()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "unreadable"
    if _has_frontmatter(text):
        return "current"
    try:
        with open(src, "r", encoding="utf-8") as handle:
            source_text = handle.read()
    except (OSError, UnicodeDecodeError):
        return "unreadable"
    if not _has_frontmatter(source_text):
        return "source-not-conformant"
    known = LEGACY_TEMPLATE_DIGESTS.get(os.path.basename(dst), frozenset())
    return "refresh" if _sha256_bytes(raw) in known else "modified"


def refresh_installed_templates(kdir):
    """Refresh every provably-pristine legacy template under `<kdir>/templates`.

    Idempotent: a refreshed file carries frontmatter, so the next run classifies
    it `current` and writes nothing. Non-destructive: the ONLY files written are
    the ones proven byte-identical to a version this package shipped. A knowledge
    dir with no `templates/` directory is a clean no-op — `init` creates it, and
    materialising one here would install a corpus into a knowledge base that has
    deliberately never had one.
    """
    summary = _empty_summary()
    tdir = os.path.join(kdir or "", TEMPLATES_DIRNAME)
    if not os.path.isdir(tdir):
        summary["reason"] = "no installed templates/ directory"
        return summary
    src_dir = shipped_templates_dir()
    if not os.path.isdir(src_dir):
        summary["reason"] = "package template corpus missing: %s" % src_dir
        return summary
    summary["engaged"] = True
    summary["templates_dir"] = tdir
    for fn in sorted(os.listdir(tdir)):
        if not fn.endswith(".md"):
            continue
        src, dst = os.path.join(src_dir, fn), os.path.join(tdir, fn)
        outcome = _classify_template(src, dst)
        if outcome == "refresh":
            with open(src, "r", encoding="utf-8") as handle:
                planedit._atomic_write(dst, handle.read())
        summary[_REFRESH_OUTCOME_KEYS[outcome]].append(fn)
    return summary


def _report(summary):
    """Announce ONE line on stderr iff the step actually rewrote something.

    The auto-apply trigger runs this unattended on the first KB write after an
    upgrade, and it rewrites files in the operator's knowledge dir — a mutation
    nobody asked for in that moment deserves a line saying it happened. The
    negative half is the one that matters: a knowledge base with no templates,
    with conformant ones, or with only operator-modified ones prints NOTHING.

    `modified` is deliberately NOT in the predicate. It is a STANDING condition —
    the file is still there on the next run and on every run after that, because
    this step never rewrites it — so putting it in the predicate would make the
    notice permanent, which is how an operator learns to stop reading the
    channel. It is reported INSIDE the line when the step did act, and owned as a
    finding by `templates lint` T1, which is the surface that can name the file.
    """
    if not summary["refreshed"]:
        return
    sys.stderr.write(
        "migration-003: refreshed %d installed template(s) from the shipped "
        "corpus, left %d operator-modified one(s) alone\n"
        % (len(summary["refreshed"]), len(summary["modified"])))


def run_template_refresh(kdir):
    """Migration 003's body: bring pristine legacy templates up to the shipped
    corpus. Idempotent + non-destructive; see refresh_installed_templates."""
    summary = refresh_installed_templates(kdir)
    _report(summary)
    return summary


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "TEMPLATES_DIRNAME",
    "LEGACY_TEMPLATE_DIGESTS",
    "shipped_templates_dir",
    "_classify_template",
    "_report",
    "refresh_installed_templates",
    "run_template_refresh",
]
