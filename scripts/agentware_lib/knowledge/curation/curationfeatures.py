"""agentware_lib.knowledge.curation.curationfeatures — the FEATURES.md renderer + verb (layer: knowledge).

Owns: the derived FEATURES.md surface — the pure category-grouped markdown
renderer (_render_features, which `index rebuild` also calls so the regenerated
roster and the `features` verb can never render two different documents), the
cmd_features handler that writes it to `index.features_path(kdir)`, and the
`features` parser registrar (the registrar travels with its handler).
Does NOT own: the learning/decision RENDERERS + _topic_to_title/_slugify ->
agentware_lib.knowledge.curation.curationrender; the ingestion connector pipeline
+ `ingest` verb -> agentware_lib.knowledge.curation.curationingest; the index
CRUD/load/path helpers -> agentware_lib.knowledge.index; the per-category roster
rendering (_render_roster) -> agentware_lib.knowledge.index.indexvalidate; the
rebuild orchestration that CALLS _render_features ->
agentware_lib.knowledge.index.indexrebuild.
Layer deps: knowledge -> core (config) + knowledge (index), all DOWNWARD. No
sibling import inside this package (it needs neither renderer nor ingest), and
never imports ops/retrieval/features or the entrypoint.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import sys

import agentware_lib.core.config as config
import agentware_lib.knowledge.index as index


def _render_features(data):
    markdown = "# Knowledge Base — Features & Capabilities\n\n"
    markdown += ("> **NOTICE:** Auto-generated from this knowledge base's "
                 "`index.json` by `scripts/agentware features`. Do not edit "
                 "manually.\n\n")
    categories = {}
    for entry in data.get("entries", []):
        cat = entry.get("category", "uncategorized")
        categories.setdefault(cat, []).append(entry)
    for cat in sorted(categories.keys()):
        pretty_cat = cat.replace("_", " ").title()
        markdown += "## %s\n\n" % pretty_cat
        markdown += "| Title | Path | Summary |\n"
        markdown += "|-------|------|---------|\n"
        for entry in categories[cat]:
            title = entry.get("title", "")
            path = entry.get("path", "")
            summary = entry.get("summary", "")
            markdown += "| **%s** | `[%s](%s)` | %s |\n" % (title, path, path, summary)
        markdown += "\n"
    if not categories:
        markdown += "_No entries yet. Capture knowledge with " \
                    "`scripts/agentware learn ...`._\n"
    return markdown


def cmd_features(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    # _allow_rebuild=False: FEATURES.md is DERIVED FROM the index, so silently
    # rebuilding the index here would render a document from a file the
    # operator never had and report success over a corruption.
    data, err = index.load_index(kdir, _allow_rebuild=False)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    content = _render_features(data)
    with open(index.features_path(kdir), "w", encoding="utf-8") as f:
        f.write(content)
    if getattr(args, "format", "text") == "json":
        cat_count = len({e.get("category", "uncategorized")
                         for e in data.get("entries", [])})
        json.dump({"ok": True, "path": index.features_path(kdir),
                   "entries": len(data.get("entries", [])), "categories": cat_count},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Successfully generated %s" % index.features_path(kdir))
    return 0


# --- parser registrars (owned here; assembled by agentware_lib.cli via the
# frozen _PARSER_REGISTRATION tuple). The registrar travels with its handler:
# cmd_features lives in this module, so its `features` verb registers here.
# register_* are resolved by the assembler through importlib (module field of
# the tuple, which names the PACKAGE whose aggregator re-imports this name),
# NOT via the _EXPORTS facade. --------------------------------------------------
def register_features(sub, fmt_parent):
    # features ---------------------------------------------------------------
    p_feat = sub.add_parser(
        "features", parents=[fmt_parent],
        help="regenerate FEATURES.md from the knowledge index.json")
    p_feat.set_defaults(func=cmd_features)


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_render_features",
    "cmd_features",
]
