# DocsFlow — Checklist for Implementing a Document-Linked Calendar

## 0. Architectural Decision

Architecture contract: [calendar-architecture.md](calendar-architecture.md)

- [x] Implement the calendar as a separate domain module, not merely as a view of `Document.deadline`
- [x] Keep `Document.deadline` for backward compatibility and document filtering
- [x] Define a separate `CalendarEvent` entity (implemented with its persistence model in MVP 1)
- [x] Separate responsibilities:
  - [x] AI extraction identifies dates and required actions
  - [x] Validation confirms dates and supporting evidence
  - [x] Calendar projection creates event suggestions
  - [x] Calendar service manages events
  - [x] Reminder service manages reminders
- [x] Do not allow AI to directly create confirmed events
- [x] Create AI-generated events with the `suggested` status

---

# MVP 1 — Core Document Calendar

## 1. `CalendarEvent` Model

- [x] Create `app/models/calendar_event.py`
- [x] Add fields:
  - [x] `id`
  - [x] `public_id` UUID
  - [x] `owner_id`
  - [x] `document_id`, nullable
  - [x] `title`
  - [x] `description`, nullable
  - [x] `event_type`
  - [x] `status`
  - [x] `source`
  - [x] `all_day`
  - [x] `start_date`, nullable
  - [x] `end_date`, nullable
  - [x] `start_at`, nullable
  - [x] `end_at`, nullable
  - [x] `timezone`, nullable
  - [x] `source_field`, nullable
  - [x] `source_key`, nullable
  - [x] `source_evidence`, JSON
  - [x] `confidence_score`, nullable
  - [x] `requires_review`
  - [x] `detached_from_source`
  - [x] `completed_at`, nullable
  - [x] `deleted_at`, nullable
  - [x] `ical_uid`
  - [x] `sequence`
  - [x] `created_at`
  - [x] `updated_at`
- [x] Add the `Document.calendar_events` relationship
- [x] Add the `User.calendar_events` relationship
- [x] Add `ondelete="CASCADE"` for the owner relationship
- [x] Define event behavior when a linked document is deleted:
  - [ ] Soft-delete the events together with the document
  - [x] Or preserve them as detached events
- [x] Create an Alembic migration

## 2. Enums

- [x] Create `CalendarEventType`
  - [x] `payment_due`
  - [x] `response_deadline`
  - [x] `action_deadline`
  - [x] `appointment`
  - [x] `contract_start`
  - [x] `contract_end`
  - [x] `cancellation_deadline`
  - [x] `renewal`
  - [x] `custom`
- [x] Create `CalendarEventStatus`
  - [x] `suggested`
  - [x] `confirmed`
  - [x] `completed`
  - [x] `cancelled`
- [x] Create `CalendarEventSource`
  - [x] `ai`
  - [x] `user`
  - [x] `system`
  - [x] `external`

## 3. Database Constraints

- [x] Add a constraint for date-only events:
  - [x] `all_day = true`
  - [x] `start_date IS NOT NULL`
  - [x] `start_at IS NULL`
- [x] Add a constraint for datetime events:
  - [x] `all_day = false`
  - [x] `start_at IS NOT NULL`
- [x] Validate `end_date >= start_date`
- [x] Validate `end_at >= start_at`
- [x] Add a unique index for `ical_uid`
- [x] Add indexes:
  - [x] `(owner_id, start_date)`
  - [x] `(owner_id, start_at)`
  - [x] `(owner_id, status)`
  - [x] `(document_id)`
  - [x] `(owner_id, deleted_at)`
- [x] Prevent duplicate AI events through `source_key`

---

# MVP 2 — User Timezone

## 4. User Timezone

- [x] Add `users.timezone`
- [x] Default value: `Europe/Berlin`
- [x] Validate it using `zoneinfo.ZoneInfo`
- [x] Accept only IANA timezone identifiers
- [x] Add a timezone setting to the user profile
- [x] Remove the hard dependency of the web UI on `Europe/Berlin`
- [x] Do not convert date-only events to UTC
- [x] Store datetime events in UTC
- [x] Preserve the original timezone separately
- [x] Add DST-related tests

---

# MVP 3 — Calendar API

## 5. Pydantic Schemas

- [x] Create `CalendarEventCreate`
- [x] Create `CalendarEventUpdate`
- [x] Create `CalendarEventRead`
- [x] Create `CalendarEventListRead`
- [x] Create `CalendarRangeQuery`
- [x] Create `CalendarEventConfirm`
- [x] Create `CalendarEventComplete`
- [x] Validate date-only and datetime variants
- [x] Prevent users from changing `owner_id`
- [x] Prevent direct modification of `source=ai` outside the service layer

## 6. REST API

