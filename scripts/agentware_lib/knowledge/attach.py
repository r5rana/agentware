"""agentware_lib.knowledge.attach - team-mode ADOPT-an-existing-repo conformance + the `attach` verb (layer: knowledge).

Owns: the team-mode onboarding surface that ADOPTS an existing repo as the shared
KB - the required-dirs constant (ATTACH_REQUIRED_DIRS), the READ-ONLY conformance
check (attach_conformance_report: expected dirs present + validate_index passes +
`index rebuild` is a parity NO-OP, writing NOTHING per INV-2), the idempotent
structure repair (_attach_migrate_structure: fills MISSING dirs/rosters + seeds an
absent index, never clobbers existing data), the `attach` command handler
(cmd_attach), and its parser registrar (register_attach).
Does NOT own: the index CRUD/validate/rebuild core (see agentware_lib.knowledge.index
- this module READS index.KNOWLEDGE_SUBDIRS / load_index / validate_index /
build_index_from_frontmatter / _index_parity_errors / index_path / save_index only);
the KB-layout NON_INDEXED_DIRS tuple (see agentware_lib.core.kblayout); the
config-pointer persist done by `init` (see agentware_lib.features.setupcmd).
Layer deps: knowledge -> knowledge (agentware_lib.knowledge.index, a same-layer
sibling) + core (agentware_lib.core.kblayout, for NON_INDEXED_DIRS) + stdlib
(os / json / sys). Never imports retrieval/ops/features or the entrypoint (no
upward/forward edges); neither index nor kblayout imports this module, so there is
no cycle.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import sys

import agentware_lib.core.kblayout as kblayout
import agentware_lib.knowledge.index as index


ATTACH_REQUIRED_DIRS = index.KNOWLEDGE_SUBDIRS + ("work", "logs")


def attach_conformance_report(path):
    """Read-only conformance check for adopting `path` as the KB. (ok, report).

    Checks, in order: (1) expected dirs present (KNOWLEDGE_SUBDIRS + work/ + logs/);
    (2) index.json present and `validate_index` passes; (3) `index rebuild` is a
    parity no-op (the committed index equals what entry frontmatter would rebuild).
    NEVER mutates `path`. `report` carries `missing_dirs`, `index_errors`,
    `parity_errors`, and a human `summary`.
    """
    report = {"path": path, "missing_dirs": [], "index_errors": [],
              "parity_errors": [], "ok": False, "summary": ""}
    if not os.path.isdir(path):
        report["index_errors"] = ["not a directory: %s" % path]
        report["summary"] = "refused: %s is not a directory" % path
        return False, report

    report["missing_dirs"] = [
        d for d in ATTACH_REQUIRED_DIRS
        if not os.path.isdir(os.path.join(path, d))]

    # INV-2 + the task-15 target-guard contract: _allow_rebuild=False. This
    # report is READ-ONLY and it must REFUSE a corrupt index rather than
    # repair it — load_index's default recovery would write into a directory
    # we are merely inspecting, and would turn the refusal into a pass.
    data, err = index.load_index(path, _allow_rebuild=False)
    if err is not None:
        report["index_errors"] = [err]
    else:
        report["index_errors"] = index.validate_index(path, data)
        # Parity: rebuild-from-frontmatter must equal the committed index. Pure,
        # read-only — build_index_from_frontmatter writes nothing.
        rebuilt, rerrs = index.build_index_from_frontmatter(path)
        if rerrs:
            report["parity_errors"] = rerrs
        else:
            report["parity_errors"] = index._index_parity_errors(data, rebuilt)

    report["ok"] = not (report["missing_dirs"] or report["index_errors"]
                        or report["parity_errors"])
    if report["ok"]:
        report["summary"] = "conformant: safe to adopt %s as the shared KB" % path
    else:
        bits = []
        if report["missing_dirs"]:
            bits.append("missing dirs: %s" % ", ".join(report["missing_dirs"]))
        if report["index_errors"]:
            bits.append("index: %s" % "; ".join(report["index_errors"]))
        if report["parity_errors"]:
            bits.append("rebuild parity: %s" % "; ".join(report["parity_errors"]))
        report["summary"] = "refused: " + " | ".join(bits)
    return report["ok"], report


def _attach_migrate_structure(path):
    """Idempotently fill MISSING KB structure (dirs/rosters; seed index if absent).

    Mirrors the dir/roster/index-seed portion of `cmd_init` but writes NO HOME
    config and clobbers nothing existing — so it can repair an attach candidate
    in place. Returns the list of created dirs.
    """
    created = []
    for sub in ("",) + index.KNOWLEDGE_SUBDIRS + kblayout.NON_INDEXED_DIRS:
        d = os.path.join(path, sub) if sub else path
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            created.append(sub or ".")
    ipath = index.index_path(path)
    if not os.path.exists(ipath):
        index.save_index(path, {"entries": [], "tags": {}})
    for sub in index.KNOWLEDGE_SUBDIRS:
        roster = os.path.join(path, sub, "index.md")
        if not os.path.exists(roster):
            with open(roster, "w", encoding="utf-8") as f:
                f.write("# %s\n\n_Index of %s._\n" % (sub.capitalize(), sub))
    return created


def cmd_attach(args):
    """Validate (and optionally migrate) a candidate repo for adoption as the KB.

    `--migrate` first fills missing STRUCTURE idempotently (never clobbers data),
    then re-validates. Exit 0 iff conformant. Read-only without `--migrate`.
    """
    path = os.path.abspath(os.path.expanduser(args.path))
    migrated = []
    if getattr(args, "migrate", False):
        migrated = _attach_migrate_structure(path)
    ok, report = attach_conformance_report(path)
    report["migrated_dirs"] = migrated
    if getattr(args, "format", "text") == "json":
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if ok:
            print("ATTACH OK: %s" % report["summary"])
        else:
            print("ATTACH REFUSED: %s" % report["summary"])
            if not getattr(args, "migrate", False) and report["missing_dirs"]:
                print("  hint: re-run with --migrate to fill missing structure "
                      "(idempotent; never clobbers data).")
    return 0 if ok else 1


def register_attach(sub, fmt_parent):
    # attach -----------------------------------------------------------------
    p_attach = sub.add_parser(
        "attach", parents=[fmt_parent],
        help="validate (and optionally --migrate) a candidate repo for adoption "
             "as the shared KB (team-mode onboarding)")
    p_attach.add_argument("--path", required=True,
                          help="path to the candidate KB repo to validate")
    p_attach.add_argument("--migrate", action="store_true",
                          help="idempotently fill missing structure first "
                               "(never clobbers existing data), then validate")
    p_attach.set_defaults(func=cmd_attach)


_EXPORTS = [
    "ATTACH_REQUIRED_DIRS",
    "attach_conformance_report",
    "_attach_migrate_structure",
    "cmd_attach",
]
