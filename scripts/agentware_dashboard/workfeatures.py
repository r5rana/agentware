"""agentware_dashboard.workfeatures — which features the loop has on disk.

Owns: the one definition of "a feature the dashboard knows about" — a directory
under `<kdir>/work/` carrying a `plan.md` or a `.loop/`, which is the unit the
loop operates on.
Does NOT own: any per-feature payload (`readloops`, `readruns`) or any
derivation; this is a directory listing and nothing else.

WHY ITS OWN MODULE: two read-model domains ask the question — the loop panel
enumerates runs, the outcomes panel enumerates results — and a second copy of
the predicate is how two panels start disagreeing about which features exist.

READ-ONLY, stdlib only, tolerant of an absent `work/` (returns nothing).
"""

import os


def list_work_features(kdir):
    """Sorted feature dirs under `<kdir>/work` that carry a plan.md or a .loop/.

    Read-only directory listing. An unreadable or absent `work/` yields an empty
    list rather than an error: a fresh knowledge base has no runs yet, and that
    is a legitimate state for every panel that calls this.
    """
    work = os.path.join(kdir, "work")
    out = []
    try:
        for name in sorted(os.listdir(work)):
            fdir = os.path.join(work, name)
            if not os.path.isdir(fdir):
                continue
            if os.path.isfile(os.path.join(fdir, "plan.md")) or \
                    os.path.isdir(os.path.join(fdir, ".loop")):
                out.append(name)
    except OSError:
        return []
    return out
