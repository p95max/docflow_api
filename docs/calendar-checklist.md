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
- [ ] Define event behavior when a linked document is deleted:
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

- [ ] Add a constraint for date-only events:
  - [ ] `all_day = true`
  - [ ] `start_date IS NOT NULL`
  - [ ] `start_at IS NULL`
- [ ] Add a constraint for datetime events:
  - [ ] `all_day = false`
  - [ ] `start_at IS NOT NULL`
- [ ] Validate `end_date >= start_date`
- [ ] Validate `end_at >= start_at`
- [ ] Add a unique index for `ical_uid`
- [ ] Add indexes:
  - [ ] `(owner_id, start_date)`
  - [ ] `(owner_id, start_at)`
  - [ ] `(owner_id, status)`
  - [ ] `(document_id)`
  - [ ] `(owner_id, deleted_at)`
- [ ] Prevent duplicate AI events through `source_key`

---

# MVP 2 — User Timezone

## 4. User Timezone

- [ ] Add `users.timezone`
- [ ] Default value: `Europe/Berlin`
- [ ] Validate it using `zoneinfo.ZoneInfo`
- [ ] Accept only IANA timezone identifiers
- [ ] Add a timezone setting to the user profile
- [ ] Remove the hard dependency of the web UI on `Europe/Berlin`
- [ ] Do not convert date-only events to UTC
- [ ] Store datetime events in UTC
- [ ] Preserve the original timezone separately
- [ ] Add DST-related tests

---

# MVP 3 — Calendar API

## 5. Pydantic Schemas

- [ ] Create `CalendarEventCreate`
- [ ] Create `CalendarEventUpdate`
- [ ] Create `CalendarEventRead`
- [ ] Create `CalendarEventListRead`
- [ ] Create `CalendarRangeQuery`
- [ ] Create `CalendarEventConfirm`
- [ ] Create `CalendarEventComplete`
- [ ] Validate date-only and datetime variants
- [ ] Prevent users from changing `owner_id`
- [ ] Prevent direct modification of `source=ai` outside the service layer

## 6. REST API

- [ ] `GET /api/v1/calendar/events`
- [ ] Support parameters:
  - [ ] `start`
  - [ ] `end`
  - [ ] `status`
  - [ ] `event_type`
  - [ ] `document_id`
  - [ ] `source`
- [ ] `POST /api/v1/calendar/events`
- [ ] `GET /api/v1/calendar/events/{id}`
- [ ] `PATCH /api/v1/calendar/events/{id}`
- [ ] `DELETE /api/v1/calendar/events/{id}`
- [ ] `POST /api/v1/calendar/events/{id}/confirm`
- [ ] `POST /api/v1/calendar/events/{id}/complete`
- [ ] `POST /api/v1/calendar/events/{id}/cancel`
- [ ] Verify ownership in every endpoint
- [ ] Use soft deletion
- [ ] Add rate limiting for bulk operations
- [ ] Add audit logs for create, update, delete, confirm, and complete actions

---

# MVP 4 — Calendar Service

## 7. Service Layer

- [ ] Create `app/services/calendar_events.py`
- [ ] Move the following operations into the service:
  - [ ] Create event
  - [ ] Update event
  - [ ] Delete event
  - [ ] Confirm event
  - [ ] Complete event
  - [ ] Cancel event
  - [ ] Retrieve events for a date range
  - [ ] Reconcile events with document data
- [ ] Do not place business logic in route handlers
- [ ] Make operations idempotent
- [ ] Add optimistic locking through `sequence` or `updated_at`
- [ ] When an AI event is manually edited:
  - [ ] Set `source = user`
  - [ ] Or set `detached_from_source = true`
- [ ] Do not overwrite manual changes during repeated AI analysis

---

# MVP 5 — AI Extraction of Temporal Events

## 8. Extend the AI Contract

- [ ] Do not remove the existing `due_date` and `action_deadline` fields
- [ ] Add a `temporal_events` array
- [ ] Extract the following for each event:
  - [ ] `event_type`
  - [ ] `title`
  - [ ] `date`
  - [ ] `datetime`
  - [ ] `all_day`
  - [ ] `timezone`, when explicitly present
  - [ ] `requires_action`
  - [ ] `confidence_score`
  - [ ] `evidence.quote`
  - [ ] `evidence.page_number`
- [ ] Limit the maximum number of events per document
- [ ] Disallow unknown free-form event types
- [ ] Return `null` for ambiguous dates
- [ ] Do not calculate relative dates without an explicit reference point
- [ ] Preserve the original phrase separately:
  - [ ] `within 14 days`
  - [ ] `bis zum 31.07.2026`
- [ ] Add source fields:
  - [ ] `due_date`
  - [ ] `action_deadline`
  - [ ] `contract_end`
  - [ ] `appointment_date`

## 9. Temporal Validation

- [ ] Create `app/services/calendar_event_validation.py`
- [ ] Validate dates against OCR text
- [ ] Validate page-specific evidence
- [ ] Support:
  - [ ] ISO dates
  - [ ] German dates
  - [ ] English dates
  - [ ] Numeric European dates
