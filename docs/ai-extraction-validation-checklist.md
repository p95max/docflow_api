# AI Extraction Validation Layer

This checklist tracks deterministic validation after the AI structured response
and before the result is presented to the user. It complements, rather than
replaces, manual confirmation of an extraction.

## MVP — implemented in this change

- [x] Strict Pydantic contract: unknown fields rejected; lengths, amount,
  ISO-date format and ISO-shaped currency checked.
- [x] Normalize AI currency to uppercase.
- [x] Independent validation service, separate from the OpenAI client.
- [x] Ground total amounts against numeric candidates in source text, including
  German notation such as `1.250,00`.
- [x] Ground ISO document dates and deadlines against extracted source text.
- [x] Warn when sender cannot be found in source text.
- [x] Logical checks: amount/currency pair, deadline before document date,
  future document date, and missing invoice sender/amount.
- [x] Persist `validation_status`, errors, warnings and score with the document.
- [x] Expose validation result in API, document view and recovery backups.
- [x] Unit tests for schema, German amount normalization, grounding and logic.

## Next tasks

### Evidence and grounding

- [x] Extend the AI contract with per-field evidence snippets and page number.
- [x] Match normalized evidence against the cited local page.
- [x] Require evidence for amount, currency, sender and dates before accepting
  those values automatically.
- [x] Link currency evidence to the grounded total amount.
- [x] Add locale-aware date candidates (`DD.MM.YYYY`, textual German dates).

### Quality and ambiguity

- [x] Store all amount/date candidates and their labels (total, tax, net;
  issue date, deadline).
- [x] Mark equally plausible candidates as `multiple_amount_candidates` or
  `multiple_date_candidates`.
- [x] Calculate OCR quality and require review for low-quality scans.
- [x] Weight schema, grounding, OCR and ambiguity in the validation score.

### Review and fallback

- [ ] Show field-level evidence next to editable extraction fields.
- [ ] Make `needs_review` the default UI state for suspicious results and retain
  the original AI response in the audit trail.
- [ ] Define a single bounded retry with a stronger model only for
  `needs_review` results.
- [ ] Compare first and fallback extraction without overwriting the first result
  silently.

### Test and evaluation set

- [ ] Add 30–50 labelled documents: invoices, letters, contracts, poor scans,
  multiple amounts and multiple dates.
- [ ] Add regression fixtures for real extraction failures (with data redacted).
- [ ] Measure per-field exact match, false-null and false-positive rates.
- [ ] Add integration coverage for validation persistence and fallback routing.
