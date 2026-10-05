# Case-study notes: what failed and what changed

Raw material for the write-up. Newest first. All runs are on the synthetic mock world.

## T4.3, five scenarios, gpt-5-mini, prompt v2 (2026-10-05)
Result: 5 of 5 reached a validated plan. 0 protocol errors, 0 validation retries, about $0.04 for all five.
One run per scenario, so this shows the loop works, not how reliable it is (evals come later).

Problems found:
- Cook plans padded with pantry staples (oil, salt, turmeric, ghee): 7 to 10 items for 1 to 2 people, Rs 455 to 685. Fixed in prompt v3 (COOK PLANS).
- Questions came back in English to Hinglish prompts. Fixed in prompt v3 (LANGUAGE).
- The harness answered budget questions that were never asked, so a Rs 500 budget was not enforced in the all-closed scenario. A harness bug, not a product bug: budget is now set up front.
- One question ("order or cook?") reads as the agent handing its decision back. Left as is; owner to judge in the Q8 wording review.

Earlier, prompt v1 (Groq gpt-oss-20b): assumed non-veg and listed no assumptions; a run died with a model error whose cause the loop had thrown away. Fixed: v2 prompt, error detail kept.
Trace redaction turned 8-digit catalogue ids into `<digits>`; fixed.
