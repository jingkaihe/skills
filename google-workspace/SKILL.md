---
name: google-workspace
description: If you use the google-workspace MCP tool, read this skill first. Contains Gmail search syntax, common patterns, and up-to-date TypeScript signatures for email search, attachment downloads, and contact management.
---

# Google Workspace MCP Skill

Interact with Gmail and Google Contacts through the `google-workspace` MCP server.

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

### Email Operations
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

## Processing Downloaded Attachments

After downloading attachments, use appropriate skills:
- **PDF files**: Use `pdf` skill for text/table extraction
- **XLSX files**: Use `xlsx` skill for spreadsheet analysis
- **DOCX files**: Use `docx` skill for document processing