- [ ] Distinguish between:
  - [ ] A concrete date
  - [ ] A relative deadline
  - [ ] An example or placeholder
  - [ ] A document date
  - [ ] An event date
- [ ] Do not create events from template placeholders
- [ ] Validate that a deadline is not earlier than the document date
- [ ] Detect conflicts between multiple dates
- [ ] Return machine-readable issue codes
- [ ] Assign validation statuses:
  - [ ] `valid`
  - [ ] `warning`
  - [ ] `needs_review`
  - [ ] `failed`

---

# MVP 6 — Projection from Documents to Calendar

## 10. Calendar Projection

- [ ] Create `app/services/document_calendar_projection.py`
- [ ] Run it after:
  - [ ] Sanitization
  - [ ] Validation
  - [ ] Persistence of the extraction result
- [ ] Create events only from confirmed temporal candidates
- [ ] Create `suggested` events for warning-level candidates
- [ ] For `needs_review`:
  - [ ] Do not create an event
  - [ ] Or create a hidden suggestion
- [ ] Generate a stable `source_key`, for example:
  ```text
  document:{document_id}:payment_due:2026-07-31
  ```
- [ ] Do not create duplicates during repeated processing
- [ ] Update an existing suggestion when the AI result changes
- [ ] Do not update manually edited events
- [ ] Remove obsolete AI suggestions only when they have not been confirmed by the user
- [ ] Support reconciliation after manual document correction
- [ ] Add audit logs for projection create, update, and remove operations

---

# MVP 7 — Calendar UI

## 11. Main Calendar Page

- [ ] Add a `Calendar` item to the navbar
- [ ] Add the `/calendar` route
- [ ] Implement:
  - [ ] Month view
  - [ ] Agenda/list view
- [ ] Do not implement drag-and-drop in the first release
- [ ] Add navigation:
  - [ ] Previous month
  - [ ] Next month
  - [ ] Today
- [ ] Add filters:
  - [ ] Event type
  - [ ] Status
  - [ ] Source
  - [ ] Document
- [ ] Use colors by status, not by document type
- [ ] Display badges:
  - [ ] AI suggested
  - [ ] Confirmed
  - [ ] Completed
  - [ ] Cancelled
- [ ] Open an event detail drawer or page when an event is selected
- [ ] Add a document link to the event
- [ ] Add a `Related calendar events` section to the document page
- [ ] Add an `Add to calendar` button
- [ ] Add a `Create event from deadline` button
- [ ] Add a `Confirm suggestion` button
- [ ] Add a `Dismiss suggestion` button

## 12. Event Form

- [ ] Title field
- [ ] Description field
- [ ] Event type
- [ ] All-day checkbox
- [ ] Start date
- [ ] End date
- [ ] Start time
- [ ] End time
- [ ] Timezone
- [ ] Document link
- [ ] Status
- [ ] Reminder settings
- [ ] Display AI evidence as read-only data
- [ ] Warn before changing an AI-generated event
- [ ] Prevent `end < start`
- [ ] Warn about events in the past

---

# MVP 8 — Reminders

## 13. `EventReminder` Model

- [ ] Create the `event_reminders` table
- [ ] Add fields:
  - [ ] `id`
  - [ ] `event_id`
  - [ ] `channel`
  - [ ] `offset_minutes`
  - [ ] `scheduled_for`
  - [ ] `status`
  - [ ] `last_attempt_at`
  - [ ] `sent_at`
  - [ ] `attempts`
  - [ ] `error_message`
  - [ ] `created_at`
  - [ ] `updated_at`
- [ ] First-release channels:
  - [ ] `in_app`
  - [ ] `email`, optional
- [ ] Statuses:
  - [ ] `pending`
  - [ ] `sending`
  - [ ] `sent`
  - [ ] `failed`
  - [ ] `cancelled`
- [ ] Add a unique index to prevent duplicate delivery

## 14. Reminder Scheduler

- [ ] Add the Celery task `calendar.schedule_due_reminders`
- [ ] Add periodic execution through Celery Beat
- [ ] MVP frequency: every 5 minutes
- [ ] Select reminders using `SELECT ... FOR UPDATE SKIP LOCKED`
- [ ] Never deliver the same reminder twice
- [ ] Add retries with backoff
- [ ] Add a maximum attempt count
- [ ] Cancel reminders when an event is cancelled or deleted
- [ ] Recalculate reminders when an event date changes
- [ ] Respect the user's timezone
- [ ] Add metrics:
  - [ ] Queued
  - [ ] Sent
  - [ ] Failed
  - [ ] Delayed

## 15. In-App Notifications

- [ ] Create `notifications`
- [ ] Add fields:
  - [ ] `owner_id`
  - [ ] `event_id`
  - [ ] `title`
  - [ ] `body`
  - [ ] `read_at`
  - [ ] `created_at`
- [ ] Add a notification icon to the navbar
- [ ] Add an unread count
- [ ] Add the `/notifications` page
- [ ] Add mark-as-read
- [ ] Add mark-all-as-read

