---
id: learn-topic-slug
title: Topic Name
category: learnings
tags: [tag1, tag2, tag3]
created: YYYY-MM-DD
last_verified: YYYY-MM-DD
summary: One sentence a future session can act on without opening the file — what is true, and what to do differently because of it.
author: your-handle
source: agent
origin: team
team: your-team
---
# [Topic Name]

> The frontmatter above is the ENTRY CONTRACT, not decoration. `id`, `title`,
> `category`, `tags` and `summary` are what `index validate` requires and what
> `recall` ranks over; `origin` and `team` are the org facets (`R-ORG-05`) and are
> inferred from the knowledge-dir path on a flat install, so a single-operator KB
> can simply delete those two lines. `last_verified` is what the staleness window
> reads — an entry that is still true after a re-check should have this bumped
> rather than be rewritten.
>
> Prefer `scripts/agentware learn` over copying this file by hand: it fills the
> contract, registers the entry, and cannot produce a file the index rejects.

## What is [Topic]?

[One or two paragraphs. State the thing plainly, then say why it matters — what
goes wrong for someone who does not know it.]

## Key points

| Point | Description |
|-------|-------------|
| Point 1 | Description |
| Point 2 | Description |

## How to use it

### Prerequisites

1. Prerequisite 1
2. Prerequisite 2

### Quick start

```bash
# Commands or code example
```

## Gotchas

- Gotcha 1 — what it looks like when it bites, and how to avoid it.
- Gotcha 2 — same.

## References

- [Documentation or upstream link]
- [Related learning](./other-topic.md)
