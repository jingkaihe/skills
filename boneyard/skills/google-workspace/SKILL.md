---
name: google-workspace
description: Use whenever working with Gmail, Google Calendar, Google Contacts, or Google Drive through the google-workspace MCP server. Covers the maco Python wrapper workflow, tool discovery, account selection, Gmail queries, attachments, event timing, Drive exports, and safe mutations.
compatibility: Requires mcp__maco_bash and mcp__maco_code_execute with a configured google-workspace MCP server.
---

# Google Workspace MCP

Use the generated maco Python wrappers to interact with Gmail, Google Calendar, Google Contacts, and Google Drive.

## Execution model

1. Use `mcp__maco_bash` to inspect generated wrappers when a signature or return field is uncertain.
2. Use `mcp__maco_code_execute` to call the MCP tools. It writes and runs Python in the maco scratch environment.
3. Import functions and Pydantic input models from `tools.google_workspace`.
4. Await every tool function inside an async entry point.
5. Treat the generated wrappers as the source of truth. Do not edit them.

The old TypeScript `.kodelet/mcp/servers/google-workspace` and `code_execution` workflow is obsolete. Tool functions and fields now use `snake_case`.

### Discover tools and signatures

Use the generated tools root advertised in the `mcp__maco_code_execute` tool description:

```bash
TOOLS_ROOT="/absolute/path/to/the/generated/tools"
fd . "$TOOLS_ROOT/google_workspace" -t f
sed -n '1,220p' "$TOOLS_ROOT/google_workspace/gmail_search_emails.py"
rg "^class .*Input|^    [a-zA-Z_].*Field|^async def" \
  "$TOOLS_ROOT/google_workspace/calendar_create_event.py"
```

Inspect `google_workspace/__init__.py` when you need the complete export list. Inspect the individual wrapper before a less-common or mutating operation rather than guessing its fields.

### Standard call pattern

```python
import asyncio
from tools.google_workspace import (
    GmailSearchEmailsInput,
    gmail_list_accounts,
    gmail_search_emails,
)


async def main():
    account_result = await gmail_list_accounts()
    if len(account_result.accounts) != 1:
        raise RuntimeError(f"Select one account from {account_result.accounts}")
    account = account_result.accounts[0]

    result = await gmail_search_emails(
        GmailSearchEmailsInput(
            account=account,
            query="from:billing@example.com after:2026/07/01 has:attachment",
            max_results=50,
        )
    )

    for email in result.emails:
        print(email.date, email.from_, email.subject)


asyncio.run(main())
```

Generated results are Pydantic models, not dictionaries. Read values as attributes such as `result.emails`, `email.subject`, and `event.start.date_time`. Use `model_dump(by_alias=True)` only when a dictionary with wire-format field names is useful.

Independent calls can run concurrently with `asyncio.gather`. Keep dependent calls sequential, such as listing accounts before calling a service with the selected account.

## Account selection

- Call `gmail_list_accounts` before Gmail, Contacts, or Calendar operations.
- Call `drive_list_accounts` before Drive operations.
- If exactly one account is connected, use it.
- If several accounts are connected, select from explicit user context. Ask a narrow question before a mutation when the intended account is ambiguous.
- Do not print account addresses, message bodies, contact details, or file contents unless they are relevant to the requested result.

## Python field aliases

Some wire-format names are adjusted into valid or idiomatic Python identifiers:

| Wire name | Python attribute |
|---|---|
| `from` | `from_` |
| `data_base64` | `data_base_64` |
| `md5_checksum` | `md_5_checksum` |
| `sha1_checksum` | `sha_1_checksum` |
| `sha256_checksum` | `sha_256_checksum` |

Input models accept aliases because they use Pydantic's `populate_by_name`, but prefer the generated Python field name after inspecting the wrapper. Output values must be read through the Python attribute, such as `download.data_base_64`.

## Gmail workflows

### Search mail

Use `gmail_search_emails` for a bounded search of up to 100 results. Use `gmail_list_emails` when pagination is required.

```python
from tools.google_workspace import GmailSearchEmailsInput, gmail_search_emails

result = await gmail_search_emails(
    GmailSearchEmailsInput(
        account=account,
        query="from:boss@company.com is:unread has:attachment",
        max_results=25,
    )
)

for email in result.emails:
    print({
        "id": email.id,
        "date": email.date,
        "from": email.from_,
        "subject": email.subject,
        "attachments": [a.filename for a in email.attachments or []],
    })
```

Search results include bodies. Avoid dumping the complete models when metadata is enough.

### Gmail search syntax

