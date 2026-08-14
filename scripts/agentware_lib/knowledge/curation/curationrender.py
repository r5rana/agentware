"""agentware_lib.knowledge.curation.curationrender — entry RENDERERS + string helpers (layer: knowledge).

Owns: the deterministic entry RENDERERS every KB write path funnels through —
_render_learning (machine frontmatter + the regenerated human blockquote header +
body, with the `back_label` seam that lets `decide` reuse it verbatim) and
_render_decision_body (the R-AUTO-03 accountability fields options/choice/
rationale/reversibility) — plus the two pure knowledge string helpers
_topic_to_title and _slugify that the ingest adapters and the learn/decide
handlers both slug through.
Does NOT own: the ingestion connector pipeline (`_ingest_*` + INGEST_SOURCES +
cmd_ingest + register_ingest) -> agentware_lib.knowledge.curation.curationingest;
the FEATURES.md renderer + handler -> agentware_lib.knowledge.curation.curationfeatures;
the frontmatter parser/builder itself -> agentware_lib.core.frontmatter; the
learn/decide HANDLERS cmd_learn/cmd_decide (they reach guard_shared_write -> ops.fleet)
-> agentware_lib.features.curationcmd.
Layer deps: knowledge -> core (frontmatter) only, DOWNWARD. Pure + stdlib (re):
no I/O, no clock, no sibling import — the two ingest/features siblings import
THIS module, never the reverse, so the package has no internal cycle.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import re

import agentware_lib.core.frontmatter as frontmatter


def _topic_to_title(topic):
    words = re.split(r"[^a-zA-Z0-9]+", topic)
    return " ".join(w.capitalize() for w in words if w) or topic


def _render_learning(fields, body, back_label="Learnings"):
    """Render a learning entry: machine frontmatter + human header + body.

    `fields` is the 9-field frontmatter dict (source of truth); the human
    blockquote header below it is for readability and is regenerated from the
    same fields, so the two never drift. `back_label` names the category roster
    the footer links back to (the `./index.md` path is the same relative roster
    for every category), so `decide` reuses this renderer verbatim.
    """
    tag_list = fields.get("tags") or []
    tags_str = ", ".join("`%s`" % t for t in tag_list) if tag_list else "_(none)_"
    return (
        "%s"
        "# %s\n\n"
        "> **Created**: %s  \n"
        "> **Tags**: %s  \n"
        "> **Category**: %s\n\n"
        "---\n\n"
        "%s\n\n"
        "---\n\n"
        "[← Back to %s](./index.md) | [← Back to Main](../MAIN.md)\n"
        % (frontmatter.render_frontmatter(fields), fields.get("title") or "",
           fields.get("created") or "", tags_str,
           fields.get("category") or "learnings", body.rstrip("\n"), back_label)
    )


def _render_decision_body(summary, options, choice, rationale, reversible):
    """Compose a decision entry body from its structured fields.

    Mirrors how `learn` takes free-form `--content`, but `decide` enforces the
    accountability fields R-AUTO-03 requires (options, choice, rationale,
    reversibility) so every recorded autonomous decision is auditable.
    """
    return (
        "## Decision\n\n%s\n\n"
        "## Options\n\n%s\n\n"
        "## Choice\n\n%s\n\n"
        "## Rationale\n\n%s\n\n"
        "## Reversibility\n\n%s\n"
        % (summary.strip(), options.strip(), choice.strip(),
           rationale.strip(), reversible.strip())
    )


def _slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_topic_to_title",
    "_render_learning",
    "_render_decision_body",
    "_slugify",
]
