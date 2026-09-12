# Headless terminal and browser capture

This workflow was used for a real Kodelet TUI/Web UI handoff. The reusable parts are a real PTY recording, a real browser view, one timestamped PNG timeline, and one final encode. The application-specific controller (login, prompt, navigation, completion detection) should stay small and be adapted to the current app rather than turned into a generic recording framework.

## Tools and isolation

Check the installed versions and command help before building the controller:

```bash
command -v tmux asciinema node ffmpeg ffprobe
asciinema rec --help
ffmpeg -hide_banner -encoders
tmux -L llm-agent list-sessions
```

Use the user's preferred package manager for missing dependencies (prefer `dnf` on Fedora when authorized). Check that the FFmpeg build actually has `libx264`; package names and codec availability vary. Use an existing Playwright/Chromium installation or install dependencies only in a private capture workspace. Do not hardcode another machine's cached npm path, Chrome path, or maco endpoint.

Create a private workspace and use it throughout the capture:

```bash
umask 077
CAPTURE_DIR=$(mktemp -d /tmp/screen-recording.XXXXXX)
printf '%s\n' "$CAPTURE_DIR"
```

Before starting anything, record which daemons and browser sessions already exist. An isolated demo daemon needs its own state/database and explicit endpoint. Do not assume changing only the working directory isolates a daemon. For Kodelet, consult the current configuration/source for the base path, config isolation, and server-management flags; never issue an unqualified `kodelet server stop` or `restart` during a recording task.

With maco, discover the current generated Playwright wrappers and their input models instead of guessing tool signatures. Use its code-execution wrapper for browser orchestration or screenshots. Create a dedicated browser context and close only that context, not the shared browser or unrelated tabs. If the MCP execution time limit is shorter than the take, run the capture controller in tmux and use maco for preflight and final playback inspection.

## Terminal stream

Invoke the tmux skill before starting the interactive recorder. The following flags were used with an asciinema v2 recorder; check local help for other versions:

```bash
tmux -L llm-agent new-session -d -s screen-recording-terminal -x 76 -y 32 -c /absolute/path/to/demo-repo
tmux -L llm-agent set-option -t screen-recording-terminal remain-on-exit on
tmux -L llm-agent send-keys -t screen-recording-terminal -l "TERM=xterm-256color COLORTERM=truecolor COLORFGBG='15;0' asciinema rec --cols 76 --rows 32 -q -c 'bash --noprofile --norc' '$CAPTURE_DIR/terminal.cast'"
tmux -L llm-agent send-keys -t screen-recording-terminal Enter
```

Use a unique session name if that name already exists. Set a short, non-sensitive shell prompt in the demo shell. Do not enable input recording (`--stdin`) unless needed and explicitly reviewed. Avoid recording extra environment variables that could include secrets.

Send text literally and control keys separately:

```bash
tmux -L llm-agent send-keys -t screen-recording-terminal -l 'kodelet chat'
tmux -L llm-agent send-keys -t screen-recording-terminal Enter
tmux -L llm-agent capture-pane -t screen-recording-terminal -p
```

For a cast-only demo, embed this real output with an asciinema player. For composition, replay the output events into xterm.js with matching columns/rows. Parse the actual cast format: v2 uses absolute seconds in JSON lines; do not assume other versions have the same timing. While tailing a live file, buffer incomplete JSON lines and consume only complete events. Await xterm's `write(data, callback)` before taking each frame so terminal rendering does not lag behind the browser.

Define a shared time origin before starting user actions, record the cast's offset, and verify alignment using a visible command/submission marker. If the recorder starts in another process, transfer a start marker; do not line up independent recordings by frame index. Check synchronization near both the start and end.

### Terminal colors

Set the terminal background, foreground, and ANSI palette explicitly if a Gruvbox terminal is requested. Test the actual TUI output first: applications emitting true-color escapes can override the terminal's ANSI palette. Changing a player's theme cannot reliably recolor every application-controlled pixel. Preserve a good existing dark TUI rather than applying a destructive video color filter.

## Browser composition and geometry

For a 4K deliverable, the tested browser context used:

```js
const context = await browser.newContext({
  viewport: { width: 3840, height: 2160 },
  deviceScaleFactor: 1,
});
```

When launching your own Chromium, `--force-color-profile=srgb` was used to make capture colors predictable. Do not disable the browser sandbox by default. Wait for fonts to load before preflight capture.

A tested split was 1600 pixels for the terminal and 2240 for the browser. Give the compositor an explicit 3840×2160 layout, `margin: 0`, and no overflow. Set `min-width: 0` on grid children. The iframe must lay out at its allocated width, not retain a wider fixed viewport that is later cropped. Keep the full browser frame visible, including its right edge and bottom composer.

Use a same-origin compositor route when the app permits it: xterm.js on one side, the real app in an iframe on the other. Authenticate using the actual app flow in an offscreen page in the same context, then navigate the visible iframe to a clean conversation URL. If the app forbids framing, do not bypass its security headers; capture the real browser viewport separately and composite its lossless PNGs on the same timeline.

