"""agentware_lib.knowledge.index — KB index CRUD/validation + layout constants (layer: knowledge).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its four domain submodules so every pre-split call site — which reaches the module
under an alias (`import agentware_lib.knowledge.index as index`) and reads
`index.save_index` — keeps resolving unchanged, and so every existing monkeypatch
target (`<consumer>.index.<name>`) keeps working. The submodules are:

  * `indexlock`     — the same-host index mutation lock (_index_lock): reentrant,
                      thread-safe (RLock) and cross-process (fcntl.flock), wrapped
                      around every `load_index -> mutate -> save_index` section.
  * `indexread`     — KB-layout constants, knowledge-dir-relative path helpers,
                      load_index (with auto-rebuild-once recovery) /
                      entry_last_verified / indexed_paths / parse_tags.
  * `indexwrite`    — save_index (F2 fingerprint-drop rule), the (category, id)
                      ordering + tag-map mutators, and the shared add core _do_add.
  * `indexvalidate` — validate_index (error ordering is a byte contract), the
                      derived-roster rendering + injection lint, and the knowledge
                      validators (tag/template/orphan + rebuild parity).
  * `indexrebuild`  — the entry-file scan/inference and the KB-rebuild
                      orchestrators build_index_from_frontmatter + rebuild_kb.

The union of the four submodules' `_EXPORTS` is exactly the pre-split module's
`_EXPORTS` — no name added, none dropped. INDEX_REL is re-exported by name (it is
owned by core.kblayout) so `index.INDEX_REL` keeps resolving for its callers.

Owns: nothing of its own — only the re-export surface that keeps the pre-split
import path (`import agentware_lib.knowledge.index as index`) and every
monkeypatch target (`<consumer>.index.<name>`) resolving unchanged.
Does NOT own: any behaviour — see the four submodules above; the KB on-disk
layout constants (agentware_lib.core.kblayout); the frontmatter parser
(agentware_lib.core.frontmatter); the `index` verb command HANDLERS
(agentware_lib.features.indexcmd).
Layer deps: knowledge -> core only (its own submodules plus core.kblayout for
INDEX_REL; nothing above the knowledge layer).
"""

from agentware_lib.knowledge.index.indexlock import (
    _index_lock,
    _index_lock_path,
)
from agentware_lib.knowledge.index.indexread import (
    CATEGORIES,
    DECISIONS_REL,
    ENTRY_SCAN_SUBDIRS,
    FEATURES_REL,
    INDEX_REL,
    KNOWLEDGE_SUBDIRS,
    LEARNINGS_REL,
    REQUIRED_FIELDS,
    SKILLS_PROPOSED_DIR,
    SYNC_CATEGORY_RULES,
    SYNC_EXCLUDE_BASENAMES,
    decisions_dir,
    entry_last_verified,
    features_path,
    index_path,
    indexed_paths,
    learnings_dir,
    _try_rebuild_missing_index,
    load_index,
    parse_tags,
)
from agentware_lib.knowledge.index.indexwrite import (
    _do_add,
    _entry_key,
    add_tags_to_map,
    insert_entry_sorted,
    remove_tags_from_map,
    save_index,
)
from agentware_lib.knowledge.index.indexvalidate import (
    _index_parity_errors,
    _render_roster,
    _roster_link,
    orphaned_learning_errors,
    roster_injection_errors,
    roster_is_injectable,
    tag_consistency_errors,
    template_conformance_errors,
    validate_index,
)
from agentware_lib.knowledge.index.indexrebuild import (
    _infer_category,
    _is_sync_candidate,
    _suggest_id,
    _suggest_title,
    _rebuild_kb_locked,
    build_index_from_frontmatter,
    rebuild_kb,
    scan_entry_files,
    scan_unindexed,
)
