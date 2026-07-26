# 0013. Real product photography for demo listings

## Status
Accepted

## Context
SPEC.md §3.3 documented since early on that `description_vs_images` (Consistency
Agent) was measurably noisier than the demo's other checks because the demo's images
were synthetic text-only placeholders, and predicted real product photography would
fix it. Tested that hypothesis for real rather than assuming it.

**Sourcing.** Discussed sourcing options with the human (Wikimedia Commons CC0/public
domain vs. user-supplied vs. programmatic realism); chose Wikimedia Commons, with the
constraint that licensing be verified per file, not assumed. Two real, appropriately
licensed photos:
- `fixtures/real_photos/iphone-16-back.jpg` -- [Back of iPhone 16](https://commons.wikimedia.org/wiki/File:Back_of_iPhone_16.jpg),
  CC BY-SA 4.0, © Kyu3a.
- `fixtures/real_photos/sony-headphones.jpg` -- [Sony Headphones (40476165073).jpg](https://commons.wikimedia.org/wiki/File:Sony_Headphones_(40476165073).jpg),
  CC BY 2.0, © Wutthichai Charoenburi.

Neither is CC0, so both require attribution -- see `fixtures/real_photos/ATTRIBUTION.md`.
Both downscaled to 900px on the long edge before committing (originals were 2-4MB;
downscaled to ~55-65KB) -- no other modification.

**Scope, deliberately narrowed** (discussed with the human before implementing): only
`clean` and `risky_seller` get real photos (`inconsistent` reuses the iPhone photo,
since it declares Apple/iPhone and the image should genuinely support that claim).
`counterfeit_brand` stays synthetic -- it deliberately needs an image that shows *no*
genuine Apple branding, safer to keep deterministic than gamble on an ambiguous real
photo's brand-visibility. `weapon` stays synthetic -- deliberately not sourcing real
firearm photography; it's already correctly classified via text regardless of images.

**First candidate Sony photo rejected after testing.** A Sony WH-1000XM3 photo was
tried first. Real calls to Evidence Agent's vision model against it misread the
earcup's "NC/AMBIENT" control-label text as a fabricated brand, "Ambienton" --
producing a false `brandMismatch` that would have incorrectly routed a seller-history
review case toward counterfeit (C001) instead. Replaced with a different Commons photo
where "SONY" is printed plainly with no nearby confusable text; confirmed correct
(`brandsDetected: ["SONY"]`, no mismatch) across 10/10 real calls, vs. the rejected
photo's clear, reproducible misread.

**Two real, reproducible bugs found and fixed** while testing the new photos (not
photo-specific -- real photos just produce more varied model output than the old OCR
placeholders, so they surfaced faster):

1. `agents/evidence_agent.py`: the vision model occasionally emits a stray duplicate
   closing markdown fence after the real one. The old `_strip_fences` used
   `rsplit("```", 1)` which only removes one trailing fence, leaving `json.loads` to
   fail with "Extra data". Replaced with `_parse_json_object`, which uses
   `json.JSONDecoder().raw_decode()` to parse the first JSON value and ignore
   anything trailing, tolerant of any number of stray fences.
2. `agents/consistency_agent.py`: the text/vision models occasionally ignore the
   "answer with exactly one word: true or false" instruction and ramble instead
   ("Given the information provided..."), producing no true/false token within the
   10-token budget and crashing `_verdict`. Added `_post_for_verdict`, which retries
   the request once (a fresh sample) before raising -- confirmed via real calls that a
   retry resolves this the large majority of the time, since it's stochastic
   non-compliance, not a broken prompt. Still raises after two consecutive failures,
   preserving SPEC.md §4's "agent error -> PENDING_REVIEW, never silent-approve"
   guarantee rather than masking a genuine repeated failure.

**Honest negative result: `description_vs_images` got *noisier*, not better, with
real photography for the `clean` listing.** Measured mean `inconsistencyScore` across
6 real-call samples each:

| | Mean inconsistencyScore |
|---|---|
| Synthetic placeholder (old) | 0.171 |
| Real iPhone photo (new) | ~0.394-0.396 |

Tried adjusting the description to drop claims the photo can't visually confirm
("factory sealed", "international warranty") -- same pattern that fixed the
`risky_seller` case -- but it made no meaningful difference here (0.394 vs 0.396).
Root cause, best guess: the sourced photo shows only the bare back of the phone -- no
screen, no box, nothing that visually distinguishes "Pro Max" from a base iPhone 16 or
confirms "256GB". The old synthetic placeholder spelled out "APPLE / iPhone 16 Pro Max
/ 256GB" as literal OCR-readable text, which let the vision model "match" by reading
text rather than doing genuine visual-semantic reasoning -- it was gaming the check,
not passing it honestly. A real photo that requires actual visual inference is
harder to confirm, not easier, when the model can't tell trim/storage from the image
alone.

Brought this finding back to the human before proceeding (rather than either hiding it
or unilaterally hunting for a better-scoring photo, which would risk cherry-picking).
Decision: ship the real photo anyway (still a strictly more honest artifact -- a real
Apple product, not a lie made of pixels) and document the negative result plainly in
SPEC.md rather than claim a fix that testing didn't support.

## Decision
- `generate_synthetic_data.py`'s `listing()` gains a `real_photo` parameter
  (mutually exclusive with `image_lines`); `clean`, `inconsistent`, and
  `risky_seller` use it, `counterfeit_brand` and `weapon` stay synthetic.
- `agents/evidence_agent.py`'s JSON parsing and `agents/consistency_agent.py`'s
  verdict parsing are both more robust to real model non-compliance (see above).
- SPEC.md §3.3 updated to state plainly that real photography fixed the
  counterfeit-brand-detection reliability question it was never explicitly asked to
  answer, but did **not** resolve the `description_vs_images` noise it was
  originally invoked to fix -- that remains open.

## Consequences
- `risky_seller`'s counterfeit-brand-detection reliability is now solid (verified
  10/10 real calls); this scenario's earlier flakiness (documented in ADR 0012's
  neighboring work on Safety Agent taxonomy) is unrelated and unaffected.
- `clean` no longer reliably auto-approves via a low `inconsistencyScore` -- this was
  already true before this change (SPEC.md's original note), and remains true after
  it, for a different and now-understood reason. Not fixed by this change; still a
  candidate for future work (e.g., a photo showing more distinguishing detail: screen
  on, or retail packaging with model/storage text).
- Both agent robustness fixes apply to all future real (non-demo) model calls, not
  just demo listings -- a real reliability improvement independent of the photography
  question.
