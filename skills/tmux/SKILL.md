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
tmux -L llm-agent capture-pane -t <name> -p -S -<N>    # last N lines of scrollback
tmux -L llm-agent capture-pane -t <name> -p -S -        # entire scrollback
```
Pipe through `grep`/`tail` for targeted extraction:
```bash
tmux -L llm-agent capture-pane -t <name> -p -S -100 | grep -i 'error\|fail\|warn'
tmux -L llm-agent capture-pane -t <name> -p -S -50 | tail -20
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
By default the session closes when the command exits, so use `remain-on-exit` to keep the pane alive for scraping final output:
```bash
tmux -L llm-agent new-session -d -s my-build
tmux -L llm-agent set-option -t my-build remain-on-exit on
tmux -L llm-agent send-keys -t my-build 'make build 2>&1' Enter
sleep 5
tmux -L llm-agent capture-pane -t my-build -p -S -30          # check progress
# when done, grab final output and clean up
tmux -L llm-agent capture-pane -t my-build -p -S -
tmux -L llm-agent kill-session -t my-build
```

If you don't need final output, the simpler form works — the session auto-closes on exit:
```bash
tmux -L llm-agent new-session -d -s my-build 'make build 2>&1'
tmux -L llm-agent has-session -t my-build 2>/dev/null && echo "running" || echo "done"
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

### Wait for keyword
Use the bundled `scripts/wait-for.sh` to poll a pane until a pattern appears (or timeout). It uses the `-L llm-agent` socket automatically and `-J` to join wrapped lines.
```bash
bash <skill-dir>/scripts/wait-for.sh -t <name> -p 'Apply complete' -T 60
bash <skill-dir>/scripts/wait-for.sh -t <name> -p 'Enter a value' -F        # fixed string
bash <skill-dir>/scripts/wait-for.sh -t <name> -p 'ready' -i 1 -l 500       # custom interval/depth
```
Options: `-t` target, `-p` pattern, `-F` fixed string, `-T` timeout (default 30s), `-i` poll interval (default 0.5s), `-l` scrollback lines (default 1000). On timeout exits non-zero and dumps the last N lines to stderr.

Example — wait for terraform prompt, respond, then wait for completion:
```bash
tmux -L llm-agent new-session -d -s tf 'terraform apply'
bash <skill-dir>/scripts/wait-for.sh -t tf -p 'Enter a value' -T 120
tmux -L llm-agent send-keys -t tf 'yes' Enter
bash <skill-dir>/scripts/wait-for.sh -t tf -p 'Apply complete' -T 300
tmux -L llm-agent capture-pane -t tf -p -S -
tmux -L llm-agent kill-session -t tf
```

## Rules

1. **Always use `-L llm-agent`** on every tmux command — isolates agent sessions from the user's personal tmux.
2. **After starting a session, always print these commands** so the user can access it:
   ```
   Session "<name>" started. To attach: tmux -L llm-agent attach -t <name>
   To capture output: tmux -L llm-agent capture-pane -t <name> -p -S -
   ```
3. **Session names**: lowercase with hyphens (e.g., `terraform-plan`, `django-server`).
4. **Redirect stderr** with `2>&1` to capture errors in the pane scrollback.
5. **For very long output**, bump the history limit:
   ```bash
   tmux -L llm-agent set-option -t <name> history-limit 50000
   ```
6. **Clean up** sessions when done. Don't leave orphans running.
