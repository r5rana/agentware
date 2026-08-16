"""agentware_lib.ops.planauthor.plansanitize -- g2 plan-text neutralization (layer: ops).

Owns: the EMIT-SIDE g2 guard family -- the three sanitizers that neutralize text
before it is rendered into a plan.md (_sanitize_provenance for a single-line
provenance value, _sanitize_context_block for an untrusted multi-line candidate
embedded in `## Context`, _sanitize_target for a `## Target packages` row) and the
three shapes they defend against (_CTX_FORGED_HEADER_RE, _CTX_ATX_HEADER_RE,
_CTX_LOOSE_TASK_RE).

The threat is ONE threat, stated once here: every plan.md parser in this tree is
FENCE-UNAWARE and LINE-ANCHORED. `_read_plan_status` / `_parse_claim` scan the whole
file for `^>\\s*Status:` and `^>\\s*Claimed-by:`; `plan_target_dirs` and the lint
heading scan anchor on `^##`; the loop's `open_markers()` grep counts `- ⬜ **<digit`
with NO closing `**`. So a code fence is not a boundary and "it is only rendered
inside a quoted block" is not a defence: any untrusted string that reaches plan.md
can forge a header, a section or a phantom task unless it is neutralized on the way
IN. A forged `> Status:` wedges the workorder on duplicate-status lint; a phantom
open marker wedges COMPLETION DETECTION -- the loop never sees the count reach 0 and
burns to MAX_ITERATIONS.

Does NOT own: the g1 KERNEL-PATH refusal family (_KERNEL_CONFUSABLE_TT /
_norm_kernel_token / _has_kernel_path_segment / _text_hits_kernel_path) -- that is a
REFUSAL (does this value name a kernel path? then reject the whole emit), a different
obligation from this module's REWRITE (make this value safe to render), and it lands
in its own module with `plan new --auto` (task 39 step 4); the plan STATE MACHINE
that consumes _sanitize_provenance for --reason/--superseded-by ->
ops.planauthor.planstate; the plan GENERATION half that consumes all three ->
ops.planauthor.planedit; the grammar these functions match against ->
ops.planmarkers.

WHY THIS IS A SEPARATE MODULE (feature 260813 task 39)
------------------------------------------------------
Same criterion as the planlint split (see
`decide-modular-core-task39-split-the-enforcer-where-the-rules-change-obligation-not-where-the-line-count-hurts`):
split where the RULES change obligation, not where the line count hurts. The g1
kernel-path family and this g2 family are named as one "sanitizer family" in the
monolith only because they sit adjacent in the file. They answer different
questions, they are called from different verbs on different paths (g1 fences the
`--auto` EMITTER; g2 runs on every set-state and every plan new), and a change to
the kernel-path token list has nothing to say about header forgery. Landing them as
one module would put ~390 lines of two unrelated obligations into planedit, which is
already 408 of its 600 lines.

Layer deps: ops.planauthor.plansanitize -> ops.planmarkers (the two plan-grammar
regexes it matches against) and stdlib `re`. A same-layer ops->ops edge onto a LEAF
that imports nothing from the package, so no cycle is reachable; nothing above ops
is imported.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all --
which is what the body differ's length floor demands
(`learn-under-a-body-length-differ-a-move-must-be-a-pure-move`: tidying in transit
reads as a body that LOST something).

Deterministic and hermetic: pure text in, text out. No I/O, no clock, no KB access.
"""

import re

from agentware_lib.ops.planmarkers import _PLAN_TASK_LINE_RE, _PLAN_VERIFY_RE


def _sanitize_provenance(value):
    """g2 guard: a provenance value (reason/trigger/…) can NEVER inject a forged
    header line. Collapse EVERY line separator to a space and strip a leading `>`
    so a crafted value cannot smuggle a `> Status:`/`> Claimed-by:` line into the
    header.

    CRITICAL: `str.splitlines()` (which every plan.md mutator uses via
    `"\\n".join(text.splitlines())`) splits on MORE than `\\r`/`\\n` — also
    `\\v \\f \\x1c \\x1d \\x1e \\x85 \\u2028 \\u2029`. Stripping only `\\r`/`\\n`
    left a value like `x\\x85> Status: done` storing clean (dup-count 1), then a
    LATER set-state/claim round-tripped it through splitlines()+join, materializing
    a SECOND `> Status:` line and PERMANENTLY WEDGING the workorder. `" ".join(
    value.splitlines())` adopts the EXACT splitlines() semantics the mutators use,
    so no residual separator survives to be re-split downstream."""
    if value is None:
        return None
    v = " ".join(value.splitlines()).strip()
    while v.startswith(">"):
        v = v[1:].strip()
    return v


