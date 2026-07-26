# 0012. Confirmed Safety Agent taxonomy; added F001, S001, extended C001

## Status
Accepted

## Context
SPEC.md §3.4 previously said the full `Safety Categories` taxonomy for
`nvidia/llama-3.1-nemotron-safety-guard-8b-v3` needed confirming against the model
card before finalizing Policy Agent's rule-trigger table, and only listed 4 categories
as "known" (2 of which -- `Fraud/Deception`, `Criminal Planning/Confessions` -- weren't
actually mapped to any rule in `agents/policy_agent.py`).

Confirmed empirically instead of trusting a scraped model-card page: real calls to the
real hosted model with a spread of ~16 prompts, one per suspected risk category (see
`agents/safety_agent.py`'s `_classify`, called directly, no mock). This matches the
project's established verify-against-real-calls convention and avoids trusting
external doc content as ground truth (`.claude/rules/security.md`).

Observed categories: `Guns and Illegal Weapons`, `Controlled/Regulated Substances`,
`Criminal Planning/Confessions`, `Illegal Activity`, `Fraud/Deception`, `Sexual`,
`Sexual (minor)`, `PII/Privacy`, `Hate/Identity Hate`, `Immoral/Unethical`,
`Suicide and Self Harm`, `Violence`, `Harassment`, `Profanity`,
`Copyright/Trademark/Plagiarism`, `Political/Misinformation/Conspiracy`,
`Needs Caution`. Most of these are general chat-safety categories, not obviously
actionable as marketplace-listing policy (e.g. `Violence`, `Hate/Identity Hate`,
`Profanity`, `Political/Misinformation/Conspiracy`, `Harassment`,
`Suicide and Self Harm`) -- brought back to the human rather than assumed; decision
was to add rules for the three that are clearly listing-policy relevant.

## Decision
Added to `agents/policy_agent.py`:
- **F001** (Fraud/Deception prohibited, High, not auto-reject) -- triggered by
  `SafetyAgent` violation `Fraud/Deception`. Confirmed firing reliably and clearly on
  a real fraud-oriented listing (fake bank statement generator), confidence 0.9998.
- **S001** (Sexual content involving minors prohibited, Critical, **autoReject: true**)
  -- triggered by `Sexual (minor)`. The first rule to actually use the `autoReject`
  hard-override lever reserved in SPEC.md §3.5/§4 since the original spec. Confirmed
  firing reliably on a real test listing, confidence 0.9981, correctly forced REJECT
  regardless of confidence thresholds.
- **C001 extended**: `Copyright/Trademark/Plagiarism` (Safety Agent, text-based) is
  now a second trigger for the existing C001 rule, alongside Evidence Agent's
  image-based `brandMismatch`. Both signals are deliberately deduped to at most one
  C001 match (taking the higher confidence) -- `policyRules` must never contain a
  duplicate rule id.

**Known limitation, found by testing, not assumed:** the `Copyright/Trademark/Plagiarism`
text signal is unreliable. Identical counterfeit-style listing text ("replica watches,
sold as genuine...") produced 4 different safety-classification outcomes across a
handful of real calls -- safe, `Criminal Planning/Confessions`, and only once the
actual `Copyright/Trademark/Plagiarism` category. F001, S001, W001, and D001 all fired
consistently across every real test in this round; this one didn't. Shipped anyway as
a secondary signal (it's still correct when it does fire, and doesn't block or degrade
anything when it doesn't), but Evidence Agent's `brandMismatch` remains the reliable
primary C001 trigger -- don't rely on the text signal alone for counterfeit detection.

Categories NOT mapped to a rule (deliberately, for now): `Sexual` (non-minor),
`Violence`, `Hate/Identity Hate`, `Suicide and Self Harm`, `Harassment`, `Profanity`,
`Immoral/Unethical`, `Illegal Activity`, `PII/Privacy`,
`Political/Misinformation/Conspiracy`, `Criminal Planning/Confessions` (fires
alongside almost every other violation, too broad to be its own signal), `Needs
Caution` (reads as a low-confidence hedge flag, not a distinct violation). Revisit if
this pipeline ever needs to moderate listing text more broadly than "is this product
allowed to be sold," e.g. if user-generated reviews/messages get added to scope.

## Consequences
- `SAFETY_CATEGORY_TO_RULE` and `RULES` in `agents/policy_agent.py` updated;
  `RULE_SETS_BY_CATEGORY_PREFIX["*"]` includes the two new rule ids.
- Tests added: `test_fraud_violation_maps_to_f001`,
  `test_sexual_minor_violation_maps_to_s001_autoreject`,
  `test_copyright_safety_category_maps_to_c001`,
  `test_brand_mismatch_and_copyright_category_dedupe_to_one_c001_match`.
- SPEC.md §3.4 and §3.5 updated to reflect the confirmed taxonomy and rule table,
  replacing the "confirm before finalizing" note.