Increase app fonts or choose a responsive layout at the intended panel width. Do not turn a smaller screenshot into a nominal 4K recording by upscaling. In the tested 76×32 terminal, 32px text with xterm `lineHeight: 1.35` left space for captions; `1.65` overlapped the bottom input box. Measure the rendered terminal bounds rather than assuming row geometry.

Keep captions sparse and outside both the terminal input and the native video control area. A decorative title bar consumes space without demonstrating functionality; omit it unless requested.

## Capture PNGs and actual timing

Take viewport-only PNGs, at CSS pixel scale with DPR 1. Do not use `fullPage: true` for a video frame, and do not disable animations: screenshot animation suppression can fast-forward or cancel real application animations.

This loop is an excerpt for a task-specific Playwright controller. `beforeFrame` should flush due terminal output and follow the transcript. The caller owns the page, actions, abort signal, and shared `origin` (from `performance.now()` in this process):

```js
import { mkdir, writeFile } from 'node:fs/promises';

async function captureFrames(page, directory, { signal, origin, beforeFrame }) {
  await mkdir(directory, { recursive: true });
  const frames = [];
  try {
    while (!signal.aborted) {
      await beforeFrame();
      const start = performance.now();
      const file = `${String(frames.length).padStart(5, '0')}.png`;
      await page.screenshot({
        path: `${directory}/${file}`,
        type: 'png', scale: 'css', fullPage: false,
        animations: 'allow', caret: 'initial',
      });
      const end = performance.now();
      frames.push({ file, time: ((start + end) / 2 - origin) / 1000 });
      await new Promise(resolve => setTimeout(resolve, Math.max(0, 1000 / 6 - (end - start))));
    }
  } finally {
    await writeFile(`${directory}/frames.json`, JSON.stringify(frames));
  }
  return frames;
}
```

The timestamp is a screenshot-time estimate, not an exact compositor presentation timestamp. Capturing at roughly 6–8 FPS worked for this text-heavy walkthrough; use faster capture or a lossless desktop recorder for smooth pointer motion or animation. Record real elapsed time even if PNG writes are slower than expected. Keep screenshots sequential instead of queueing concurrent captures. Stop the loop, await its completion, then close the page; do not encode a failed or incomplete take as if it succeeded.

### Keep the browser at the bottom

Find the actual transcript scroller with DOM inspection; substitute its selector below. With an iframe, call this on its Playwright `Frame`, not the outer compositor page:

```js
await frame.locator('[data-testid="chat-transcript-scroll"]').evaluate(el => {
  el.style.scrollBehavior = 'auto';
  el.scrollTop = el.scrollHeight;
});
```

Do this as content grows, not just once after navigation. Smooth scrolling and a UI's near-bottom threshold can lose track during fast streaming. Wait for the app's genuine completion state, follow to the bottom again after final rendering, and assert:

```js
const gap = await frame.locator('[data-testid="chat-transcript-scroll"]').evaluate(
  el => el.scrollHeight - el.scrollTop - el.clientHeight,
);
if (Math.abs(gap) > 2) throw new Error(`Transcript is not at the bottom: ${gap}px`);
```

Also inspect the final paragraph visually; a numeric scroll check alone does not prove the right answer is shown. Hold the final view for about four seconds of **delivered** playback (eight real seconds at 2×).

### Example real handoff

For the tested Kodelet take: `kodelet chat` → select `flair` with Ctrl+T → submit a read-only code question → Ctrl+C to detach → `kodelet server url --open` → view the same conversation → `kodelet chat` → Ctrl+L, search for the task, Enter to rejoin. Verify shortcuts against the current TUI; do not blindly select the first history row, which may be **New conversation**.

A session-only browser launcher can hand the real `--open` URL to the controlled browser without printing its token. Keep its URL file private and never show the token-bearing address in the captured frame. Do not change the user's global browser association. Wait for both views to show ongoing output and then the completed answer. Keep model output genuine, including failed commands; shorten operator pauses only with the same cuts applied to both views.

## Timestamped encode

Generate a trusted `frames.ffconcat` beside the PNGs from `frames.json`. For consecutive frames at times `t[i]` and `t[i+1]`, use `duration = (t[i+1] - t[i]) / speed`. Do not apply the same speed multiplier again in FFmpeg. Filenames should be controlled numeric names; escape paths correctly if they contain quotes.

```text
ffconcat version 1.0
file '00000.png'
duration 0.100000
file '00001.png'
duration 0.150000
file '00002.png'
duration 4.000000
file '00002.png'
```

Repeat the last image after its duration so there is a following timestamp defining its hold. This example adds a four-second final hold; if that hold is already present in the captured timeline, preserve it instead of adding it twice. Set the output duration explicitly to the sum of the manifest durations: otherwise end-of-stream frame-duration inference can extend a long final hold again. Use relative paths where possible; `-safe 0` is needed only for paths rejected by the concat safety rules, and only for your own trusted manifest.

