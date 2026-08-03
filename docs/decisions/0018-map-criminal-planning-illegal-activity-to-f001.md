# 0018. Map Criminal Planning/Confessions and Illegal Activity to F001

## Status
Accepted

## Context

Evaluating fit for a real prospective customer (alsoug.com, a Sudan-based classifieds
marketplace, Arabic-language listings) surfaced a gap that hadn't been tested before:
`docs/decisions/0012` confirmed the Safety Agent's taxonomy and rule mapping using
English/generic test prompts, never against realistic Arabic scam listing text.

Real calls to `nvidia/llama-3.1-nemotron-safety-guard-8b-v3` (via `agents/safety_agent.py`
directly, no mock) against Arabic job-scam and real-estate-scam listing text found that
the same scam intent that reliably surfaces as `Fraud/Deception` in English test cases
often surfaces instead as `Criminal Planning/Confessions` and/or `Illegal Activity` in
Arabic -- neither of which `SAFETY_CATEGORY_TO_RULE` maps to a rule. Concretely:

- Arabic job-scam text ("send a $50 registration fee and your bank details via
  WhatsApp"): one call returned `PII/Privacy`, `Unauthorized Advice` -- no
  `Fraud/Deception`, no F001 match at all despite being a textbook advance-fee scam.
- Arabic real-estate-scam text ("send a deposit, no viewing needed, seller is
  traveling"): returned `Criminal Planning/Confessions`, `Illegal Activity` -- again,
  no F001 match.
- The equivalent scam pattern in English reliably returned `Fraud/Deception`.

0012 had deliberately left `Criminal Planning/Confessions` and `Illegal Activity`
unmapped, reasoning `Criminal Planning/Confessions` "fires alongside almost every other
violation, too broad to be its own signal." That reasoning was re-tested with a broader
batch of real calls: 9 clean/edgy-but-legal Arabic listings spanning electronics, jobs,
real estate, services, cars, kitchen knives, a licensed hunting rifle, a vague
"double your money" investment pitch, and a currency-exchange ad. **Zero false
positives** for either category across that batch -- both categories only fired on the
two genuine scam test cases.

## Decision

Map `Criminal Planning/Confessions` and `Illegal Activity` to F001 (Fraud or deceptive
listings prohibited), alongside the existing `Fraud/Deception` trigger. All three are
deduped the same way C001's two triggers already are -- at most one F001 match even if
more than one of the three categories fires on the same listing.

This revises 0012's original decision for these two categories specifically, based on
new evidence that didn't exist when 0012 was written (0012 never tested Arabic scam
text). The other categories 0012 left unmapped (`Sexual` non-minor, `Violence`,
`Hate/Identity Hate`, `Suicide and Self Harm`, `Harassment`, `Profanity`,
`Immoral/Unethical`, `PII/Privacy`, `Political/Misinformation/Conspiracy`,
`Needs Caution`) are unaffected -- this decision doesn't retest or revise those.

## Known limitation, found by testing, not assumed

The underlying model is non-deterministic on identical input. Repeated real calls with
the *exact same* scam listing text produced different `Safety Categories` sets across
calls -- one run on the job-scam text caught `Fraud/Deception`, another run on the
identical text didn't. A lottery/advance-fee scam pattern ("you won a prize, pay a fee
to claim it") was missed outright in one run, flagged safe at 0.87 confidence.

This mapping improves *coverage* (more of the categories that do fire on fraud are now
actionable) but does not fix *reliability* (whether fraud is detected at all on a given
call). That's a separate problem, tracked in issue #55, not addressed by this change.

## Consequences

- `SAFETY_CATEGORY_TO_RULE` in `agents/policy_agent.py` updated: `Criminal
  Planning/Confessions` and `Illegal Activity` both map to F001.
- SPEC.md §3.4 and §3.5 updated to reflect the revised mapping and cite this ADR.
- Tests added: `test_criminal_planning_violation_maps_to_f001`,
  `test_illegal_activity_violation_maps_to_f001`,
  `test_criminal_planning_and_fraud_deception_dedupe_to_one_f001_match`.
- Follow-up issue #55 filed for the non-determinism/missed-detection problem.