# A header-shaped line the plan state machine parses. After stripping leading
# '>' markers a residual `Status:`/`Claimed-by:`/`Superseded-by:` line can no
# longer forge a header (the parsers all anchor on a leading '>'), but we
# space-prefix it anyway (defense in depth) so it can never be re-promoted.
_CTX_FORGED_HEADER_RE = re.compile(
    r"^(Status|Claimed-by|Superseded-by|Origin|Generated-by|Trigger|Evidence)\s*:",
    re.I)
# An ATX markdown SECTION header (`## Target packages`, `## Tasks`, `# X`). The
# fence-unaware section parsers (`plan_target_dirs`, the lint heading scan) anchor
# on `^##`/`^#` regardless of any wrapping code fence, so a smuggled section
# header inside a quoted candidate would forge plan STRUCTURE (e.g. hijack
# `plan target-dirs`). Neutralized the same way forged `>` headers are.
_CTX_ATX_HEADER_RE = re.compile(r"^#{1,6}(?:\s|$)")


# The loop's authoritative open-marker grep (agentware.sh:508, mirrored by
# `_PLAN_OPEN_MARKER_PY`) counts a task marker with only an OPENING `**`+digit — NO
# closing `**` required — whereas the strict `_PLAN_TASK_LINE_RE` needs the closing
# `**`. `_sanitize_context_block` must neutralize the LOOSE form too so an untrusted
# candidate cannot smuggle a phantom open task the loop counts but the sanitizer misses.
_CTX_LOOSE_TASK_RE = re.compile(r"^\s*-\s*(⬜|🟡|✅)\s*\*\*\d")


def _sanitize_context_block(value):
    """Sanitize UNTRUSTED candidate text (a mined preference) for embedding in a
    plan's Context section as a fenced block — the emit-side counterpart to the
    g1/g2/g3 guards. `_read_plan_status`/`_parse_claim` scan the WHOLE file and
    are FENCE-UNAWARE, so a code fence alone is not a boundary; every line is
    neutralized regardless:

      * normalize EVERY exotic line separator via `str.splitlines()` (the exact
        semantics every plan.md mutator round-trips through, so no residual
        `\\x85`/`\\u2028`/`\\v`… survives to be re-split downstream into a forged
        header — the same wedge `_sanitize_provenance` defends);
      * strip leading blockquote `>` markers from every line so an embedded
        `> Status: done` / `> Claimed-by: x@h` can NEVER survive as a real
        header (count stays 1; every state transition still succeeds);
      * defensively space-prefix any residual header-shaped line;
      * neutralize code-fence delimiters (``` / ~~~) so the candidate can never
        break OUT of its fence and inject markdown tasks/headers.

    Multi-line STRUCTURE is preserved (unlike `_sanitize_provenance`, which
    collapses to one line) because the block is rendered verbatim for review.
    Returns '' for empty/None so the caller renders the plain TODO stub."""
    if not value:
        return ""
    out = []
    for line in value.splitlines():
        s = line
        # Strip leading blockquote markers, repeatedly (`> > Status:`).
        while True:
            t = s.lstrip()
            if t.startswith(">"):
                s = t[1:]
                continue
            break
        s = s.rstrip()
        if _CTX_FORGED_HEADER_RE.match(s.lstrip()):
            s = " " + s.lstrip()
        # ATX section header (`## Target packages` …): the fence-unaware section
        # parsers anchor on `^##`, so a leading space makes them no longer match
        # — closing the header-injection hole this function's contract already
        # claims to cover ("inject markdown tasks/headers").
        if _CTX_ATX_HEADER_RE.match(s.lstrip()):
            s = " " + s.lstrip()
        st = s.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            # Break the fence delimiter so content cannot escape its fence.
            s = s.replace("`", "'").replace("~", "-")
        # A fence-unaware parser would still see a task marker / Verify directive
        # smuggled INSIDE the fence, so neutralize both: break the task-status
        # glyph and the `*Verify:*` token so the candidate can never forge an
        # executable/task line (the allowlist: Context prose only). Match BOTH the
        # strict form (_PLAN_TASK_LINE_RE, closing `**`) AND the LOOSE form the
        # loop's open_markers() grep counts (opening `**`+digit, NO closing `**`,
        # `_CTX_LOOSE_TASK_RE`) — else `- ⬜ **9 do it` survives as a phantom open
        # task the loop never sees reach 0 (completion detection wedged).
        if _PLAN_TASK_LINE_RE.match(s) or _CTX_LOOSE_TASK_RE.match(s):
            s = s.replace("⬜", "[ ]").replace("🟡", "[~]").replace("✅", "[x]")
        s = _PLAN_VERIFY_RE.sub("*Verify :*", s)
        out.append(s)
    return "\n".join(out).strip("\n")


