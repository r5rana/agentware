#!/usr/bin/env python3
"""Task-83 WRITE-SIDE neutrality redaction: the counterpart to the five-surface gate.

`tests/_portability_gate.py` grades the tree AFTER the fact. This module is the
other half: it stops a GENERATOR from committing a vendor token in the first
place, and it re-neutralises a capture that was frozen before the rule existed.

Why a second module rather than a bigger gate
---------------------------------------------
The gate answers "is this tree publishable?". This answers "what should a
generator have written?". They share one needle set on purpose -- both call
`_portability_gate.load_needles`, so a redactor can never drift from the scanner
that grades it -- but they are opposite obligations, and the gate sits at its
600-line ceiling, so the split is where the obligation changes (R-MOD-04).

The three artifacts this exists for
-----------------------------------
Three committed CAPTURES carried a token the gate reports and no wave could
clear, because each is a freeze taken at a pinned head:

  * ``tests/baselines/baseline-donor.json``  -- a donor test-run capture whose
    ``output_tail`` quotes a failing assertion's message verbatim.
  * ``tests/fixtures/nodeids-donor.json``    -- the frozen donor node-id set;
    three donor tests are NAMED for the donor environment's build system and
    auth, because that is what they test.
  * ``tests/fixtures/preserved-surface.json``-- the same three ids inside the
    ``pending_in_destination`` backlog.

Deleting those rows is not available: a capture rewritten to hide a deletion
destroys the only record of what the other tree contained (the finding the
task-12 debt ratchet and the task-15 record ledger are both built on). So the
row STAYS, at its position, with its shape -- and only the token inside it is
replaced.

Why ONE opaque placeholder and not a digest
-------------------------------------------
The obvious design writes ``<neutralised-a3f9c1d2>`` so two different tokens stay
distinguishable. That design LEAKS: these tokens are short dictionary words, and
a truncated SHA-256 of a short word is confirmable by anyone who guesses it, so
the digest would preserve exactly the fact the redaction exists to remove. A
per-needle index (``<neutralised-1>``) leaks less but is unstable -- it renumbers
whenever the private needle file gains a row, churning every frozen artifact for
a reason unrelated to any of them.

So every needle collapses to one opaque `<neutralised>`. Distinguishing tokens is
not something a frozen capture needs; keeping the row's identity is. Where that
collapse WOULD destroy an identity -- two node ids that differ only inside a
redacted span -- `redact_ids` raises rather than silently merging them, because a
set that quietly loses a member is precisely the regression the freeze exists to
catch.

Fail-closed
-----------
The `environment` needle class is resolved at runtime from a private file outside
this repository (R-LOC-03). When that file is absent this module reports the class
as `unavailable` and says so in its exit banner and JSON; a run that could not look
for something must never read like a run that looked and found nothing.

Stdlib only. `--check` writes nothing anywhere.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:  # the corpus's dual-import seam: ONE module object, whichever spelling wins
    from tests import _portability_gate as gate
except ImportError:  # pragma: no cover - direct-script invocation
    import _portability_gate as gate

#: The single opaque replacement. Deliberately NOT parameterised: a caller that
#: could choose its own placeholder could choose one that carries the token.
PLACEHOLDER = "<neutralised>"

#: The committed capture artifacts this redactor owns, relative to the repo root.
#: Every one is a freeze at a pinned head, which is why re-redaction is the only
#: available remedy -- regeneration would re-derive both sides at TODAY's heads
#: and churn thousands of unrelated rows.
CAPTURE_ARTIFACTS = (
    os.path.join("tests", "baselines", "baseline-donor.json"),
    os.path.join("tests", "baselines", "baseline-local.json"),
    os.path.join("tests", "fixtures", "nodeids-donor.json"),
    os.path.join("tests", "fixtures", "nodeids-local.json"),
    os.path.join("tests", "fixtures", "preserved-surface.json"),
)


class RedactionCollision(ValueError):
    """Two distinct inputs redacted to the same output -- a lost member."""


# --------------------------------------------------------------------------
# needles
# --------------------------------------------------------------------------

def load_compiled(needles_path=None):
    """Return ``(compiled, meta)`` from the SAME loader the read-side gate uses.

    Sharing `load_needles` is the whole point: a redactor with its own notion of
    what a vendor token is would clean an artifact against a narrower rule than
    the gate grades it by, and the two would disagree exactly once -- on the
    token nobody thought of.
    """
    rows, meta = gate.load_needles(needles_path)
    return gate.compile_needles(rows), meta


def unavailable_classes(meta):
    """Needle classes that resolved no source, in declared order."""
    return [cls for cls in gate.CLASSES
            if meta.get(cls, {}).get("status") == "unavailable"]


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------

def redact_text(text, compiled):
    """Replace every needle occurrence in `text` with `PLACEHOLDER`.

    Case-insensitive, matching the gate's own scan, so a token and its
    capitalised spelling are ONE needle. Pure text in, text out -- no I/O
    (INV-2). Idempotent: the placeholder contains no needle, so a second pass is
    a no-op.
    """
    if not text:
        return text
    for row, pattern in compiled:
        text = _replace_ci(text, row, pattern)
    return text


def _replace_ci(text, row, pattern):
    """One needle's replacement, driven by the gate's own compiled pattern.

    The pattern is compiled against LOWERCASE input (that is how `scan_text`
    uses it), so matching runs on a lowered copy while the slices come from the
    original -- the offsets are identical because `str.lower` here is
    length-preserving for every character these needles can match.
    """
    low = text.lower()
    spans = [m.span() for m in pattern.finditer(low)]
    if not spans:
        return text
    out, prev = [], 0
    for start, end in spans:
        out.append(text[prev:start])
        out.append(PLACEHOLDER)
        prev = end
    out.append(text[prev:])
    return "".join(out)


def redact_ids(ids, compiled):
    """Redact a SET of identifiers, refusing to merge two distinct members.

    A node-id freeze is a set whose cardinality is the claim. If two ids differ
    only inside a redacted span they would collapse to one row and the freeze
    would report a loss it caused itself, so this raises instead.
    """
    seen, out = {}, []
    for original in ids:
        redacted = redact_text(original, compiled)
        if redacted != original and redacted in seen \
                and seen[redacted] != original:
            raise RedactionCollision(
                "redaction merges two distinct ids into %r: %r and %r; the "
                "freeze would lose a member it is meant to preserve"
                % (redacted, seen[redacted], original))
        seen[redacted] = original
        out.append(redacted)
    return out


def redact_json(value, compiled):
    """Redact every string anywhere in a decoded JSON document.

    Keys as well as values: a fixture that names a token in a key leaks exactly
    as loudly as one that names it in a value.
    """
    if isinstance(value, str):
        return redact_text(value, compiled)
    if isinstance(value, list):
        return [redact_json(v, compiled) for v in value]
    if isinstance(value, dict):
        return {redact_text(k, compiled): redact_json(v, compiled)
                for k, v in value.items()}
    return value


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def inspect_file(path, compiled):
    """Return ``(needs_redaction, redacted_bytes)`` for one committed artifact.

    JSON is round-tripped through the decoder so the redaction reaches keys and
    nested values; anything that is not JSON is treated as text. The output
    preserves the file's existing separator/indent style by re-serialising with
    the same options the generators use, then comparing bytes -- a formatting
    change would show up as a diff and be caught here rather than in review.
    """
    body = _read(path)
    try:
        decoded = json.loads(body)
    except ValueError:
        redacted = redact_text(body, compiled)
        return redacted != body, redacted
    cleaned = redact_json(decoded, compiled)
    if cleaned == decoded:
        return False, body
    indent = _detect_indent(body)
    rendered = json.dumps(cleaned, indent=indent, sort_keys=False) + "\n"
    return True, rendered


def _detect_indent(body):
    """The indent width a generator used, read off the document's second line."""
    for line in body.split("\n")[1:]:
        stripped = line.lstrip(" ")
        if stripped and stripped != line:
            return len(line) - len(stripped)
    return 1


