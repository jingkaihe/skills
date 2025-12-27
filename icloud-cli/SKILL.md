---
name: icloud-cli
description: Interact with iCloud services using the icloud CLI tool. Use when users want to manage iCloud calendars (list, create, update, delete calendars), manage calendar events (list, create, update, delete events with recurrence support), or manage iCloud email (list mailboxes, read/search emails, send/reply, manage drafts, move/delete/mark/flag emails). Triggers on mentions of iCloud, Apple calendar, iCloud email, calendar events, or scheduling tasks.
---

# iCloud CLI

CLI tool for interacting with iCloud calendar and email services.

## Prerequisites

The `icloud` binary must be available in PATH. At least one iCloud account must be configured before use.

## Account Management

```bash
# Add account (interactive or with flags/env vars)
icloud account add [-e EMAIL] [-p PASSWORD] [-n NAME] [--alias ALIAS]
# Env vars: APPLE_EMAIL, APPLE_APP_SPECIFIC_PASSWORD

# List configured accounts
icloud account list

# Set default account
icloud account set-default <account>

# Remove account
icloud account remove <account>
```

## Calendar Management

```bash
# List calendars
icloud calendar list -a ACCOUNT

# Create calendar
icloud calendar create <name> -a ACCOUNT

# Update calendar name
icloud calendar update <calendar-id> -n <new-name> -a ACCOUNT

# Delete calendar
icloud calendar delete <calendar-id> -a ACCOUNT
```

## Event Management

```bash
# List events (defaults to current week)
icloud event list -a ACCOUNT [-s START] [-e END] [-c CALENDAR_ID] [-o FORMAT]
# Dates: YYYY-MM-DD or relative (1d, 1w, -2d, 1m)
# Format: tsv (default) or json

# Get single event
icloud event get <event-uid> -c CALENDAR_ID -a ACCOUNT [-o FORMAT]

# Create event
icloud event create -t TITLE -s START -c CALENDAR_ID [-e END] [-l LOCATION] [-d DESCRIPTION] [-r RRULE] -a ACCOUNT
# Start/End: YYYY-MM-DD HH:MM or YYYY-MM-DDTHH:MM
# End defaults to 1 hour after start

# Update event
icloud event update <event-uid> -c CALENDAR_ID [-t TITLE] [-s START] [-e END] [-l LOCATION] [-d DESCRIPTION] [-r RRULE] [-S] [-o OCCURRENCE] -a ACCOUNT
# --series (-S): Update entire recurring series
# --occurrence (-o): Update single occurrence (YYYY-MM-DD HH:MM)

# Delete event
icloud event delete <event-uid> -c CALENDAR_ID -a ACCOUNT [-S] [-o OCCURRENCE]
```

### Recurrence Rules (RRULE)

```
FREQ=DAILY                          # Every day
FREQ=WEEKLY;BYDAY=MO,WE,FR          # Mon, Wed, Fri
FREQ=WEEKLY;INTERVAL=2              # Every 2 weeks
FREQ=MONTHLY;BYMONTHDAY=15          # 15th of each month
FREQ=DAILY;COUNT=10                 # Daily for 10 occurrences
FREQ=WEEKLY;UNTIL=20251231T235959Z  # Weekly until end of 2025
```

## Email Management

### Mailbox Operations

```bash
# List mailboxes
icloud email mailbox list -a ACCOUNT

# List emails
icloud email list -a ACCOUNT [-m MAILBOX] [-n LIMIT] [-o FORMAT]
# MAILBOX: INBOX (default), Sent Messages, Drafts, Trash, Junk, Archive
# LIMIT: default 20

# Get single email
icloud email get <uid> -m MAILBOX -a ACCOUNT [-o FORMAT]

# Search emails
icloud email search <query> -a ACCOUNT [-m MAILBOX] [-n LIMIT] [-o FORMAT]
# Criteria: FROM:, TO:, SUBJECT:, BODY:, SINCE:YYYY-MM-DD, BEFORE:YYYY-MM-DD, UNSEEN, FLAGGED
```

### Send & Reply

```bash
# Send email
icloud email send -t TO -s SUBJECT [-c CC] [-b BCC] [-B BODY] [-f FILE] -a ACCOUNT

# Reply to email
icloud email reply <uid> -m MAILBOX [-B BODY] [-f FILE] [--all] -a ACCOUNT
```

### Email Management

```bash
# Move email
icloud email move <uid> -m SOURCE -d DESTINATION -a ACCOUNT

# Delete email
icloud email delete <uid> -m MAILBOX -a ACCOUNT [-f]

# Mark read/unread
icloud email mark <uid> -m MAILBOX --read|--unread -a ACCOUNT

# Flag/unflag
icloud email flag <uid> -m MAILBOX --set|--unset -a ACCOUNT
```

### Draft Management

```bash
# Create draft
icloud email draft create -t TO -s SUBJECT [-c CC] [-B BODY] -a ACCOUNT

# List drafts
icloud email draft list -a ACCOUNT [-n LIMIT] [-o FORMAT]

# Get draft
icloud email draft get <uid> -a ACCOUNT [-o FORMAT]

# Update draft
icloud email draft update <uid> [-t TO] [-s SUBJECT] [-c CC] [-B BODY] -a ACCOUNT

# Delete draft
icloud email draft delete <uid> -a ACCOUNT [-f]

# Send draft
icloud email draft send <uid> -a ACCOUNT
```

## Common Workflows

### Schedule a meeting
```bash
icloud event create -a ACCOUNT -t "Team Standup" -s "2025-01-15 09:00" -e "2025-01-15 09:30" -c <calendar-id> -l "Zoom" -r "FREQ=DAILY;BYDAY=MO,TU,WE,TH,FR"
```

### Find and reply to email
```bash
icloud email search "FROM:boss@company.com UNSEEN" -a ACCOUNT -m INBOX
icloud email reply <uid> -a ACCOUNT -m INBOX -B "Thanks for the update!"
```

### Move emails to archive
```bash
icloud email move <uid> -a ACCOUNT -m INBOX -d Archive
```