def _sanitize_target(value):
    """g2 guard for --target on the untrusted `plan new --auto` path. The seed
    renders each target as `- <target>` into the plan's `## Target packages`
    section; the ONLY prior defense was the g1 kernel-path refusal
    (`_target_hits_kernel`), so a mined/malicious target could smuggle a phantom
    loose task marker (`\\n- ⬜ **9 …` → the loop's LOOSE `open_markers()` grep
    counts it forever → completion wedged → burns to MAX_ITERATIONS) or a forged
    `> Status:`/`> Claimed-by:`/`> Superseded-by:` header (duplicate-status lint
    → workorder permanently wedged). Extend the SAME g2-class neutralization the
    sibling emitted fields already get, BEFORE rendering:

      * `_sanitize_provenance` collapses EVERY line separator to a space (the
        exact `str.splitlines()` semantics every plan.md mutator round-trips
        through, so no residual `\\x85`/`\\u2028`/… survives to be re-split into a
        forged header) and strips leading blockquote `>` markers — a multi-line
        target becomes ONE line, so a line-anchored `> Status:` header can never
        survive;
      * defensively space-prefix a residual header-shaped token (`_CTX_FORGED_
        HEADER_RE`) so it can never be re-promoted into a header;
      * neutralize a loose task glyph against the RENDERED `- <target>` line,
        matching BOTH the strict `_PLAN_TASK_LINE_RE` (closing `**`) AND the
        LOOSE `_CTX_LOOSE_TASK_RE` the loop's open_markers() grep counts (opening
        `**`+digit, NO closing `**`) — else a target of `⬜ **9 …` renders `- ⬜
        **9 …`, a phantom open task the loop never sees reach 0.

    A legitimate single-line path target (no separators, no leading `>`, no task
    glyph, not header-shaped) renders BYTE-IDENTICAL — none of the branches fire.
    Returns None for None (the caller falls back to the default kdir target)."""
    if value is None:
        return None
    v = _sanitize_provenance(value)
    if v is None:
        return None
    if _CTX_FORGED_HEADER_RE.match(v.lstrip()):
        v = " " + v.lstrip()
    rendered = "- " + v
    if _PLAN_TASK_LINE_RE.match(rendered) or _CTX_LOOSE_TASK_RE.match(rendered):
        v = v.replace("⬜", "[ ]").replace("🟡", "[~]").replace("✅", "[x]")
    return v


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these names so SourceFileLoader-based tests still reach them as
# CLI.<name>. See the M3/_EXPORTS convention in the plan. The monolith still
# defines all six for its own unported call sites (`plan new --auto`, the emit
# guard); the duplicates retire together when the `plan` port deletes the legacy
# ranges (task 39 step 6).
_EXPORTS = [
    "_sanitize_provenance",
    "_CTX_FORGED_HEADER_RE",
    "_CTX_ATX_HEADER_RE",
    "_CTX_LOOSE_TASK_RE",
    "_sanitize_context_block",
    "_sanitize_target",
]

__all__ = list(_EXPORTS)