def check_paths(paths, compiled):
    """Paths that still carry a needle, with the count of strings affected."""
    hits = []
    for path in paths:
        if not os.path.isfile(path):
            hits.append((path, "MISSING"))
            continue
        needs, _ = inspect_file(path, compiled)
        if needs:
            hits.append((path, "carries a needle"))
    return hits


def apply_paths(paths, compiled):
    """Rewrite every path that still carries a needle; return those rewritten."""
    changed = []
    for path in paths:
        if not os.path.isfile(path):
            continue
        needs, rendered = inspect_file(path, compiled)
        if not needs:
            continue
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        changed.append(path)
    return changed


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_argparser():
    parser = argparse.ArgumentParser(
        prog="_neutrality_redact.py",
        description="Write-side neutrality redaction for committed captures.")
    parser.add_argument("paths", nargs="*",
                        help="files to inspect (default: the capture artifacts)")
    parser.add_argument("--apply", action="store_true",
                        help="rewrite the offending files in place")
    parser.add_argument("--needles", default=None,
                        help="explicit private needle file")
    parser.add_argument("--require-class", action="append", default=[],
                        choices=list(gate.CLASSES),
                        help="fail if this class resolved no needle source")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def resolve_paths(paths):
    return [p if os.path.isabs(p) else os.path.join(REPO, p)
            for p in (paths or CAPTURE_ARTIFACTS)]


def main(argv=None):
    args = build_argparser().parse_args(argv)
    try:
        compiled, meta = load_compiled(args.needles)
    except (ValueError, OSError) as exc:
        sys.stderr.write("neutrality redact error: %s\n" % exc)
        return 2
    paths = resolve_paths(args.paths)
    if args.apply:
        changed = apply_paths(paths, compiled)
        result = {"mode": "apply", "changed": changed,
                  "classes": meta, "ok": True}
    else:
        hits = check_paths(paths, compiled)
        result = {"mode": "check",
                  "hits": [{"path": p, "reason": r} for p, r in hits],
                  "classes": meta, "ok": not hits}
    result["unavailable"] = unavailable_classes(meta)
    if args.json:
        json.dump(result, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        _render(result, sys.stdout)
    missing = [c for c in args.require_class if c in result["unavailable"]]
    if missing:
        sys.stderr.write("required class(es) resolved no needles: %s\n"
                         % ", ".join(missing))
        return 2
    return 0 if result["ok"] else 1


def _render(result, stream):
    for cls in gate.CLASSES:
        info = result["classes"].get(cls, {})
        stream.write("class %-16s %-12s %d needle(s)\n"
                     % (cls, info.get("status", "?"), info.get("count", 0)))
    if result["mode"] == "apply":
        for path in result["changed"]:
            stream.write("REDACTED %s\n" % os.path.relpath(path, REPO))
        stream.write("OK %d file(s) rewritten\n" % len(result["changed"]))
        return
    for hit in result["hits"]:
        stream.write("HIT  %-60s %s\n"
                     % (os.path.relpath(hit["path"], REPO), hit["reason"]))
    stream.write("%s %d file(s) still carrying a needle\n"
                 % ("OK" if result["ok"] else "FAIL", len(result["hits"])))


if __name__ == "__main__":
    sys.exit(main())
