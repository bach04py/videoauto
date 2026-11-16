# videoauto

Turns raw footage and music into text indexes, so a later stage can plan an edit from text alone.
This project is separate from OpenChatCut and never imports it. Later milestones will call the
`occ` CLI as a subprocess.

## M1: indexer

```
python -m videoauto.index <file|folder> --out .index/
```

Each source file gets one JSON index at `.index/<sha256>.json`. The format is defined in
[`videoauto/schemas/index.schema.json`](videoauto/schemas/index.schema.json). Every index is validated
against it before it is written. A validation failure raises an error and nothing is written.

### Pipeline per video (local steps first, model calls last)

| Step | Tool | Model call? |
|---|---|---|
| sha256 → cache lookup (a hit is logged and the file is skipped) | hashlib | no |
| duration, fps, width, height, orientation | ffprobe | no |
| letterbox/pillarbox bars: all video analysis below runs on the picture area only | ffmpeg `cropdetect` | no |
| shot boundaries | PySceneDetect `ContentDetector` (frames from an ffmpeg pipe, so any codec works, incl. AV1) | no |
| speech detection (VAD) → transcript only if speech | faster-whisper: Silero VAD gate, then `medium` on CPU / `large-v3` on CUDA (`whisper.model`, default `auto`) | local model, not billed |
| keyframes: 1 per shot, plus a 2nd for shots > 8 s | ffmpeg | no |
| motion 0–1 (`1 - exp(-d/10)`, d = mean grey change at ~6 samples/s) | OpenCV | no |
| camera movement: 3x3 tile phase correlation, fit translation + zoom, trust only samples where the tiles agree | OpenCV | no |
| flags black / frozen / silent | ffmpeg `blackdetect` / `freezedetect` / `silencedetect` | no |
| flag blurry | OpenCV variance of Laplacian on keyframes | no |
| audio peaks (loudest RMS moments, ≥ 1 s apart) | numpy | no |

`audio.has_music` and `audio.bpm` are `null` for video in M1 (there's no music detector yet).
`camera` never takes the value `tracking`, because this method can't tell it apart from a pan.
A black shot whose keyframe is truly blank gets a local caption and no model cell. A black
title card with text is still captioned.
| caption (VI), subjects, shot_size | vision provider, batched 3x3 contact sheets | **yes** |

Music files (anything under a `music/` folder) get duration, BPM, beats and an RMS energy curve
from librosa. Music never triggers a model call.

### Vision provider

The provider is set in `videoauto.toml`:

```toml
[vision]
provider = "claude"            # default; or "openai-compatible"

[vision.openai_compatible]
base_url = "https://.../v1"
model    = "..."
api_key_env = "VIDEOAUTO_API_KEY"   # the key is read from this env var, never stored in the file
```

- **claude**: runs `claude -p --output-format json`. The prompt references the contact sheet's
  path, and only the `Read` tool is allowed.
- **openai-compatible**: POSTs to `/chat/completions` with the sheet as a base64 image.

Rules for keeping cost down:
- Keyframes from **all queued files** go into 9-cell contact sheets: 3x3, built with ffmpeg
  `tile`, with each cell's number drawn on it by `drawtext`. One request covers one sheet. When
  two cells belong to the same shot (long shots), the prompt says so and the reply has one entry
  for that shot.
- The reply is a JSON array keyed by cell number:
  `[{"cell": 1, "caption": "...", "subjects": [...], "shot_size": "..."}]`.
- Requests run sequentially. If a reply is malformed, it is retried twice. After that the shot
  gets `caption: null` and the `caption_failed` flag.
- The locally computed camera label is passed into the prompt, so the Vietnamese caption mentions
  it without the model guessing motion from a still image.
- If the provider reports a usage or rate limit, the run stops. Every file whose shots are all
  captioned is written to the cache. Files only partly done are **not** written. A message in
  Vietnamese says when to retry, using the reset time when the CLI reports one.
- At the end of a run it logs the number of model calls, shots per call, and cache hits.

### Errors

Each problem a user can hit (missing ffmpeg/ffprobe, unreadable file, `claude` not logged in or
no API key, limit reached) is caught at the CLI boundary. The user sees a short Vietnamese message
saying what went wrong and what to do, then the exit code is non-zero. Stack traces appear only
with `--debug`.

### Tests (pytest)

They run against a short real video in `tests/fixtures/`. The vision provider is faked, so tests
never spend usage. They cover:
1. A second run over an unchanged file makes 0 provider calls and logs a cache hit.
2. Schema validation rejects a malformed index, and nothing is written.
3. A simulated rate-limit error mid-run stops cleanly: finished files are cached, the partly done
   file isn't, no stray or partial `.json` files remain, and the message is in Vietnamese.

Run them with ffmpeg on PATH: `.venv\Scripts\python -m pytest`

Exit codes: 0 ok, 1 some files failed, 2 user error (missing ffmpeg/claude, bad path or config),
3 limit reached (partial run, safe to re-run), 4 invalid index (a bug).

### Layout

```
videoauto/
  index.py              CLI entry (python -m videoauto.index)
  indexer.py            orchestration, cross-file batching, cache writes
  probe.py crop.py frames.py speech.py audio.py keyframes.py quality.py
  vision/base.py claude_cli.py openai_compat.py contact_sheet.py captioner.py
  cache.py schema.py config.py errors.py tools.py
  schemas/index.schema.json
tests/
```
