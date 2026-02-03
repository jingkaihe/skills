---
name: google-workspace
description: If you use the google-workspace MCP tool, read this skill first. Contains Gmail search syntax, Google Calendar patterns, and up-to-date TypeScript signatures for email, calendar, and contact management.
---

# Google Workspace MCP Skill

Interact with Gmail, Google Calendar, and Google Contacts through the `google-workspace` MCP server.

## Quick Start

1. Discover available tools:
```bash
ls .kodelet/mcp/servers/google-workspace/
```

2. Read function signatures before use:
```bash
file_read .kodelet/mcp/servers/google-workspace/gmailSearchEmails.ts
```

3. Write TypeScript to `.kodelet/mcp/` and execute via `code_execution`.

## Common Workflows

### List Accounts (Always First)
```typescript
import * as gmail from './servers/google-workspace/index.js';

const { accounts } = await gmail.gmailListAccounts({});
const account = accounts[0];
```

### Search Emails
```typescript
const results = await gmail.gmailSearchEmails({
  query: 'from:billing@example.com after:2025/01/01 has:attachment',
  account: account,
  max_results: 50
});

for (const email of results.emails) {
  console.log(`${email.date} | ${email.from} | ${email.subject}`);
}
```

### Download Attachments
```typescript
import * as fs from 'fs';

for (const email of results.emails) {
  if (email.attachments) {
    for (const att of email.attachments) {
      const data = await gmail.gmailGetAttachment({
        account: account,
        message_id: email.id,
        attachment_id: att.id
      });
      const buffer = Buffer.from(data.data_base64, 'base64');
      fs.writeFileSync(`/tmp/${att.filename}`, buffer);
    }
  }
}
```

### Send Email
```typescript
await gmail.gmailSendEmail({
  account: account,
  to: ['recipient@example.com'],
  subject: 'Hello',
  body: 'Plain text body',
  html_body: '<p>HTML body</p>'  // optional
});
```

### Search Contacts
```typescript
const contacts = await gmail.gmailSearchContacts({
  account: account,
  query: 'John'
});
```

### Create All-Day Event (OOO, Holiday Block)
```typescript
import * as gw from './servers/google-workspace/index.js';

const { accounts } = await gw.gmailListAccounts({});
const account = accounts[0];

// end_date is EXCLUSIVE - '2026-01-05' means event covers through Jan 4
const result = await gw.calendarCreateEvent({
  account: account,
  summary: '🎄 OOO - Christmas Holiday',
  description: 'Out of office for holiday.',
  start_date: '2025-12-22',      // YYYY-MM-DD format
  end_date: '2026-01-05',        // Exclusive - event ends Jan 4
  visibility: 'public',
  use_default_reminders: false   // Recommended for OOO blocks
});
```

### Create Timed Event with Google Meet
```typescript
const result = await gw.calendarCreateEvent({
  account: account,
  summary: 'Meeting: Project Discussion',
  description: 'Agenda: Review project status',
  attendees: ['attendee@example.com'],
  start_date_time: '2026-01-06T20:00:00',  // RFC3339 format
  end_date_time: '2026-01-06T21:00:00',
  time_zone: 'Europe/London',
  add_conferencing: true,        // Adds Google Meet
  use_default_reminders: true
});

// Get Google Meet link from response
if (result.conference_data?.entry_points) {
  const meetLink = result.conference_data.entry_points.find(e => e.entry_point_type === 'video');
  console.log(`Google Meet: ${meetLink?.uri}`);
}
```

### Update Event Attendees
```typescript
await gw.calendarUpdateEvent({
  account: account,
  event_id: 'event_id_here',
  attendees: ['corrected.email@example.com']
});
```