- [x] `GET /api/v1/calendar/events`
- [x] Support parameters:
  - [x] `start`
  - [x] `end`
  - [x] `status`
  - [x] `event_type`
  - [x] `document_id`
  - [x] `source`
- [x] `POST /api/v1/calendar/events`
- [x] `GET /api/v1/calendar/events/{id}`
- [x] `PATCH /api/v1/calendar/events/{id}`
- [x] `DELETE /api/v1/calendar/events/{id}`
- [x] `POST /api/v1/calendar/events/{id}/confirm`
- [x] `POST /api/v1/calendar/events/{id}/complete`
- [x] `POST /api/v1/calendar/events/{id}/cancel`
- [x] Verify ownership in every endpoint
- [x] Use soft deletion
- [ ] Add rate limiting for bulk operations
- [x] Add audit logs for create, update, delete, confirm, and complete actions

---

# MVP 4 — Calendar Service

## 7. Service Layer

- [x] Create `app/services/calendar_events.py`
- [x] Move the following operations into the service:
  - [x] Create event
  - [x] Update event
  - [x] Delete event
  - [x] Confirm event
  - [x] Complete event
  - [x] Cancel event
  - [x] Retrieve events for a date range
  - [x] Reconcile events with document data
- [x] Do not place business logic in route handlers
- [x] Make operations idempotent
- [x] Add optimistic locking through `sequence` or `updated_at`
- [x] When an AI event is manually edited:
  - [x] Set `source = user`
  - [x] Or set `detached_from_source = true`
- [x] Do not overwrite manual changes during repeated AI analysis

---

# MVP 5 — AI Extraction of Temporal Events

## 8. Extend the AI Contract

- [x] Do not remove the existing `due_date` and `action_deadline` fields
- [x] Add a `temporal_events` array
- [x] Extract the following for each event:
  - [x] `event_type`
  - [x] `title`
  - [x] `date`
  - [x] `datetime`
  - [x] `all_day`
  - [x] `timezone`, when explicitly present
  - [x] `requires_action`
  - [x] `confidence_score`
  - [x] `evidence.quote`
  - [x] `evidence.page_number`
- [x] Limit the maximum number of events per document
- [x] Disallow unknown free-form event types
- [x] Return `null` for ambiguous dates
- [x] Do not calculate relative dates without an explicit reference point
- [x] Preserve the original phrase separately:
  - [x] `within 14 days`
  - [x] `bis zum 31.07.2026`
- [x] Add source fields:
  - [x] `due_date`
  - [x] `action_deadline`
  - [x] `contract_end`
  - [x] `appointment_date`

## 9. Temporal Validation

- [x] Create `app/services/calendar_event_validation.py`
- [x] Validate dates against OCR text
- [x] Validate page-specific evidence
- [x] Support:
  - [x] ISO dates
  - [x] German dates
  - [x] English dates
  - [x] Numeric European dates
- [x] Distinguish between:
  - [x] A concrete date
  - [x] A relative deadline
  - [x] An example or placeholder
  - [x] A document date
  - [x] An event date
- [x] Do not create events from template placeholders
- [x] Validate that a deadline is not earlier than the document date
- [x] Detect conflicts between multiple dates
- [x] Return machine-readable issue codes
- [x] Assign validation statuses:
  - [x] `valid`
  - [x] `warning`
  - [x] `needs_review`
  - [x] `failed`

---

# MVP 6 — Projection from Documents to Calendar

## 10. Calendar Projection

- [x] Create `app/services/document_calendar_projection.py`
- [x] Run it after:
  - [x] Sanitization
  - [x] Validation
  - [x] Persistence of the extraction result
- [x] Create events only from confirmed temporal candidates
- [x] Create `suggested` events for warning-level candidates
- [x] For `needs_review`:
  - [x] Do not create an event
- [x] Generate a stable `source_key`, for example:
  ```text
  document:{document_id}:payment_due:2026-07-31
  ```
- [x] Do not create duplicates during repeated processing
- [x] Update an existing suggestion when the AI result changes
- [x] Do not update manually edited events
- [x] Remove obsolete AI suggestions only when they have not been confirmed by the user
- [x] Support reconciliation after manual document correction
- [x] Add audit logs for projection create, update, and remove operations

---

# MVP 7 — Calendar UI

## 11. Main Calendar Page

- [x] Add a `Calendar` item to the navbar
- [x] Add the `/calendar` route
- [x] Implement:
  - [x] Month view
  - [x] Agenda/list view
- [x] Do not implement drag-and-drop in the first release
- [x] Add navigation:
  - [x] Previous month
  - [x] Next month
  - [x] Today
- [x] Add filters:
  - [x] Event type
  - [x] Status
  - [x] Source
  - [x] Document