| Operator | Example | Meaning |
|---|---|---|
| `from:` | `from:john@example.com` | Sender |
| `to:` | `to:jane@example.com` | Recipient |
| `subject:` | `subject:"quarterly report"` | Subject text |
| `has:attachment` | `has:attachment` | Has attachments |
| `is:unread` | `is:unread` | Unread mail |
| `is:starred` | `is:starred` | Starred mail |
| `label:` | `label:important` | Label name or ID |
| `newer_than:` | `newer_than:7d` | Relative age |
| `older_than:` | `older_than:1m` | Relative age |
| `after:` | `after:2026/07/01` | After date |
| `before:` | `before:2026/08/01` | Before date |

Combine operators in one query. Quote phrases that contain spaces.

### Download an attachment

```python
import base64
from pathlib import Path
from tools.google_workspace import GmailGetAttachmentInput, gmail_get_attachment

download = await gmail_get_attachment(
    GmailGetAttachmentInput(
        account=account,
        message_id=email.id,
        attachment_id=attachment.id,
    )
)

safe_name = Path(attachment.filename).name
output = Path(safe_name)
output.write_bytes(base64.b64decode(download.data_base_64))
print(output.resolve())
```

Sanitize attachment names with `Path(...).name`, do not print the base64 payload, and report the resolved output path.

### Send or reply to mail

```python
from tools.google_workspace import GmailSendEmailInput, gmail_send_email

sent = await gmail_send_email(
    GmailSendEmailInput(
        account=account,
        to=["recipient@example.com"],
        cc=["copy@example.com"],
        subject="Project update",
        body="Plain-text body",
        html_body="<p>Optional HTML body</p>",
        # reply_to="original_message_id",
        # thread_id="original_thread_id",
    )
)
print(sent.message, sent.email_id)
```

Only call `gmail_send_email` when the user asked to send. If they asked to draft, return the draft without invoking the tool. For attachments, inspect `gmail_send_email.py`; attachment items contain `filename`, `data_base_64`, and optional `mime_type`.

### Contacts

Use `gmail_search_contacts` for name or email lookup. Contact mutation IDs are `resource_name` values, not display names.

```python
from tools.google_workspace import GmailSearchContactsInput, gmail_search_contacts

result = await gmail_search_contacts(
    GmailSearchContactsInput(account=account, query="Alex", page_size=30)
)
for contact in result.contacts:
    print(contact.resource_name, contact.names, contact.emails)
```

Google Contacts requires the People API to be enabled for the configured Google Cloud project. If a contact tool returns `403`, `SERVICE_DISABLED`, or `People API has not been used`, report the configuration issue instead of retrying the same call. Gmail, Calendar, and Drive may still be available.

## Calendar workflows

### List or search events

- Use `calendar_list_events` for a time range and optional pagination.
- Use `calendar_search_events` for text search with optional `time_min` and `time_max`.
- Set `single_events=True` when expanded recurring instances are needed.
- Use RFC3339 timestamps with explicit offsets for range filters.

### Create an all-day event

```python
from tools.google_workspace import CalendarCreateEventInput, calendar_create_event

event = await calendar_create_event(
    CalendarCreateEventInput(
        account=account,
        summary="Out of office",
        description="Annual leave",
        start_date="2026-08-10",
        end_date="2026-08-13",  # exclusive: covers August 10-12
        visibility="public",
        use_default_reminders=False,
    )
)
print(event.id, event.html_link)
```

For all-day events, use `start_date` and `end_date` in `YYYY-MM-DD` format. Google Calendar treats `end_date` as exclusive.

### Create a timed event with Google Meet

```python
event = await calendar_create_event(
    CalendarCreateEventInput(
        account=account,
        summary="Project discussion",
        description="Review project status and next steps",
        attendees=["attendee@example.com"],
        start_date_time="2026-08-04T10:00:00-04:00",
        end_date_time="2026-08-04T11:00:00-04:00",
        time_zone="America/New_York",
        add_conferencing=True,
        use_default_reminders=True,
    )
)

meet_link = None
if event.conference_data and event.conference_data.entry_points:
    meet_link = next(
        (point.uri for point in event.conference_data.entry_points
         if point.entry_point_type == "video"),
        None,
    )
print(event.id, meet_link)
```

For timed events, use `start_date_time` and `end_date_time`. Include both an explicit UTC offset and the IANA `time_zone` when the user's locale matters.

### Availability and updates

- Use `calendar_get_free_busy` to retrieve busy intervals.
- Use `calendar_find_available_slots` to find common slots of at least `duration_minutes`.
- `calendar_update_event.attendees` replaces the complete attendee list. Fetch the event first when preserving existing attendees.
- Prefer `calendar_update_reminders` for reminder-only changes. With `use_default=True`, omit custom reminders; with custom reminders, set `use_default=False`.
- Recurrence rules use strings such as `RRULE:FREQ=WEEKLY;COUNT=10`.

If `calendar_get_free_busy` fails with an `Invalid structured content` error saying `busy` is `None` rather than an array, this is a server/schema edge case for an empty busy result, not an authentication failure. Use `calendar_find_available_slots` for availability or `calendar_list_events` with `single_events=True` as a read-only fallback, and do not blindly retry the identical call.