---

# MVP 9 — iCalendar Export

## 16. ICS Export

- [ ] Add `GET /api/v1/calendar/events/{id}.ics`
- [ ] Add `GET /api/v1/calendar/feed.ics`
- [ ] Use `VALUE=DATE` for date-only events
- [ ] Use UTC or `TZID` for datetime events
- [ ] Use a stable `UID`
- [ ] Increment `SEQUENCE` when the event changes
- [ ] Add `DTSTAMP`
- [ ] Escape special characters
- [ ] Correctly fold long lines
- [ ] Exclude deleted events
- [ ] Optionally include cancelled events with `STATUS:CANCELLED`
- [ ] Protect the private feed with a dedicated secret token
- [ ] Add feed token revocation and regeneration
- [ ] Do not place the access JWT in the calendar subscription URL

---

# MVP 10 — Google Calendar Integration

## 17. OAuth and Scopes

- [ ] Do not automatically reuse the Google Drive token
- [ ] Add separate Google Calendar OAuth consent
- [ ] Request the minimum required scope
- [ ] Store the Calendar connection separately
- [ ] Encrypt refresh tokens
- [ ] Support token rotation
- [ ] Add disconnect functionality
- [ ] Remove local external links on disconnect without deleting user events

## 18. Synchronization

- [ ] Start with one-way sync: DocsFlow → Google Calendar
- [ ] Do not implement two-way synchronization in the first release
- [ ] Create `external_calendar_links`
- [ ] Add fields:
  - [ ] `event_id`
  - [ ] `provider`
  - [ ] `external_calendar_id`
  - [ ] `external_event_id`
  - [ ] `external_etag`
  - [ ] `last_synced_at`
  - [ ] `sync_status`
  - [ ] `sync_error`
- [ ] Add event creation
- [ ] Add event update
- [ ] Add event deletion or cancellation
- [ ] Use idempotency
- [ ] Handle 401 and 403 responses
- [ ] Handle events deleted in Google Calendar
- [ ] Add a manual `Sync now` button
- [ ] Add a retry queue

---

# MVP 11 — Audit and Security

## 19. Audit Log

- [ ] Extend the audit log to support events without documents
- [ ] Make `document_id` nullable
- [ ] Add `calendar_event_id`
- [ ] Log:
  - [ ] Event created
  - [ ] Event updated
  - [ ] Event confirmed
  - [ ] Event completed
  - [ ] Event cancelled
  - [ ] Reminder created
  - [ ] Reminder sent
  - [ ] External synchronization
- [ ] Do not store secret tokens in the audit log
- [ ] Limit the size of old/new JSON values

## 20. Security

- [ ] Verify ownership in every query
- [ ] Use CSRF protection for web forms
- [ ] Add rate limiting to write endpoints
- [ ] Use soft deletion
- [ ] Protect private ICS feed tokens
- [ ] Do not expose another user's events through document relationships
- [ ] Verify permissions when navigating from an event to a document
- [ ] Sanitize titles and descriptions
- [ ] Limit field lengths
- [ ] Do not render AI descriptions as HTML
- [ ] Add IDOR security tests

---

# MVP 12 — Backup and Recovery

## 21. Backup

- [ ] Increment `BACKUP_SCHEMA_VERSION`
- [ ] Include in backups:
  - [ ] Calendar events
  - [ ] Event reminders
  - [ ] Notifications
  - [ ] External calendar links without secrets
- [ ] Add record counts
- [ ] Add a strict restore schema
- [ ] Restore document-to-event relationships
- [ ] Restore user-created events
- [ ] Do not restore old pending reminders as overdue deliveries
- [ ] Recalculate scheduled reminders after restore
- [ ] Do not restore OAuth refresh tokens from recovery backups

---

# MVP 13 — Testing

## 22. Unit Tests

- [ ] Date-only validation
- [ ] Datetime validation
- [ ] Timezone validation
- [ ] DST spring-forward
- [ ] DST fall-back
- [ ] `end >= start`
- [ ] Source key generation
- [ ] Duplicate prevention
- [ ] Projection idempotency
- [ ] Reconciliation
- [ ] Manual override protection
- [ ] Reminder scheduling
- [ ] Reminder idempotency
- [ ] ICS generation

## 23. Integration Tests

- [ ] An AI-processed document creates a suggested event
- [ ] A confirmed event links to its document
- [ ] Reprocessing does not duplicate an event
- [ ] Manual document correction updates the suggestion
- [ ] Manual event editing detaches it from the AI source
- [ ] A deleted document does not expose an inaccessible event
- [ ] Soft-deleted events are excluded from the calendar
- [ ] A reminder is delivered once
- [ ] Recovery backup restores calendar records
- [ ] User A cannot access User B's events
- [ ] A confidential document does not create an AI event until AI analysis is explicitly enabled

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
  - [ ] `sync_failures`
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

## Phase 6 — Google Calendar

- [ ] Separate OAuth flow
- [ ] One-way synchronization
- [ ] Synchronization status
- [ ] Retry handling

---

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
