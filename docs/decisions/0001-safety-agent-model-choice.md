# 0001. Safety Agent uses safety-guard-8b-v3, not nemotron-3.5-content-safety

## Status
Accepted

## Context
SPEC.md and the project's stack description named `nvidia/nemotron-3.5-content-safety`
as the Safety Agent's model. Before writing any code, the model was called live via
`https://integrate.api.nvidia.com/v1/chat/completions` with both a clearly unsafe
message (weapon listing text) and a clearly safe one.

The actual response was a binary verdict only: `"User Safety: safe"` or
`"User Safety: unsafe"` -- no category, no free-text explanation. Policy Agent (§3.5)
needs to know *which* rule was hit (W001 weapons vs. D001 drugs vs. others) to route
correctly; a bare unsafe/safe flag can't support that.

A second model on the same API key, `nvidia/llama-3.1-nemotron-safety-guard-8b-v3`,
was tested with the same inputs and returned a category alongside the verdict, e.g.
`{"User Safety": "unsafe", "Safety Categories": "Guns and Illegal Weapons"}`.

## Decision
Use `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` for the Safety Agent instead of the
originally-specified model. `confidence` is derived from the model's own
log-probability for the safe/unsafe token it emitted (`logprobs: true` on the chat
completion), not a separately requested score. `explanation` is generated
deterministically from the category string, not model-written prose.

## Consequences
- SPEC.md §3.4/§3.5 were updated to reflect the real model and the real category
  taxonomy strings (e.g. `Guns and Illegal Weapons`, `Controlled/Regulated Substances`)
  rather than the original placeholder names (`Weapons`).
- The stack description ("NVIDIA Nemotron 3.5 Content Safety") is no longer literally
  accurate to the model actually called; SPEC.md's agent section is the source of
  truth for the real model name, not the top-level stack line.
- General lesson (see AGENTS.md §3): verify a model's actual output schema with a live
  call before writing an agent around an assumed one.
