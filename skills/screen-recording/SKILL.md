---
name: screen-recording
description: Record terminal demos, browser screencasts, and synchronized TUI/Web UI walkthroughs, including on headless Linux.
---

# Screen recording

Produce a real, readable demonstration, not merely a video file that encodes successfully. Capture at the intended resolution, preserve the event timeline, inspect the final encoded playback, and publish only after the visual checks pass.

Read `references/headless-capture.md` for the concrete terminal/browser workflow, PNG timing, FFmpeg commands, and troubleshooting. Use the **tmux** skill for interactive applications and long-running capture/encoding; use **uv** for Python dependencies. Prefer existing browser tooling through **maco** when available.

## Choose the smallest capture path

| Need | Default |
| --- | --- |
| Terminal only | Record a real PTY with asciinema; embed the cast with playback controls. No MP4 is necessary unless requested. |
| Browser only, especially text-heavy UI | Capture native-resolution PNG frames with Playwright, then encode once with FFmpeg. |
| Terminal and browser streaming together | Render the real terminal stream in xterm.js beside the real Web UI and capture both on one timeline. |
| Native desktop applications | Use an actual desktop capture session and a lossless master. Check display dimensions before recording; a browser-only compositor cannot demonstrate native window behavior. |

For a headless terminal/browser demonstration, a browser compositor avoids needing a desktop or virtual display. Keep the actual application UI and actual terminal output; do not replace them with fabricated progress or answers.

## Non-negotiable safeguards

- **Never stop or restart an existing daemon to prepare a demo.** It may be driving the current conversation. Reuse it only with permission and a dedicated demo conversation, or create a separately configured daemon with its own state, port, and authentication. Every management command must carry that isolated configuration.
- Inventory existing processes, tmux sessions, and browser tabs first. Use a uniquely named session on `tmux -L llm-agent`; close only resources you created. Never use broad `pkill` or `tmux kill-server` for cleanup.
- Keep capture scripts, dependencies, browser profiles, tokens, and raw frames in a private temporary directory outside the product repository. Do not introduce an npm build step into a static site just to make a video.
- Authenticate offscreen. Do not display token-bearing URLs, credentials, unrelated conversation history, or private notifications. Audit terminal output and visual frames before publishing; output-only asciinema recording can still capture echoed secrets.
- Get permission for consequential demo actions and provider spend. Prefer a read-only task for code walkthroughs; verify the target worktree afterward. Do not fake success or silently remove failed tool attempts to misrepresent a run.

## Workflow

### 1. Plan and preflight

Pick the story, target dimensions, aspect ratio, font sizes, and playback speed before invoking a potentially slow task. A useful handoff story is: start in the terminal, submit a task, detach, open the same task in the browser, then rejoin from the terminal so both stream together.

For dense side-by-side text, the tested starting point is **3840×2160**, `deviceScaleFactor: 1`, and a larger browser panel than terminal panel. These are starting values, not mandatory dimensions. Ensure text remains readable at the site's rendered width and in fullscreen.

**Make a short test before the real take:** capture the layout, encode a few seconds through the final pipeline, and inspect both the PNG and decoded MP4 with `view_image`. This catches gray padding, clipping, tiny fonts, terminal/caption collisions, and color damage without repeating a long model run.

### 2. Capture a clean master

- Capture **lossless PNGs** or another genuinely lossless master. Avoid JPEG screenshots and chains such as JPEG → VP8 → browser replay → JPEG → MP4. A higher-quality final encode cannot restore already damaged pixels.
- Keep the viewport, page layout, screenshot dimensions, and encoder dimensions consistent. Do not upscale a small browser screenshot into a nominally 4K frame or crop away the right edge to make it fit.
- Record actual timestamps. PNG capture may run slower than its requested frame rate; frame counts alone are not a clock. Keep terminal and browser events on the same timeline, including any later edits or speed changes.
- Preserve animations and real streaming behavior during capture. Scroll the **actual transcript container**, not just the page, while new output arrives.
- Wait for task completion, confirm the final paragraph and bottom edge are visible, and hold the ending long enough to read. Record teardown only outside the chosen publish interval.

### 3. Encode once

Use the timestamped PNG sequence as the source for a single final encode. The tested text-heavy delivery settings are H.264, CRF 12, `yuv420p`, explicit BT.709 conversion/metadata, and `+faststart`. See the reference for exact commands.

Treat CRF 12 as a quality-first starting point, not a universal optimum. Inspect decoded frames before trading quality for file size. Increasing output FPS duplicates existing frames; it does not add missing motion. Apply a uniform 1×, 1.2×, or 2× timing change to both views, and leave a readable final hold.

### 4. Close the visual feedback loop

Use **maco** to exercise browser playback if available, then **`view_image`** to inspect screenshots. Use **`look_at` only for an optional second opinion**, never as a substitute for directly viewing the result.

Required checks:

1. Inspect the encoded opening, a busy middle frame, and the final frame. Compare suspicious flat colors with source PNGs; distinguish intentional UI shadows from compression artifacts.
2. Verify full-frame coverage, the browser's right edge, readable text, and no captions hidden behind the terminal input or native player controls.
3. Confirm both views show the same live conversation and the completed browser answer is at the bottom—not merely that a scroll command ran.
4. Test the **published asset** in a browser: playback, seeking near the end, decoded dimensions, duration, fullscreen, and errors. A successful encoder exit is not visual verification.
5. For an embedded video, check desktop/mobile and both website themes. Verify the real serving URL supports byte-range requests so seeking works.

### 5. Publish and clean up

Generate the poster from a representative **encoded** frame and update the video's text alternative to match the take. Prefer native controls, `playsinline`, and `preload="none"` for a secondary demo. If autoplay is requested, respect reduced motion, keep controls available, and avoid two simultaneous autoplaying recordings.

Run the site's normal build/checks after integration. Report the new duration, dimensions, approximate size, preview location, verification, and commit status. A changed duration or versioned asset name helps the user distinguish the new take from a cached one.

Once verification is complete, remove temporary scripts, dependencies, credentials, and capture-only sessions. Leave a user-requested preview running. Retain a lossless master only in an agreed private location for further editing; do not leave secret-bearing scratch directories behind or commit them with the delivery assets.