### Delete and Recreate Event (Workaround for Reminder Bugs)
When updating events, you may encounter "Cannot specify both default reminders and overrides" error. The workaround is to delete and recreate:
```typescript
await gw.calendarDeleteEvent({
  account: account,
  event_id: 'old_event_id'
});

const result = await gw.calendarCreateEvent({
  account: account,
  // ... new event properties
  use_default_reminders: false  // Explicitly set to avoid conflicts
});
```

## Gmail Search Query Syntax

| Operator | Example | Description |
|----------|---------|-------------|
| `from:` | `from:john@example.com` | Emails from sender |
| `to:` | `to:jane@example.com` | Emails to recipient |
| `subject:` | `subject:meeting` | Subject contains |
| `has:attachment` | `has:attachment` | Has attachments |
| `is:unread` | `is:unread` | Unread emails |
| `is:starred` | `is:starred` | Starred emails |
| `label:` | `label:important` | Has label |
| `newer_than:` | `newer_than:7d` | Last 7 days |
| `older_than:` | `older_than:1m` | Older than 1 month |
| `after:` | `after:2025/01/01` | After date |
| `before:` | `before:2025/12/31` | Before date |

Combine operators: `from:boss@company.com is:unread has:attachment`

## Available Tools

### Email Operations (Gmail)
- `gmailListAccounts` - List connected accounts (call first!)
- `gmailListEmails` - List emails with query/labels
- `gmailGetEmail` - Get email by ID
- `gmailSearchEmails` - Search emails
- `gmailSendEmail` - Send email (HTML, CC, BCC, attachments)

### Email Actions
- `gmailMarkRead` / `gmailMarkUnread`
- `gmailStarEmail` / `gmailUnstarEmail`
- `gmailArchiveEmail` - Remove from inbox
- `gmailTrashEmail` / `gmailDeleteEmail`
- `gmailModifyLabels` - Add/remove labels

### Labels
- `gmailListLabels` / `gmailCreateLabel` / `gmailUpdateLabel` / `gmailDeleteLabel`

### Attachments
- `gmailGetAttachment` - Returns base64 data

### Filters
- `gmailListFilters` / `gmailCreateFilter` / `gmailDeleteFilter`

### Contacts
- `gmailListContacts` / `gmailSearchContacts`
- `gmailCreateContact` / `gmailUpdateContact` / `gmailDeleteContact`

### Calendar Events
- `calendarListEvents` / `calendarSearchEvents` / `calendarGetEvent`
- `calendarCreateEvent` - Create with attendees, Google Meet, reminders
- `calendarUpdateEvent` - Modify existing event
- `calendarDeleteEvent` / `calendarDeleteFutureEvents`
- `calendarQuickAddEvent` - Create from natural language string
- `calendarGetRecurringInstances` - Get instances of recurring event

### Calendar Management
- `calendarListCalendars` / `calendarCreateCalendar` / `calendarUpdateCalendar` / `calendarDeleteCalendar`
- `calendarAddConferencing` - Add Google Meet to existing event
- `calendarUpdateReminders` - Modify event reminders
- `calendarMoveEvent` - Move to different calendar
- `calendarRespondToEvent` - Accept/decline/tentative
- `calendarFindAvailableSlots` / `calendarGetFreeBusy` - Check availability

## Key Calendar Notes

| Concept | Details |
|---------|---------|
| **All-day events** | Use `start_date` / `end_date` in `YYYY-MM-DD` format |
| **Timed events** | Use `start_date_time` / `end_date_time` in RFC3339 format |
| **end_date is exclusive** | `end_date: '2026-01-05'` means event covers through Jan 4 |
| **Google Meet** | Set `add_conferencing: true` |
| **Reminder bug** | Update can fail with "Cannot specify both default reminders and overrides" - workaround: delete + recreate |

## Processing Downloaded Attachments

After downloading attachments, use appropriate skills:
- **PDF files**: Use `pdf` skill for text/table extraction
- **XLSX files**: Use `xlsx` skill for spreadsheet analysis
- **DOCX files**: Use `docx` skill for document processing