- [x] Use colors by status, not by document type
- [x] Display badges:
  - [x] AI suggested
  - [x] Confirmed
  - [x] Completed
  - [x] Cancelled
- [x] Open an event detail drawer or page when an event is selected
- [x] Add a document link to the event
- [x] Add a `Related calendar events` section to the document page
- [x] Add an `Add to calendar` button
- [x] Add a `Create event from deadline` button
- [x] Add a `Confirm suggestion` button
- [x] Add a `Dismiss suggestion` button

## 12. Event Form

- [x] Title field
- [x] Description field
- [x] Event type
- [x] All-day checkbox
- [x] Start date
- [x] End date
- [x] Start time
- [x] End time
- [x] Timezone
- [x] Document link
- [x] Status
- [x] Reminder settings (shown as unavailable until MVP 8 delivery is implemented)
- [x] Display AI evidence as read-only data
- [x] Warn before changing an AI-generated event
- [x] Prevent `end < start`
- [x] Warn about events in the past

---

# MVP 8 — Reminders

## 13. `EventReminder` Model

- [x] Create the `event_reminders` table
- [x] Add fields:
  - [x] `id`
  - [x] `event_id`
  - [x] `channel`
  - [x] `offset_minutes`
  - [x] `scheduled_for`
  - [x] `status`
  - [x] `last_attempt_at`
  - [x] `sent_at`
  - [x] `attempts`
  - [x] `error_message`
  - [x] `created_at`
  - [x] `updated_at`
- [x] First-release channels:
  - [x] `in_app`
  - [x] `email`, optional
- [x] Statuses:
  - [x] `pending`
  - [x] `sending`
  - [x] `sent`
  - [x] `failed`
  - [x] `cancelled`
- [x] Add a unique index to prevent duplicate delivery

## 14. Reminder Scheduler

- [x] Add the Celery task `calendar.schedule_due_reminders`
- [x] Add periodic execution through Celery Beat
- [x] MVP frequency: every 5 minutes
- [x] Select reminders using `SELECT ... FOR UPDATE SKIP LOCKED`
- [x] Never deliver the same reminder twice
- [x] Add retries with backoff
- [x] Add a maximum attempt count
- [x] Cancel reminders when an event is cancelled or deleted
- [x] Recalculate reminders when an event date changes
- [x] Respect the user's timezone
- [x] Add metrics:
  - [x] Queued
  - [x] Sent
  - [x] Failed
  - [x] Delayed

## 15. In-App Notifications

- [x] Create `notifications`
- [x] Add fields:
  - [x] `owner_id`
  - [x] `event_id`
  - [x] `title`
  - [x] `body`
  - [x] `read_at`
  - [x] `created_at`
- [x] Add a notification icon to the navbar
- [x] Add an unread count
- [x] Add the `/notifications` page
- [x] Add mark-as-read
- [x] Add mark-all-as-read

---

# MVP 9 — iCalendar Export

## 16. ICS Export

- [x] Add `GET /api/v1/calendar/events/{id}.ics`
- [x] Add `GET /api/v1/calendar/feed.ics`
- [x] Add an `Add to Google Calendar` button that downloads a single-event `.ics` file
- [x] Keep `.ics` export provider-neutral: Google Calendar, Apple Calendar, and Outlook can import it
- [x] Do not add Google Calendar OAuth, account connections, or direct synchronization
- [x] Use `VALUE=DATE` for date-only events
- [x] Use UTC or `TZID` for datetime events
- [x] Use a stable `UID`
- [x] Increment `SEQUENCE` when the event changes
- [x] Add `DTSTAMP`
- [x] Escape special characters
- [x] Correctly fold long lines
- [x] Exclude deleted events
- [x] Optionally include cancelled events with `STATUS:CANCELLED`
- [x] Protect the private feed with a dedicated secret token
- [x] Add feed token revocation and regeneration
- [x] Do not place the access JWT in the calendar subscription URL

---

# MVP 11 — Audit and Security

## 19. Audit Log

- [x] Extend the audit log to support events without documents
- [x] Make `document_id` nullable
- [x] Add `calendar_event_id`
- [ ] Log:
  - [x] Event created
  - [x] Event updated
- [x] Event confirmed
- [x] Event completed
- [x] Event cancelled
- [x] Reminder created
- [x] Reminder sent
- [x] Do not store secret tokens in the audit log
- [x] Limit the size of old/new JSON values

## 20. Security

- [x] Verify ownership in every query
- [x] Use CSRF protection for web forms
- [x] Add rate limiting to write endpoints
- [x] Use soft deletion
- [x] Protect private ICS feed tokens
- [x] Do not expose another user's events through document relationships
- [x] Verify permissions when navigating from an event to a document
- [x] Sanitize titles and descriptions
- [x] Limit field lengths
- [x] Do not render AI descriptions as HTML
- [x] Add IDOR security tests

