# Calendar Architecture Decision

## Status

Accepted for the first DocsFlow calendar release.

## Boundary

Calendar is a separate domain, not a derived view of `Document.deadline`.
`Document.deadline` remains the extracted, searchable document field used by
the existing document UI and filters. Calendar events will be represented by a
separate `CalendarEvent` entity linked to a document only when applicable.

This lets a user keep a manually created event, record several events for one
document, or retain an event after its document is no longer available.

## Responsibilities

| Component | Responsibility |
| --- | --- |
| AI extraction | Identifies dates, actions, and source evidence in a document. |
| Validation | Checks extracted dates and their evidence. |
| Calendar projection | Converts validated document data into event suggestions. |
| Calendar service | Creates, updates, confirms, completes, and cancels events. |
| Reminder service | Schedules and delivers reminders for eligible events. |

## Safety rules

- AI extraction never creates a confirmed calendar event directly.
- The projection layer may create only `suggested` events with source `ai`.
- A user or calendar service must explicitly confirm a suggestion.
- Manual event edits detach the event from an AI suggestion; later projection
  must not overwrite them.
- The calendar module owns event lifecycle and reminder logic. Document
  processing only supplies source facts and does not own calendar state.

## Delivery boundary for this decision

This decision intentionally adds no calendar UI, API, projections, reminders,
or Google Calendar synchronization. The `CalendarEvent` persistence model is
introduced in MVP 1; the remaining capabilities are implemented in subsequent
MVP sections.
