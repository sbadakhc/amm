---
name: add-agent
description: >
  Scaffold a new pipeline agent (Evidence/Consistency/Safety/Policy/Decision-style)
  following this project's established pattern: verify the real model call first,
  then implement, then test with recorded fixtures, then update SPEC.md and
  pipeline.py. Use when adding a new agent to the moderation pipeline. Triggers on
  "add an agent", "new agent", "scaffold agent".
argument-hint: <agent-name, e.g. "fraud">
compatibility: Claude Code
metadata:
  category: development
  version: "1.0"
---

# Skill: add-agent

## Purpose

Every agent in this pipeline (`agents/*.py`) was built in the same order, and it's the
order that matters: verify the real model's actual behavior before writing code
around an assumed one. SPEC.md's model names, output schemas, and even prompt framing
were all corrected at least once after a live test contradicted the original
assumption (see `docs/decisions/0001-safety-agent-model-choice.md` for the clearest
example). Skipping straight to implementation risks building against a schema the
model doesn't actually return.

## Steps

### Step 1 -- Define the contract in SPEC.md first

Add a `### 3.N <Name> Agent` section (§3) describing: what it consumes from the
canonical document (§3.1) or upstream agents, what it outputs (the artifact
`payload` shape), and how Policy/Decision Agents will use its output. Write this
*before* touching code -- it's the thing you'll be testing against.

### Step 2 -- Verify the real model call

If the agent calls a model: make the actual API call (curl or a short Python
snippet) with realistic input before writing `agents/<name>_agent.py`. Check:
- Does the response schema match what you assumed in Step 1?
- Does `logprobs: true` work on this model, if you need a genuine (not fabricated)
  confidence score?
- Does the model actually behave correctly on both a clear positive and a clear
  negative case for what you're checking? (The Consistency Agent's first prompt
  framing passed this check backwards -- see `docs/decisions/`.)

If SPEC.md's assumption was wrong, fix SPEC.md now, not after implementing.

### Step 3 -- Implement `agents/<name>_agent.py`

Follow the existing agents' shape: a `run_<name>_agent(canonical_doc) -> dict`
function returning the artifact shape from SPEC.md §5:
```python
{"listingId": ..., "agent": "<Name>Agent", "version": ..., "producedAt": ..., "payload": {...}}
```
Include a `if __name__ == "__main__":` block that takes a JSON canonical doc as
`sys.argv[1]` for standalone testing, matching the other agents.

### Step 4 -- Test against real data

Run it against a real sample listing from `listings.json` (or a new one) with a real
model call, not a mock, at least once -- this is how the Evidence Agent's "no brand
detected on a Generic-branded listing" bug was caught before it ever reached a test
file.

### Step 5 -- Add offline tests

Add `tests/test_<name>_agent.py` using the `fake_post` fixture (`tests/conftest.py`)
with a *trimmed but structurally faithful* copy of the real response captured in
Step 2/4 -- not an invented shape. See `tests/test_safety_agent.py` for the pattern.

### Step 6 -- Wire it in

- If it's part of the parallel fan-out (§1), add it to the `ThreadPoolExecutor` calls
  in `pipeline.run_fusion`.
- If Policy Agent needs its output, add the mapping to
  `agents/policy_agent.py`'s rule table and `SAFETY_CATEGORY_TO_RULE`-style lookup.
- If it should be rerunnable individually via the CLI, add it to
  `cli/tools.AGENT_RUNNERS`.

### Step 7 -- Definition of done

- [ ] SPEC.md §3 has the agent's contract, matching what was actually built
- [ ] Tested against a real model call and real sample data at least once
- [ ] `tests/test_<name>_agent.py` passes offline with recorded fixtures
- [ ] Wired into `pipeline.py` and/or `cli/tools.py` as appropriate
- [ ] `pre-commit run --all-files` passes