---

# MVP 12 — Backup and Recovery

## 21. Backup

- [x] Increment `BACKUP_SCHEMA_VERSION`
- [x] Include in backups:
  - [x] Calendar events
  - [x] Event reminders
  - [x] Notifications
- [x] Add record counts
- [x] Add a strict restore schema
- [x] Restore document-to-event relationships
- [x] Restore user-created events
- [x] Do not restore old pending reminders as overdue deliveries
- [x] Recalculate scheduled reminders after restore
- [x] Do not restore OAuth refresh tokens from recovery backups

---

# MVP 13 — Testing

## 22. Unit Tests

- [x] Date-only validation
- [x] Datetime validation
- [x] Timezone validation
- [x] DST spring-forward
- [x] DST fall-back
- [x] `end >= start`
- [x] Source key generation
- [x] Duplicate prevention
- [x] Projection idempotency
- [x] Reconciliation
- [x] Manual override protection
- [x] Reminder scheduling
- [x] Reminder idempotency
- [x] ICS generation

## 23. Integration Tests

- [x] An AI-processed document creates a suggested event
- [x] A confirmed event links to its document
- [x] Reprocessing does not duplicate an event
- [x] Manual document correction updates the suggestion
- [x] Manual event editing detaches it from the AI source
- [x] A deleted document does not expose an inaccessible event
- [x] Soft-deleted events are excluded from the calendar
- [x] A reminder is delivered once
- [x] Recovery backup restores calendar records
- [x] User A cannot access User B's events
- [x] A confidential document does not create an AI event until AI analysis is explicitly enabled

Coverage is implemented across `test_processing_jobs.py`,
`test_document_calendar_projection.py`, `test_calendar_api.py`,
`test_calendar_security.py`, `test_calendar_feed.py`,
`test_reminder_scheduler.py`, `test_backup_calendar_recovery.py`, and
`test_knowledge_document_scope_web.py`.

## 24. UI Tests

- [ ] Month navigation
- [ ] Empty state
- [ ] Suggested event badge
- [ ] Confirm event
- [ ] Complete event
- [ ] Event-to-document link
- [ ] Document-to-event link
- [ ] Date-only display
- [ ] Timezone display
- [ ] Mobile layout
- [ ] Keyboard navigation
- [ ] Accessible labels and focus states

---

# MVP 14 — Observability

## 25. Logging and Metrics

- [ ] Log projection results
- [ ] Log skipped events with a reason code
- [ ] Log reminder delivery
- [ ] Add counters:
  - [ ] `events_created`
  - [ ] `suggestions_created`
  - [ ] `suggestions_confirmed`
  - [ ] `reminders_sent`
  - [ ] `reminders_failed`
- [ ] Add structured logs with `owner_id`, `document_id`, and `event_id`
- [ ] Do not log full OCR text
- [ ] Add a Celery Beat health check
- [ ] Add an alert for a growing reminder backlog

---

# Recommended Implementation Order

## Phase 1 — Calendar Core

- [ ] `CalendarEvent` model
- [ ] Migration
- [ ] Schemas
- [ ] Service layer
- [ ] CRUD API
- [ ] Ownership permissions
- [ ] Audit logging
- [ ] Unit tests

## Phase 2 — Calendar UI

- [ ] `/calendar`
- [ ] Month view
- [ ] Agenda view
- [ ] Create, edit, and delete
- [ ] Link events with documents
- [ ] Related events on the document page

## Phase 3 — AI Suggestions

- [ ] Temporal event extraction
- [ ] Temporal validation
- [ ] Projection service
- [ ] Suggested, confirm, and dismiss workflow
- [ ] Reprocessing reconciliation

## Phase 4 — Reminders

- [ ] Reminder model
- [ ] Celery Beat scheduler
- [ ] In-app notifications
- [ ] Email delivery
- [ ] Delivery idempotency

## Phase 5 — Export

- [ ] Single-event ICS
- [ ] Private ICS feed
- [ ] Feed token revocation and regeneration

# Definition of Done for the First Calendar Release

- [ ] The user can view a month and a list of upcoming events
- [ ] The user can create an event manually
- [ ] An event can be linked to a document
- [ ] One document can have multiple events
- [ ] AI creates only `suggested` events
- [ ] The user can confirm or dismiss a suggestion
- [ ] Repeated AI analysis does not create duplicates
- [ ] AI does not overwrite manual changes
- [ ] Date-only events do not shift because of timezone conversion
- [ ] Every endpoint enforces ownership checks
- [ ] Calendar records are included in recovery backups
- [ ] Unit, integration, and security tests are present
