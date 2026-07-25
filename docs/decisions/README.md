# Architecture Decision Records

Lightweight ADRs for choices that weren't obvious from SPEC.md alone, or where SPEC.md
under-specified something enough that an agent implementing it would have had to
invent a scheme. Each one is Status / Context / Decision / Consequences, numbered in
the order they were made.

New ADRs: copy the format of an existing one. Only write one when a choice is
non-obvious, was genuinely contested, or cost real effort (a live API test, a
rejected alternative) to arrive at -- not for routine implementation detail already
covered by SPEC.md.

| # | Decision |
|---|---|
| [0001](0001-safety-agent-model-choice.md) | Safety Agent uses safety-guard-8b-v3, not nemotron-3.5-content-safety |
| [0002](0002-cli-tool-layer-not-chat-loop.md) | Moderator CLI is a Python tool layer, not a standalone chat loop |
| [0003](0003-decision-fusion-formula.md) | Decision fusion is a deterministic 3-step formula |
| [0004](0004-locking-model.md) | Claim listings with `FOR UPDATE SKIP LOCKED`, no broker |
| [0005](0005-find-similar-cases-heuristic.md) | find_similar_cases is a heuristic, not embeddings |
