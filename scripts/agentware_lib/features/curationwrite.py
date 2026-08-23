"""agentware_lib.features.curationwrite -- the shared curation write chain (layer: features).

Owns: the transaction BOTH curation verbs run once they hold the index lock and
nothing else -- the three-way collision refusal (_refuse_existing_entry:
duplicate id, path already indexed, file already on disk), the register step and
its rollback (_finish_curation_write: index._do_add, best-effort unlink of the
markdown already written, then index.save_index inside the caller's lock), and
the one success line they end on (_emit_curation_entry: the `{"ok": True, ...}`
json envelope or the "<LABEL> entry '<id>' -> <path> (registered in index)"
text). Nothing here reads argparse, resolves a knowledge dir or renders an entry
body.

Does NOT own: the handlers, the write prologue/CR-gate, the `--topic` grammar or
the near-duplicate policy -> agentware_lib.features.curationcmd; the entry
renderers -> agentware_lib.knowledge.curation; the index CRUD (_do_add /
save_index) -> agentware_lib.knowledge.index.

Why a SIBLING of curationcmd rather than three more functions inside it: the
obligation changes at the lock. `curationcmd` owns what the two verbs decide
BEFORE they may write (is this caller entitled to this knowledge dir, is this
topic a legal stem, is this content a restatement of something already here);
this module owns what they DO once the decision is made, and that half is
byte-for-byte the same work for a learning and for a decision. Keeping the two
halves apart is also what stops the next curation verb from re-deriving the
rollback: it gets the whole transaction by calling one function, the way
`_kdir_for_curation_write` already hands it the gate.

Layer deps: features.curationwrite -> knowledge (index) only -- DOWNWARD, and
the narrowest dependency set of any module in this family. It reaches the index
through the MODULE alias so a monkeypatch of the owner propagates; `index` stays
the sole index writer (R-KB-01).

Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import sys

import agentware_lib.knowledge.index as index


def _refuse_existing_entry(data, eid, rel_path, abs_path, noun):
    """The message refusing a curation write that would collide, or None.

    THREE collisions, checked in ONE place because both handlers checked all
    three and neither may check two: a duplicate ID (the index's primary key),
    a PATH already indexed (a second row pointing at one file), and a FILE
    already on disk (an unindexed entry the write would silently overwrite).
    They are ORDERED, and one candidate can trip more than one, so collapsing
    any pair changes which sentence the operator gets.

    Read-only, and runs inside the caller's index lock against the `data` that
    lock protects. `noun` names the artifact in the third message ("learning" /
    "decision"); the first two are id/path-shaped and identical for both verbs.
    """
    entries = data.get("entries") or []
    if any(e.get("id") == eid for e in entries):
        return "agentware: duplicate id '%s'\n" % eid
    if any(e.get("path") == rel_path for e in entries):
        return "agentware: path already indexed: %s\n" % rel_path
    if os.path.exists(abs_path):
        return ("agentware: %s file already exists (refusing to overwrite): "
                "%s\n" % (noun, abs_path))
    return None


def _finish_curation_write(kdir, data, eid, title, category, rel_path,
                           tag_list, summary, created, abs_path):
    """Register the entry whose file is already written: (entry, error).

    The write is TWO steps that must not half-happen -- the markdown is on disk
    before `_do_add` runs -- so the ROLLBACK belongs with the add rather than
    with either caller, which is why both callers had grown the same unlink
    block. On an add error the file is removed best-effort (an unlink that
    itself fails leaves the index untouched, which is the safe half) and the
    error is RETURNED for the caller to print, since the two handlers own their
    own stderr prefix. On success the index is saved, still inside the caller's
    lock: a save outside it could lose a concurrent update, which is the whole
    reason the lock spans load->save.
    """
    entry, add_err = index._do_add(kdir, data, eid, title, category, rel_path,
                                   tag_list, summary, created)
    if add_err is not None:
        try:
            os.remove(abs_path)
        except OSError:
            pass
        return None, add_err
    index.save_index(kdir, data)
    return entry, None


def _emit_curation_entry(entry, eid, abs_path, fmt, key, label):
    """The one success line both curation verbs end on. Writes stdout only.

    `key` is the json envelope's payload field ("learning" / "decision") and
    `label` the text line's leading word ("LEARNED" / "DECISION"); the `{"ok":
    True, ...}` envelope, the indent, the trailing newline and the
    "-> <path> (registered in index)" tail are shared and were duplicated.
    Deliberately takes the FORMAT rather than the args namespace: nothing in
    this module should be able to reach a flag the caller did not pass it.
    """
    if fmt == "json":
        json.dump({"ok": True, key: entry}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("%s entry '%s' -> %s (registered in index)"
              % (label, eid, abs_path))


_EXPORTS = [
    "_refuse_existing_entry",
    "_finish_curation_write",
    "_emit_curation_entry",
]
