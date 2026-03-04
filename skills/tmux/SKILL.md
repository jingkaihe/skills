---
name: tmux
description: Run interactive CLIs (python REPL, terraform apply) and long-running background tasks in isolated tmux sessions. Scrape pane output and manage session lifecycle. Use when a command needs interactive input, runs for a long time, or requires a TTY.
---

# tmux Session Manager

Run interactive CLIs and long-running tasks in isolated tmux sessions using a **dedicated socket** (`-L llm-agent` → `/tmp/tmux-llm-agent`) to avoid interfering with the user's own tmux. **Never omit `-L llm-agent`.**

## Commands

**Create session:**
```bash
tmux -L llm-agent new-session -d -s <name> '<command>'
```

**Send input** (interactive prompts, REPL input):
```bash
tmux -L llm-agent send-keys -t <name> '<text>' Enter
tmux -L llm-agent send-keys -t <name> C-c    # interrupt
tmux -L llm-agent send-keys -t <name> C-d    # EOF / exit REPL
```

**Capture pane output:**
```bash
tmux -L llm-agent capture-pane -t <name> -p            # visible screen
tmux -L llm-agent capture-pane -t <name> -p -S -<N>    # last N lines
tmux -L llm-agent capture-pane -t <name> -p -S -        # entire scrollback
```

**Session management:**
```bash
tmux -L llm-agent list-sessions
tmux -L llm-agent has-session -t <name> 2>/dev/null && echo "running" || echo "finished"
tmux -L llm-agent kill-session -t <name>
tmux -L llm-agent kill-server                            # kill ALL agent sessions
```

## Patterns

### Run and Poll (long-running task)
```bash
tmux -L llm-agent new-session -d -s my-build 'make build 2>&1'
sleep 5
tmux -L llm-agent capture-pane -t my-build -p -S -30          # check progress
tmux -L llm-agent has-session -t my-build 2>/dev/null && echo "running" || echo "done"
tmux -L llm-agent capture-pane -t my-build -p -S -             # grab final output
tmux -L llm-agent kill-session -t my-build
```

To keep the session alive after the command exits (so you can scrape final output):
```bash
tmux -L llm-agent new-session -d -s my-build
tmux -L llm-agent set-option -t my-build remain-on-exit on
tmux -L llm-agent send-keys -t my-build 'make build 2>&1' Enter
```

### Interactive CLI (REPL / wizard)
```bash
tmux -L llm-agent new-session -d -s repl 'python3'
sleep 1
tmux -L llm-agent send-keys -t repl 'import os; os.getcwd()' Enter
sleep 1
tmux -L llm-agent capture-pane -t repl -p -S -10
tmux -L llm-agent send-keys -t repl 'exit()' Enter
```

### Parallel tasks
```bash
tmux -L llm-agent new-session -d -s test-unit 'pytest tests/unit'
tmux -L llm-agent new-session -d -s test-integ 'pytest tests/integration'
tmux -L llm-agent list-sessions
tmux -L llm-agent capture-pane -t test-unit -p -S -20
```

## Rules

1. **Always use `-L llm-agent`** on every tmux command — isolates agent sessions from the user's personal tmux.
2. **After starting a session, always print these commands** so the user can access it:
   ```
   Session "<name>" started. To attach: tmux -L llm-agent attach -t <name>
   To capture output: tmux -L llm-agent capture-pane -t <name> -p -S -
   ```
3. **Use `sleep 1-2s`** between `send-keys` and `capture-pane` to let the process produce output.
4. **Session names**: lowercase with hyphens (e.g., `terraform-plan`, `django-server`).
5. **Redirect stderr** with `2>&1` to capture errors in the pane scrollback.
6. **For very long output**, bump the history limit:
   ```bash
   tmux -L llm-agent set-option -t <name> history-limit 50000
   ```
7. **Clean up** sessions when done. Don't leave orphans running.
