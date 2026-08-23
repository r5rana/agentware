"""agentware_lib.ops.fleet.fleettemplates -- the per-team template contract T1..T5 (layer: ops).

Owns: the per-team template-contract lint (feature 260721 follow-up, R-ORG-09) --
the required-frontmatter vocabularies (TEMPLATE_REQUIRED_FM / TEMPLATE_ORG_FM /
SKILL_TEMPLATE_FM), the filename-stem classifier (_template_kind -> 'skill' |
'entry' | None), the read-only linter itself (lint_templates = rules T1
frontmatter present, T2 required entry fields declared, T3 org fields declared,
T4 skill name+description, T5 no leftover conflict markers), the
`templates lint` handler cmd_templates_lint and the `templates` registrar
register_templates.
Does NOT own: the committed telemetry sink (agentware_lib.ops.fleet.fleetshard);
the shared-write guard + org read guards (agentware_lib.ops.fleet.fleetguard);
the fleet reporter (agentware_lib.ops.fleet.fleetreport); the O1..O13 org lint
that FOLDS this contract in per team (agentware_lib.ops.orglint, which calls
lint_templates through the package alias); the frontmatter parser itself
(agentware_lib.core.frontmatter); the templates a KB actually ships.
Layer deps: ops -> core only (config/frontmatter). No sibling import inside this
package -- fleettemplates is self-contained, which is what lets the org linter
depend on it without pulling in the sink.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Determinism: lint_templates is a pure function of the on-disk corpus -- a team
with NO templates/ dir is a clean no-op (ok:true, checked=0), files are walked in
sorted order, and the emitted {rule, file, line, message} rows are a byte contract
the org linter re-surfaces verbatim.
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter


TEMPLATE_REQUIRED_FM = ("id", "title", "category", "tags", "summary")


TEMPLATE_ORG_FM = ("origin", "team")


SKILL_TEMPLATE_FM = ("name", "description")


def _template_kind(stem):
    """Classify a template filename stem → 'skill' | 'entry' | None (not a template)."""
    s = stem.lower()
    if "skill" in s:
        return "skill"
    for kind in ("learning", "decision", "project", "configuration", "config",
                 "reference", "prompt"):
        if kind in s:
            return "entry"
    if s.endswith("-template") or s.endswith("_template") or s == "template":
        return "entry"  # unknown but clearly a template → hold to the entry contract
    return None


def _conflict_marker_line(text):
    """1-based line number of the FIRST git conflict marker, else None (T5).

    Deliberately NOT `orgkeys.ORG_CONFLICT_MARKER_RE`, which the org linter's O6
    uses: that pattern also matches a `=======` divider, and a markdown template
    may legitimately carry a setext underline of seven or more `=`. The two
    predicates DISAGREE on that input, so folding them into one shared scanner
    would be a silent behaviour change dressed as de-duplication — the reason
    this stays a local scanner with a comment rather than an import.
    """
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("<<<<<<<") or line.startswith(">>>>>>>"):
            return i
    return None


def _declared_frontmatter_keys(text):
    """The key names a frontmatter block DECLARES (value may be a placeholder).

    A template's keys are declared-but-EMPTY by construction, so a YAML parse
    that drops empty values cannot answer T2/T3/T4 at all; this reads the block
    as lines and asks only which names are present. A commented-out key does not
    count as declared, and an indented key does.
    """
    declared = set()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if m:
        for ln in m.group(1).splitlines():
            if ":" in ln and not ln.strip().startswith("#"):
                declared.add(ln.split(":", 1)[0].strip())
    return declared


def _lint_entry_contract(rel, declared, strict, err, warn):
    """T2 + T3 for one ENTRY template's declared key set.

    T2 and T3 are independent: a template missing both reports both, in that
    order, and T3 is the ONLY rule in the contract whose severity moves with
    --strict.
    """
    missing_req = [k for k in TEMPLATE_REQUIRED_FM if k not in declared]
    if missing_req:
        err("T2", rel, 1,
            "entry template missing required frontmatter key(s): %s — a filled "
            "entry would fail REQUIRED_FIELDS / index validate"
            % ", ".join(missing_req))
    missing_org = [k for k in TEMPLATE_ORG_FM if k not in declared]
    if missing_org:
        # Org fields: hard error in strict, warning otherwise (path-inference can
        # backfill them, but an explicit template is the deterministic contract).
        msg = ("entry template missing org frontmatter key(s): %s — an org entry "
               "copied from it relies on path-inference for O1 instead of being "
               "explicit" % ", ".join(missing_org))
        if strict:
            err("T3", rel, 1, msg)
        else:
            warn("T3", rel, 1, msg)


def _lint_template_file(tdir, fn, kind, strict, err, warn):
    """T1/T4/T5 + the entry contract for ONE recognized template file.

    Rule ORDER is the contract: T5 is scanned on the RAW text before the
    frontmatter block is parsed, so a template that is both conflicted and
    frontmatter-less reports T5 and then T1 — T1's `return` would swallow T5
    if the two were reordered.
    """
    rel = "templates/%s" % fn
    try:
        with open(os.path.join(tdir, fn), "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        err("T1", rel, 0, "cannot read template: %s" % e)
        return
    # T5 — no stray conflict markers.
    marker = _conflict_marker_line(text)
    if marker is not None:
        err("T5", rel, marker, "git conflict marker in template")
    fields, _body = frontmatter.split_frontmatter(text)
    # T1 — a template MUST open with a YAML frontmatter block.
    if fields is None:
        err("T1", rel, 1,
            "template has NO YAML frontmatter block — a legacy blockquote-only "
            "template produces entries that cannot be org-validated. Add a "
            "'---'…'---' block declaring the required keys.")
        return
    declared = _declared_frontmatter_keys(text)
    if kind == "skill":
        missing = [k for k in SKILL_TEMPLATE_FM if k not in declared]
        if missing:
            err("T4", rel, 1,
                "skill template missing frontmatter key(s): %s (agentskills.io "
                "requires name + description)" % ", ".join(missing))
        return
    _lint_entry_contract(rel, declared, strict, err, warn)


def lint_templates(kdir, strict=False):
    """Lint every templates/*.md against the per-kind contract. Read-only.

    Returns {ok, errors[{rule,file,line,message}], warnings[], checked}. A team with
    NO templates/ dir is a clean no-op (ok:true, checked=0). Deterministic/stdlib.
    Rule ids: T1 frontmatter present; T2 required entry fields declared;
    T3 org fields (origin/team) declared; T4 skill name+description; T5 no leftover
    conflict markers.

    `checked` counts files the CLASSIFIER recognized, so it is incremented before
    the file is opened: an unreadable template is checked-and-failed, not skipped.
    """
    errors, warnings = [], []
    checked = 0

    def err(rule, file, line, message):
        errors.append({"rule": rule, "file": file, "line": line, "message": message})

    def warn(rule, file, line, message):
        warnings.append({"rule": rule, "file": file, "line": line, "message": message})

    tdir = os.path.join(kdir or "", "templates")
    if not os.path.isdir(tdir):
        return {"ok": True, "errors": [], "warnings": [], "checked": 0}
    for fn in sorted(os.listdir(tdir)):
        if not fn.endswith(".md"):
            continue
        kind = _template_kind(fn[:-3])
        if kind is None:
            continue  # not a recognized template (e.g. a README) — skip
        checked += 1
        _lint_template_file(tdir, fn, kind, strict, err, warn)
    ok = len(errors) == 0
    return {"ok": ok, "errors": errors, "warnings": warnings, "checked": checked}


def cmd_templates_lint(args):
    """`agentware templates lint` — enforce the per-team template contract."""
    strict = bool(getattr(args, "strict", False))
    as_json = getattr(args, "format", "text") == "json"
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    result = lint_templates(kdir, strict=strict)
    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("TEMPLATES LINT %s (%d template(s) checked, %d error(s), %d warning(s)):"
              % ("PASSED" if result["ok"] else "FAILED", result["checked"],
                 len(result["errors"]), len(result["warnings"])))
        for e in result["errors"]:
            print("  [%s] %s:%s — %s" % (e["rule"], e["file"], e["line"], e["message"]))
        for w in result["warnings"]:
            print("  (warn %s) %s:%s — %s" % (w["rule"], w["file"], w["line"], w["message"]))
    return 0 if result["ok"] else 1


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("templates", 3100, "register_templates"),
)


def register_templates(sub, fmt_parent):
    # templates --------------------------------------------------------------
    # Per-team template contract (feature 260721 follow-up): a team may keep its own
    # entry templates, but each must carry the minimum frontmatter so a filled entry
    # is org-valid. `templates lint` enforces T1..T5.
    p_tpl = sub.add_parser("templates", help="team template tooling (lint)")
    tpl_sub = p_tpl.add_subparsers(dest="templates_command", metavar="<subcommand>")
    tpl_sub.required = True
    p_tpl_lint = tpl_sub.add_parser(
        "lint", parents=[fmt_parent],
        help="enforce the per-team template contract (T1..T5)")
    p_tpl_lint.add_argument("--strict", action="store_true",
                            help="treat org-field warnings (T3) as hard failures")
    p_tpl_lint.set_defaults(func=cmd_templates_lint)


_EXPORTS = [
    'TEMPLATE_REQUIRED_FM',
    'TEMPLATE_ORG_FM',
    'SKILL_TEMPLATE_FM',
    '_template_kind',
    '_conflict_marker_line',
    '_declared_frontmatter_keys',
    '_lint_entry_contract',
    '_lint_template_file',
    'lint_templates',
    'cmd_templates_lint',
]
