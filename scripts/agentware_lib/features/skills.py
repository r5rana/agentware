"""agentware_lib.features.skills -- the AUTHORED skill catalog + install/vet surface (layer: features).

Owns: the in-package authored skill catalog and the trusted-in-repo installer --
the catalog path constant (SKILLS_CATALOG_PATH); the bundled-script/prose danger
signatures scanned by the static vetter (_SKILL_SCRIPT_DANGER / _SKILL_PROSE_DANGER
/ _SKILL_TEXT_EXTS); the catalog loader (load_skill_catalog / _catalog_skill); the
skill-frontmatter reader (read_skill_frontmatter); the pinned-ref resolver
(_pinned_ref); the static skill-vetter (vet_skill_dir); and the `skill`
list/search/validate/add/remove/roster-injectable handlers (cmd_skill_list /
cmd_skill_search / _validate_skill_dir / cmd_skill_validate / _copy_skill_tree /
cmd_skill_add / cmd_skill_remove / cmd_skill_roster_injectable).

Does NOT own: the skill PROPOSAL/PROMOTION lifecycle (-> agentware_lib.features.proposals)
and the package self-update (cmd_update); the `skill` PARSER REGISTRAR (register_skill)
-- it registers BOTH catalog AND proposal sub-verbs, so it was homed WITH the proposal
handlers in agentware_lib.features.proposals (wave-6 seam 21), reaching this module's 6
catalog handlers via the `_skills` sibling alias; it is resolved by the assembler via
_PARSER_REGISTRATION, NOT re-exported.

Layer deps: features.skills -> core (config, frontmatter, paths, gitio._git) +
knowledge (index), all DOWNWARD. read_skill_frontmatter is CONSUMED UP by
knowledge (build_index_from_frontmatter -> rebuild_kb) and by features.proposals
(near_duplicate_proposal) via a FUNCTION-LOCAL (lazy) import once those move -- one
of the five measured irreducible upward SYMBOL edges M12 permits (knowledge->features).
skills itself imports nothing upward, so no MODULE-LEVEL cycle exists.

A LEAF: nothing imports it at module level (fan-in 0 until knowledge/proposals add
their lazy edges); the scripts/agentware entrypoint re-exports its _EXPORTS BY NAME
so the legacy load_cli oracle (CLI.load_skill_catalog / CLI.read_skill_frontmatter /
CLI.vet_skill_dir / ...) resolves them in the entrypoint namespace; register_skill
(now in features.proposals, wave-6 seam 21) reaches the 6 catalog handlers via the
`_skills` sibling alias, and the entrypoint keeps re-exporting the cmd_skill_* handlers
for the leaf-dispatch contract.

Determinism: read-only w.r.t. the index EXCEPT the explicit install writes in
cmd_skill_add/cmd_skill_remove, which go through index.save_index / the CLI (the
sole index writer, R-KB-01). vet_skill_dir treats bundled skill scripts as
UNTRUSTED and only STATIC-scans them (never executes), and `skill add` refuses a
remote source unless vetted + --allow-external (R-SEC-02).
Fan-in: 4 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import re
import shutil
import subprocess  # noqa: F401 -- resolves the by-name-imported gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.paths as paths
import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.skillentry as skillentry
from agentware_lib.core.gitio import _git


SKILLS_CATALOG_PATH = os.path.join(paths.REPO_ROOT, "catalog", "skills.json")

# Danger signatures scanned in BUNDLED SCRIPTS (executable files), not in the
# guidance prose of a SKILL.md (security skills legitimately *discuss* these).
_SKILL_SCRIPT_DANGER = (
    (re.compile(r"\b(curl|wget)\b[^\n|]*\|[^\n]*\b(sh|bash|zsh|python[0-9.]*)\b"),
     "pipe-to-shell (downloads then executes remote code)"),
    (re.compile(r"/dev/tcp/"), "raw TCP socket (possible exfiltration channel)"),
    (re.compile(r"\b(nc|ncat|netcat)\b\s+-"), "netcat invocation (network egress)"),
    (re.compile(r"base64\s+(-d|--decode)"), "base64 decode (obfuscated payload)"),
    (re.compile(r"\beval\s*\(|\bexec\s*\("), "dynamic code execution (eval/exec)"),
    (re.compile(r"chmod\s+(-R\s+)?0?777"), "world-writable permissions (chmod 777)"),
    (re.compile(r"\b(pip[0-9]?|npm|yarn|pnpm|gem|cargo)\s+(install|add|-g)\b"),
     "install-time dependency fetch in a bundled script (unvetted supply chain)"),
)
# Tight prompt-injection markers scanned in SKILL.md / manifest prose. Kept
# narrow on purpose so defensive security skills that *describe* these attacks
# are not false-positives.
_SKILL_PROSE_DANGER = (
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
     "instruction-override ('ignore previous instructions')"),
    (re.compile(r"disregard\s+(the\s+)?(system|above)\s+(prompt|instructions)", re.I),
     "instruction-override ('disregard the system prompt')"),
    (re.compile(r"[​‌‍‎‮]"),
     "hidden/zero-width unicode (steganographic instructions)"),
)
_SKILL_TEXT_EXTS = (".md", ".markdown", ".txt", ".rst")


def load_skill_catalog():
    """Load catalog/skills.json -> (data_dict, error_or_None)."""
    try:
        with open(SKILLS_CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as exc:
        return None, "cannot read skill catalog (%s): %s" % (SKILLS_CATALOG_PATH, exc)
    except ValueError as exc:
        return None, "skill catalog is not valid JSON: %s" % exc
    if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
        return None, "skill catalog malformed: expected {\"skills\": [...]}"
    return data, None


def _catalog_skill(data, name):
    """Return the catalog entry for `name`, or None."""
    for s in data.get("skills", []):
        if isinstance(s, dict) and s.get("name") == name:
            return s
    return None


def read_skill_frontmatter(skill_md_path):
    """Parse an agentskills.io SKILL.md frontmatter -> {name, description}.

    Independent of the index `split_frontmatter` (which only keeps index fields):
    a skill's frontmatter carries `name` + `description`, neither of which is an
    index field. Tolerates folded (`>-`) / block scalars for `description`.
    """
    out = {"name": None, "description": None}
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return out
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return out
    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close = i
            break
    if close is None:
        return out
    block = lines[1:close]
    i = 0
    while i < len(block):
        ln = block[i]
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", ln)
        if not m:
            i += 1
            continue
        key, val = m.group(1).strip(), m.group(2).strip()
        if key in ("name", "description"):
            if val in (">", ">-", "|", "|-", "") and key == "description":
                # Folded/block scalar: gather following more-indented lines.
                parts = []
                j = i + 1
                while j < len(block) and (block[j].strip() == "" or
                                          block[j][:1] in (" ", "\t")):
                    parts.append(block[j].strip())
                    j += 1
                out["description"] = " ".join(p for p in parts if p).strip()
                i = j
                continue
            out[key] = val.strip().strip("'\"")
        i += 1
    return out


def _pinned_ref():
    """Short git SHA of the package repo, or 'unknown' (read-only)."""
    cp = _git(paths.REPO_ROOT, "rev-parse", "--short", "HEAD")
    if cp.returncode == 0 and cp.stdout.strip():
        return cp.stdout.strip()
    return "unknown"


def vet_skill_dir(skill_dir):
    """Static, read-only skill-vetter pass over a skill folder.

    Returns a list of finding strings (empty == clean). Scripts are scanned for
    code-execution / egress / supply-chain danger; markdown/manifest prose is
    scanned with a TIGHT injection-marker set so defensive security skills that
    merely *describe* these attacks do not false-positive. NEVER executes
    anything under review (execution is exactly what the gate prevents).
    """
    findings = []
    if not os.path.isdir(skill_dir):
        return ["skill source directory not found: %s" % skill_dir]
    for root, _dirs, files in os.walk(skill_dir):
        for fn in sorted(files):
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, skill_dir)
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError as exc:
                findings.append("%s: unreadable (%s)" % (rel, exc))
                continue
            is_text = os.path.splitext(fn)[1].lower() in _SKILL_TEXT_EXTS
            # Prose injection markers apply to every file (incl. SKILL.md).
            for rx, label in _SKILL_PROSE_DANGER:
                if rx.search(content):
                    findings.append("%s: %s" % (rel, label))
            # Script danger signatures apply to NON-text (executable) files only.
            if not is_text:
                for rx, label in _SKILL_SCRIPT_DANGER:
                    if rx.search(content):
                        findings.append("%s: %s" % (rel, label))
    return findings


def cmd_skill_list(args):
    data, err = load_skill_catalog()
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    skills = [s for s in data.get("skills", []) if isinstance(s, dict)]
    cat = getattr(args, "category", None)
    if cat:
        skills = [s for s in skills if s.get("category") == cat]
    skills = sorted(skills, key=lambda s: (s.get("category", ""), s.get("name", "")))
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "count": len(skills), "skills": skills},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Authored skill catalog (%d skill(s)%s):"
              % (len(skills), " in category '%s'" % cat if cat else ""))
        for s in skills:
            print("  %-30s [%-12s] %s"
                  % (s.get("name", "?"), s.get("category", "?"),
                     s.get("summary", "")))
    return 0


def cmd_skill_search(args):
    data, err = load_skill_catalog()
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    q = (args.query or "").strip().lower()
    hits = []
    for s in data.get("skills", []):
        if not isinstance(s, dict):
            continue
        hay = " ".join([
            s.get("name", ""), s.get("category", ""), s.get("summary", ""),
            " ".join(s.get("tags", []) if isinstance(s.get("tags"), list) else []),
        ]).lower()
        if q in hay:
            hits.append(s)
    hits = sorted(hits, key=lambda s: s.get("name", ""))
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "query": args.query, "count": len(hits),
                   "skills": hits}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if not hits:
            print("No catalog skills match '%s'." % args.query)
        else:
            print("%d match(es) for '%s':" % (len(hits), args.query))
            for s in hits:
                print("  %-30s [%-12s] %s"
                      % (s.get("name", "?"), s.get("category", "?"),
                         s.get("summary", "")))
    return 0


def _validate_skill_dir(skill_dir):
    """Validate ONE on-disk skill folder (frontmatter name==folder, description
    present, skill-vetter clean). Returns (name, errors) for an IN-PLACE authored
    skill — no catalog entry required. Closes the seam that `vet_skill_dir`
    previously ran only at `skill add`, never on a KB skill authored in place."""
    skill_dir = os.path.abspath(skill_dir.rstrip("/"))
    folder = os.path.basename(skill_dir)
    skill_md = os.path.join(skill_dir, "SKILL.md")
    errs = []
    if not os.path.isdir(skill_dir):
        return folder, ["source dir missing: %s" % skill_dir]
    if not os.path.isfile(skill_md):
        return folder, ["SKILL.md missing under %s" % skill_dir]
    fm = read_skill_frontmatter(skill_md)
    if fm.get("name") != folder:
        errs.append("frontmatter name (%r) != folder (%r)"
                    % (fm.get("name"), folder))
    if not fm.get("description"):
        errs.append("frontmatter has no 'description' (routing text)")
    vet = vet_skill_dir(skill_dir)
    if vet:
        errs.append("skill-vetter findings: %s" % "; ".join(vet))
    return folder, errs


def cmd_skill_validate(args):
    # In-place authored skill: vet an arbitrary directory directly (no catalog).
    source = getattr(args, "source", None)
    if source:
        name, errs = _validate_skill_dir(source)
        ok = not errs
        if getattr(args, "format", "text") == "json":
            json.dump({"ok": ok, "count": 1,
                       "results": [{"name": name, "ok": ok, "errors": errs}]},
                      sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            if ok:
                print("OK    %s" % name)
            else:
                print("FAIL  %s" % name)
                for e in errs:
                    print("        - %s" % e)
            print("%s: skill dir %s" % ("PASS" if ok else "FAIL", source))
        return 0 if ok else 1
    data, err = load_skill_catalog()
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    only = getattr(args, "name", None)
    results = []
    for s in data.get("skills", []):
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if only and name != only:
            continue
        errs = []
        src = s.get("source")
        if not src:
            errs.append("catalog entry missing 'source'")
            results.append({"name": name, "ok": False, "errors": errs})
            continue
        skill_dir = os.path.join(paths.REPO_ROOT, src)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isdir(skill_dir):
            errs.append("source dir missing: %s" % src)
        elif not os.path.isfile(skill_md):
            errs.append("SKILL.md missing under %s" % src)
        else:
            fm = read_skill_frontmatter(skill_md)
            folder = os.path.basename(skill_dir.rstrip("/"))
            if fm.get("name") != folder:
                errs.append("frontmatter name (%r) != folder (%r)"
                            % (fm.get("name"), folder))
            if name and fm.get("name") != name:
                errs.append("frontmatter name (%r) != catalog name (%r)"
                            % (fm.get("name"), name))
            if not fm.get("description"):
                errs.append("frontmatter has no 'description' (routing text)")
            vet = vet_skill_dir(skill_dir)
            if vet:
                errs.append("skill-vetter findings: %s" % "; ".join(vet))
        results.append({"name": name, "ok": not errs, "errors": errs})
    if only and not results:
        sys.stderr.write("agentware: no catalog skill named '%s'\n" % only)
        return 1
    ok = all(r["ok"] for r in results) and bool(results)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": ok, "count": len(results), "results": results},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for r in results:
            if r["ok"]:
                print("OK    %s" % r["name"])
            else:
                print("FAIL  %s" % r["name"])
                for e in r["errors"]:
                    print("        - %s" % e)
        print("%s: %d/%d catalog skill(s) valid."
              % ("PASS" if ok else "FAIL",
                 sum(1 for r in results if r["ok"]), len(results)))
    return 0 if ok else 1


def _copy_skill_tree(src_dir, dest_dir):
    """Copy a skill folder src_dir -> dest_dir, never clobbering an existing
    SKILL.md (idempotent; protects operator edits). Returns (copied_bool, note)."""
    dest_md = os.path.join(dest_dir, "SKILL.md")
    if os.path.exists(dest_md):
        return False, "already installed at %s (left untouched)" % dest_dir
    os.makedirs(os.path.dirname(dest_dir) or ".", exist_ok=True)
    # py3.7.16 has no shutil.copytree(dirs_exist_ok=) (a 3.8+ kwarg that raises
    # TypeError on this floor). Merge-copy by hand, preserving
    # copytree(dirs_exist_ok=True) semantics: recurse the source tree, mirror
    # every (incl. empty) subdir into a possibly pre-existing dest, and copy each
    # file with metadata via copy2. This is a COPY (never shutil.move, which the
    # promote path at cmd_skill_promote uses and would DELETE the catalog source);
    # the SKILL.md guard above keeps it a no-clobber, idempotent install.
    for root, _dirs, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        target_root = dest_dir if rel == "." else os.path.join(dest_dir, rel)
        os.makedirs(target_root, exist_ok=True)
        for fn in files:
            shutil.copy2(os.path.join(root, fn), os.path.join(target_root, fn))
    return True, "copied"


def cmd_skill_add(args):
    name = args.name
    data, cerr = load_skill_catalog()
    if cerr is not None:
        sys.stderr.write("agentware: %s\n" % cerr)
        return 1

    external = bool(getattr(args, "source", None))
    if external:
        # Non-catalog / external source: gated behind skill-vetter + explicit
        # --allow-external confirmation. NEVER fetched from the internet here.
        if "://" in args.source:
            sys.stderr.write("agentware: refusing to fetch a remote source (%s); "
                             "`skill add` is not an internet fetcher. Clone it "
                             "locally, vet it, then pass --source <local-path> "
                             "--allow-external.\n" % args.source)
            return 1
        skill_dir = os.path.abspath(args.source)
        catalog_name = os.path.basename(skill_dir.rstrip("/"))
        name = name or catalog_name
        trust = "external (untrusted)"
    else:
        entry = _catalog_skill(data, name)
        if entry is None:
            sys.stderr.write("agentware: '%s' is not in the trusted catalog. "
                             "Use `skill list` to see authored skills, or "
                             "`--source <local-path> --allow-external` for an "
                             "externally-sourced skill.\n" % name)
            return 1
        skill_dir = os.path.join(paths.REPO_ROOT, entry.get("source", ""))
        trust = "trusted in-repo catalog"

    if not os.path.isdir(skill_dir):
        sys.stderr.write("agentware: skill source not found: %s\n" % skill_dir)
        return 1

    # skill-vetter static review + provenance/pinned-ref report (R-SEC-02).
    findings = vet_skill_dir(skill_dir)
    provenance = {
        "name": name,
        "trust": trust,
        "source": skill_dir,
        "pinned_ref": _pinned_ref() if not external else "n/a (external)",
        "vet_findings": findings,
    }
    if findings:
        sys.stderr.write("agentware: skill-vetter found issues in '%s':\n" % name)
        for f in findings:
            sys.stderr.write("  - %s\n" % f)
        sys.stderr.write("Aborting install (fail-closed). Resolve the findings "
                         "or vet manually with the skill-vetter skill.\n")
        return 1
    if external and not getattr(args, "allow_external", False):
        sys.stderr.write("agentware: '%s' is an EXTERNAL source. It passed the "
                         "static vet, but external skills require explicit "
                         "confirmation: re-run with --allow-external.\n" % name)
        return 1

    # Destination: external KB (indexed) by default, or a harness path (not
    # indexed — the harness discovers it on the filesystem).
    harness_path = getattr(args, "harness_path", None)
    if harness_path:
        dest_dir = os.path.join(os.path.abspath(harness_path), name)
        copied, note = _copy_skill_tree(skill_dir, dest_dir)
        if getattr(args, "format", "text") == "json":
            json.dump({"ok": True, "installed": copied, "dest": dest_dir,
                       "indexed": False, "provenance": provenance, "note": note},
                      sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("VET PASS (%s, ref %s)" % (trust, provenance["pinned_ref"]))
            print("%s skill '%s' -> %s (harness path, not indexed)"
                  % ("INSTALLED" if copied else "SKIPPED", name, dest_dir))
        return 0

    # Default: install into the external KB under skills/<name>/ and register
    # via index add (the sole index writer, R-KB-01).
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    rel_dir = os.path.join("skills", name)
    dest_dir = os.path.join(kdir, rel_dir)
    copied, note = _copy_skill_tree(skill_dir, dest_dir)
    rel_md = os.path.join(rel_dir, "SKILL.md")

    eid = skillentry.skill_entry_id(name)
    cat_entry = _catalog_skill(data, name) or {}
    indexed, rerr = skillentry.skill_register_entry(
        kdir, eid, name, rel_md,
        description=read_skill_frontmatter(
            skillentry.skill_md_path(dest_dir)).get("description"),
        catalog_summary=cat_entry.get("summary"),
        catalog_tags=cat_entry.get("tags"))
    if rerr is not None:
        sys.stderr.write("agentware: %s\n" % rerr)
        return 1

    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "installed": copied, "dest": dest_dir,
                   "indexed": indexed, "id": eid, "path": rel_md,
                   "provenance": provenance, "note": note},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("VET PASS (%s, ref %s)" % (trust, provenance["pinned_ref"]))
        print("%s skill '%s' -> %s" % ("INSTALLED" if copied else "SKIPPED",
                                        name, dest_dir))
        print("%s index entry '%s' (category=skills, path=%s)"
              % ("REGISTERED" if indexed else "ALREADY-INDEXED", eid, rel_md))
    return 0


def cmd_skill_remove(args):
    name = args.name
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    eid = skillentry.skill_entry_id(name)
    had_entry, ierr = skillentry.skill_unregister_entry(kdir, eid)
    if ierr is not None:
        sys.stderr.write("agentware: %s\n" % ierr)
        return 1

    dest_dir = os.path.join(kdir, "skills", name)
    removed_files = False
    if not getattr(args, "keep_files", False) and os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir)
        removed_files = True

    if not had_entry and not removed_files:
        sys.stderr.write("agentware: nothing to remove for skill '%s' "
                         "(no index entry, no %s)\n" % (name, dest_dir))
        return 1
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "id": eid, "unregistered": had_entry,
                   "files_removed": removed_files, "dest": dest_dir},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("%s index entry '%s'%s"
              % ("UNREGISTERED" if had_entry else "NOT-INDEXED", eid,
                 (" + removed %s" % dest_dir) if removed_files else ""))
    return 0


def cmd_skill_roster_injectable(args):
    """Exit 0 iff the given roster file has >=1 real always-on entry to inject.

    The SessionStart hook calls this instead of grepping, so the hook and the
    `index validate` linter share ONE predicate (`roster_is_injectable`) and can
    never drift. A missing/unreadable file is treated as NOT injectable (exit 1),
    matching the hook's `[[ -f ... ]]` no-op semantics. Never touches the index,
    so it is safe to call from a hook against ANY path — including a
    `skills-proposed/` roster, which is deliberately NOT wired into any roster the
    hook injects, so proposals stay invisible to always-on.
    """
    path = args.path
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        text = ""
    ok = index.roster_is_injectable(text)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": ok, "path": path}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    return 0 if ok else 1


_EXPORTS = [
    "SKILLS_CATALOG_PATH",
    "_SKILL_SCRIPT_DANGER",
    "_SKILL_PROSE_DANGER",
    "_SKILL_TEXT_EXTS",
    "load_skill_catalog",
    "_catalog_skill",
    "read_skill_frontmatter",
    "_pinned_ref",
    "vet_skill_dir",
    "cmd_skill_list",
    "cmd_skill_search",
    "_validate_skill_dir",
    "cmd_skill_validate",
    "_copy_skill_tree",
    "cmd_skill_add",
    "cmd_skill_remove",
    "cmd_skill_roster_injectable",
]