Run a full-length encode in a dedicated tmux session. This was the tested high-quality SDR delivery path:

```bash
OUTPUT_SECONDS=$(awk '$1 == "duration" { seconds += $2 } END { printf "%.6f", seconds }' "$CAPTURE_DIR/frames/frames.ffconcat")
ffmpeg -hide_banner -y -f concat -i "$CAPTURE_DIR/frames/frames.ffconcat" \
  -vf "fps=24,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p" \
  -t "$OUTPUT_SECONDS" -an -c:v libx264 -preset slow -crf 12 \
  -x264-params colorprim=bt709:transfer=bt709:colormatrix=bt709 \
  -movflags +faststart "$CAPTURE_DIR/final.mp4"
```

This converts the captured RGB frames for delivery; color metadata alone is not a conversion. It is still a lossy, chroma-subsampled encode, not pixel-identical to the PNGs. Raising quality cannot remove damage already in the master. For a size reduction, re-encode the lossless source at another CRF and compare, not the previous MP4. Audio requires its own synchronized capture/timing plan; this example is intentionally silent.

## Verify the actual output

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=codec_name,width,height,pix_fmt,r_frame_rate,color_space,color_transfer,color_primaries:format=duration,size \
  -of json "$CAPTURE_DIR/final.mp4"

ffmpeg -hide_banner -y -ss 5 -i "$CAPTURE_DIR/final.mp4" -frames:v 1 "$CAPTURE_DIR/opening.png"
ffmpeg -hide_banner -y -sseof -0.5 -i "$CAPTURE_DIR/final.mp4" -frames:v 1 "$CAPTURE_DIR/ending.png"
```

Choose a busy middle timestamp as well. Inspect these images using `view_image` and compare against corresponding source frames. Generate a poster from an approved encoded frame:

```bash
ffmpeg -hide_banner -y -i "$CAPTURE_DIR/opening.png" -frames:v 1 -c:v libwebp -quality 90 "$CAPTURE_DIR/poster.webp"
```

Then play the final asset through the actual website in maco. Confirm `videoWidth`, `videoHeight`, `duration`, no `video.error`, playback advancing, and a successful seek close to the end. Capture and inspect the browser-decoded ending at native resolution as well as the responsive embed. Make sure neither CSS nor browser controls conceal the result. For a replacement at the same URL, verify the delivered duration/size to rule out an old cached asset.

Serve only the public output directory, not the capture directory or authenticated app, for a shareable preview. Use a byte-range-capable server. For a requested all-interface preview, a tested option (run in tmux with cwd set to that public directory) is:

```bash
uv run --with rangehttpserver==1.4.0 -- python -m RangeHTTPServer --bind 0.0.0.0 1314
curl -sS -D - -o /dev/null -H 'Range: bytes=0-1023' http://127.0.0.1:1314/recordings/demo.mp4
```

Check for `206 Partial Content` and a correct `Content-Range`. Do not assume a basic static server supports video seeking. Bind to localhost unless the user requests external access, and preserve unrelated preview servers.

## Failure diagnosis

| Symptom | Check and correction |
| --- | --- |
| Huge gray area with tiny content in a corner | Compare CSS viewport, device scale, actual PNG dimensions, and compositor child bounds. Fix capture geometry; do not pad or upscale a broken capture. |
| Browser right side chopped | Resize the real browser/iframe layout to its panel before capture. Inspect the right edge and long lines at native resolution. |
| Pink/green blocks on a cream background | Compare raw PNG, intermediate media, decoded MP4, and browser playback. If already in the master, recapture losslessly; if only in the final encode, inspect conversion/range and quality. Keep genuine UI gradients/shadows. |
| End of answer missing | Follow the transcript scroller during streaming; wait for completion and final render, assert bottom gap, inspect last paragraph, and hold. |
| Terminal input or caption cut off | Measure xterm row height and its bounding box. Adjust fonts/line spacing or layout before capturing; reserve space above player controls. |
| Browser and terminal drift apart | Use actual shared timestamps and the cast offset. Apply identical time scaling/cuts; await terminal writes before each PNG. |
| Seeking fails or shows the old take | Test HTTP byte ranges and verify the asset's duration/size; refresh or version the URL if cached. |

## Upstream references

- Playwright `Page.screenshot`: https://playwright.dev/docs/api/class-page#page-screenshot
- Playwright context viewport/device scale: https://playwright.dev/docs/api/class-browser#browser-new-context
- FFmpeg concat demuxer: https://ffmpeg.org/ffmpeg-formats.html#concat
- FFmpeg scale filter: https://ffmpeg.org/ffmpeg-filters.html#scale
- xterm.js API: https://xtermjs.org/docs/api/terminal/classes/terminal/

Check these and local tool help when adapting to another version. The layout/font/CRF values above describe a tested starting point, not an upstream compatibility guarantee.
