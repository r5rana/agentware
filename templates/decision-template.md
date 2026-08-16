---
id: decide-decision-slug
title: Decision Title
category: decisions
tags: [tag1, tag2]
created: YYYY-MM-DD
last_verified: YYYY-MM-DD
summary: One sentence naming the choice made and the reason it was made, readable without the body.
author: your-handle
source: agent
origin: team
team: your-team
---
# [Decision Title]

> Prefer `scripts/agentware decide` over copying this file by hand — it fills the
> entry contract above and registers the decision. This template exists for the
> cases the verb cannot express, and as the readable statement of what a decision
> entry must contain.
>
> A decision is recorded so a FUTURE session does not silently re-litigate it, or
> silently violate it. That is why the sections below are what they are: options
> and rationale explain the choice to someone who was not there, and
> reversibility tells them what it costs to change their mind.

## Options weighed

- **(a)** [option] — [what it buys, what it costs]
- **(b)** [option] — [what it buys, what it costs]

## Choice

[(b), stated in one line.]

## Rationale

[Why this option, in terms of the goal and the acceptance criteria — not in terms
of preference. If a constraint forced the choice, name the constraint. If the
losing option would have been better under a different assumption, say which
assumption, so a future reader can tell when this decision has expired.]

## Reversibility

[How hard is it to undo, and what would have to be true to revisit it. A decision
nobody can cost is a decision nobody will revisit.]

## Consequences

- [What is now true because of this.]
- [What is now forbidden because of this.]

## References

- [Related decision or learning](./other-decision.md)