## Drive workflows

### Search or list files

```python
from tools.google_workspace import (
    DriveListFilesInput,
    drive_list_accounts,
    drive_list_files,
)

account_result = await drive_list_accounts()
if len(account_result.accounts) != 1:
    raise RuntimeError(f"Select one Drive account from {account_result.accounts}")
drive_account = account_result.accounts[0]

result = await drive_list_files(
    DriveListFilesInput(
        account=drive_account,
        query="trashed = false and name contains 'Quarterly'",
        order_by="modifiedTime desc",
        page_size=100,
    )
)

for file in result.files:
    print(file.id, file.name, file.mime_type, file.modified_time)
```

Use `parent_id` to list a folder's children and `folders_only=True` to return only folders. Follow `next_page_token` when the request requires all results.

### Download or export a file

```python
import base64
from pathlib import Path
from tools.google_workspace import DriveDownloadFileInput, drive_download_file

download = await drive_download_file(
    DriveDownloadFileInput(
        account=drive_account,
        file_id=file_id,
        # For a native Google file, optionally choose an export format:
        # export_mime_type="application/pdf",
    )
)

safe_name = Path(download.suggested_file_name or download.file.name).name
output = Path(safe_name)
output.write_bytes(base64.b64decode(download.data_base_64))
print(output.resolve(), download.mime_type, download.exported)
```

Native Google Workspace files require export. `drive_download_file` can use its default export format or an explicit `export_mime_type`, for example:

| Source | Common export MIME type |
|---|---|
| Google Docs | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| Google Sheets | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| Google Slides | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| Any supported native file | `application/pdf` |

Use `drive_upload_file` for binary content encoded as base64. Use `drive_create_file` for metadata-only files or native Google Docs/Sheets/Slides, and `drive_create_folder` for folders.

## Pagination and concise output

`gmail_list_emails`, `gmail_list_contacts`, `calendar_list_events`, and `drive_list_files` expose `next_page_token`. Continue with that token only when the user needs exhaustive results; otherwise bound the request and return the most relevant entries.

Do not print full Pydantic models containing email bodies or base64 data. Print or return only fields needed for the task.

## Mutating operations

Sending mail, changing labels, creating filters or contacts, altering calendars, and modifying Drive all have side effects.

- If the user clearly requested the exact action, execute it and report the resulting ID/status.
- If the user requested a draft, preview, search, or recommendation, do not mutate anything.
- Resolve the account, target ID, recipients/attendees, and important dates before a mutation.
- Prefer recoverable actions: use `gmail_trash_email` instead of `gmail_delete_email` unless permanent deletion was requested. `drive_delete_file` is permanent.
- Inspect the generated wrapper before delete, move, update, filter, recurrence, or attachment-send operations.

## Available tool groups

### Gmail and Contacts

- Accounts/mail: `gmail_list_accounts`, `gmail_list_emails`, `gmail_get_email`, `gmail_search_emails`, `gmail_send_email`
- Message actions: `gmail_mark_read`, `gmail_mark_unread`, `gmail_star_email`, `gmail_unstar_email`, `gmail_archive_email`, `gmail_trash_email`, `gmail_delete_email`, `gmail_modify_labels`
- Attachments: `gmail_get_attachment`
- Labels: `gmail_list_labels`, `gmail_create_label`, `gmail_update_label`, `gmail_delete_label`
- Filters: `gmail_list_filters`, `gmail_create_filter`, `gmail_delete_filter`
- Contacts: `gmail_list_contacts`, `gmail_search_contacts`, `gmail_create_contact`, `gmail_update_contact`, `gmail_delete_contact`

### Calendar

- Events: `calendar_list_events`, `calendar_search_events`, `calendar_get_event`, `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`, `calendar_delete_future_events`, `calendar_quick_add_event`, `calendar_get_recurring_instances`
- Calendars: `calendar_list_calendars`, `calendar_create_calendar`, `calendar_update_calendar`, `calendar_delete_calendar`
- Conferencing/reminders: `calendar_add_conferencing`, `calendar_update_reminders`
- Scheduling/actions: `calendar_get_free_busy`, `calendar_find_available_slots`, `calendar_respond_to_event`, `calendar_move_event`

### Drive

- Accounts/browse: `drive_list_accounts`, `drive_list_files`, `drive_get_file`
- Download/upload: `drive_download_file`, `drive_upload_file`
- Create/update: `drive_create_file`, `drive_create_folder`, `drive_update_file`, `drive_move_file`
- Delete: `drive_delete_file`

## Processing downloaded files

After writing downloaded bytes to a resolved local path, invoke the matching file skill when further processing is requested:

- PDF: `pdf`
- Excel/CSV: `xlsx`
- Word: `docx`
- PowerPoint: `pptx`
