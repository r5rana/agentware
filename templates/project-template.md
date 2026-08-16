---
id: proj-project-slug
title: Project Name
category: projects
tags: [tag1, tag2, tag3]
created: YYYY-MM-DD
last_verified: YYYY-MM-DD
summary: One sentence saying what this project is and what problem it solves, readable without opening the file.
author: your-handle
source: agent
origin: team
team: your-team
status: active
---
# [Project Name]

> The frontmatter above is the ENTRY CONTRACT, not decoration. `id`, `title`,
> `category`, `tags` and `summary` are what `index validate` requires and what
> `recall` ranks over; `origin` and `team` are the org facets (`R-ORG-05`),
> inferred from the knowledge-dir path on a flat install, so a single-operator KB
> can delete those two lines. `status` drives the plan-status ranking multiplier —
> an ACTIVE project's entries should surface above a reference one's.
>
> Keep `last_verified` current. A project page is the entry most likely to
> silently rot, because the project keeps moving after the page stops.


---

## Overview

[Brief description — what it is, what problem it solves, the main goal.]

---

## Background

[Context: how the project came about, links to relevant notes / issues.]

---

## Architecture

```
[ASCII diagram or short prose description of the architecture.]
```

---

## Technical details

### Technologies
- Technology 1
- Technology 2

### Key components

| Component | Purpose |
|-----------|---------|
| Component 1 | Description |

---

## Implementation status

### Phase 1 — [phase name]
- [ ] Task 1
- [ ] Task 2

---

## Resources

| Resource | Type | Location |
|----------|------|----------|
| Resource 1 | Type | Path / link |

---

## Open questions

1. Question 1?

---

## Related

- [Related project](./other-project/index.md)
- [Related learning](../../learnings/topic.md)

---

[← Back to Projects](../index.md) | [← Back to Main](../../MAIN.md)
