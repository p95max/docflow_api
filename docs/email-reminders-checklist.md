# Optional email reminders

## Goal

Allow a user to additionally receive selected calendar reminders by email. In-app notifications remain the default and work independently from email.

The calendar form should show the existing reminder checkboxes and an optional control:

```text
[ ] Also send selected reminders by email
    Alerts will be sent to: user@example.com
```

The email address is the signed-in account address. Disable the control with a clear explanation when email delivery is not configured or the account email is not eligible for delivery.

---

## 1. Product decisions

- [x] Confirm email reminders are opt-in per event; in-app reminders remain independent.
- [x] Confirm supported offsets: at event time, 1 hour before, and 1 day before.
- [x] Snapshot the recipient email when the reminder is created; changing the account email affects only newly created reminders.
- [x] Do not create email reminders for an event whose calculated send time is already in the past.
- [x] Do not add a global email-reminder opt-out in the first release; the per-event email checkbox is the user control.
- [x] Use English email content initially and the sender display name `DocsFlow Reminders`; the sender address remains deployment configuration.
- [x] Use a clear, action-oriented subject format, for example `Reminder: Pay invoice — today`.

## 2. Data model and migrations

- [x] Add an email-recipient field to `event_reminders` if the recipient is snapshotted.
- [x] Add a provider message-ID field for traceability and support.
- [x] Keep `(event_id, channel, offset_minutes)` idempotent for the email channel.
- [x] Add indexes needed to claim pending email reminders efficiently.
- [x] Create an Alembic migration with downgrade and PostgreSQL/SQLite coverage.
- [x] Never store SMTP/API credentials or OAuth tokens in reminder records or recovery archives.

## 3. Email provider abstraction

- [x] Define an `EmailReminderDeliverer` interface independent of a provider.
- [x] Choose the first provider: SMTP, using Python's standard library and no provider SDK.
- [x] Add environment variables for provider credentials, sender address, sender name, and enable flag.
- [x] Add a configured public application base URL for direct event links; never generate production email links from a Codespaces or localhost URL.
- [x] Validate configuration at startup without logging secrets.
- [x] Keep email delivery disabled by default when configuration is incomplete.
- [x] Provide a safe fake deliverer for automated tests.

## 4. Scheduling and delivery

- [x] Extend reminder configuration so selected offsets create both `in_app` and `email` records when the optional checkbox is enabled.
- [x] Preserve in-app delivery when email is disabled or fails.
- [x] Deliver only future reminders; never send missed reminders retroactively.
- [x] Send only to the owner-approved recipient address.
- [x] Keep the email concise: event title, date/time, timezone, reminder offset, and one direct authenticated link to that specific DocsFlow event; do not include document raw text or unnecessary extracted data.
- [x] Build matching plain-text and HTML email versions.
- [x] Escape all event/document-derived content in HTML email.
- [x] Create an email design system aligned with the DocsFlow interface: dark surface palette, teal accent, compact typography, clear event card, and recognizable DocsFlow header/footer.
- [ ] Verify the HTML email remains readable in clients that block dark-mode styles; keep the plain-text alternative complete and usable.
- [ ] Test the template in major email clients and at narrow mobile widths.
- [x] Add a stable provider idempotency key to prevent duplicate sends on retries.
- [x] Mark a reminder as `sent` only after provider acceptance.
- [x] Retry temporary failures with bounded exponential backoff; stop after the configured maximum.
- [x] Cancel pending email reminders when the event is deleted, cancelled, or completed.
- [x] Recalculate pending email reminders when event date, time, or timezone changes.

## 5. Calendar form and event detail

- [ ] Keep the three existing reminder-offset checkboxes.
- [ ] Add the optional `Also send selected reminders by email` checkbox.
- [ ] Show the exact recipient: `Alerts will be sent to: <account email>`.
- [ ] Add a tooltip: email is an additional copy of the selected in-app reminders.
- [ ] Build the direct event link from the configured public application base URL.
- [ ] Disable the checkbox with an explanation when email delivery is unavailable.
- [ ] On edit, preselect email delivery only when pending email reminders exist.
- [ ] On event detail, show channels and offsets separately, for example `1 hour before · In-app + email`.
- [ ] Do not expose another user's email in any page or API response.

## 6. Account settings and consent

- [ ] Show the account email used for reminders in Settings.
- [ ] Let the user change the notification email in Settings, then verify the new address before using it for reminders.
- [x] Add an optional global email-reminder preference if approved.
- [ ] Explain that event titles and dates may be disclosed to the configured mailbox.
- [ ] Require a valid active account email before enabling email reminders.
- [ ] Decide whether email verification is required.

## 7. Security, privacy, and reliability

- [ ] Keep provider credentials only in deployment secrets / `.env`, never in logs or the database.
- [ ] Redact recipient addresses and provider responses from logs where practical.
- [ ] Rate-limit reminder configuration writes and provider sends.
- [ ] Fail closed: never mark an email reminder sent when the provider is unavailable.
- [ ] Audit email reminder creation, cancellation, delivery, and final failure.
- [ ] Define retention for failed-delivery diagnostics and provider message IDs.
- [ ] Ensure recovery backups include reminder configuration but exclude provider credentials and email secrets.

## 8. Tests

- [ ] Unit: selecting two offsets with email enabled creates four reminders (two in-app, two email).
- [ ] Unit: email disabled creates only in-app reminders.
- [ ] Unit: changing offsets cancels only obsolete pending reminders.
- [ ] Unit: enabling/disabling email adds or cancels only email reminders.
- [ ] Unit: date/time/timezone changes reschedule both channels.
- [ ] Unit: delete/cancel/complete cancels both channels.
- [ ] Unit: provider acceptance marks exactly one email reminder sent and stores its provider ID.
- [ ] Unit: transient failure retries; permanent failure stops after the configured maximum.
- [ ] Unit: retries never send the same email twice.
- [ ] Integration: form displays account email and persists the optional checkbox.
- [ ] Integration: disabled provider state cannot create email reminders.
- [ ] Integration: User A cannot configure or inspect User B's email reminders.
- [ ] Integration: backup and restore preserve email reminder records without credentials.
- [ ] UI: keyboard and screen-reader labels identify the optional email checkbox and recipient.

## 9. Documentation and rollout

- [ ] Document environment variables and provider setup in README and `.env.example`.
- [ ] Document that email delivery is optional and in-app notifications remain available.
- [ ] Add administrator-facing health/readiness check for email configuration.
- [ ] Add metrics for queued, sent, retried, failed, and cancelled email reminders.
- [ ] Roll out behind an `EMAIL_REMINDERS_ENABLED` feature flag.
- [ ] Test with a non-production recipient before enabling for users.
