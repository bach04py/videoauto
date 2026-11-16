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
A cached index from an older `schema_version` fails validation on read, so that file is indexed
again. Version 2 added per-shot `sharpness`, and with it `blurry` stopped excluding shots.

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
| `sharpness` per shot + `blurry` flag (rule below) | OpenCV variance of Laplacian on keyframes | no |
| audio peaks (loudest RMS moments, ≥ 1 s apart) | numpy | no |

`audio.has_music` and `audio.bpm` are `null` for video in M1 (there's no music detector yet).
`camera` never takes the value `tracking`, because this method can't tell it apart from a pan.
A black shot whose keyframe is truly blank gets a local caption and no model cell. A black
title card with text is still captioned.
| caption (VI), subjects, shot_size | vision provider, batched 3x3 contact sheets | **yes** |

Music files (any audio or video file under a `music/` folder) get duration, BPM, beat times
(librosa `beat_track`) and an RMS energy curve (0–1, every 0.1 s). Music never triggers a model
call. When there's no steady pulse (silence, ambient), `bpm` is `null` and `beats` is empty.
If a file with identical bytes was already indexed as the other kind in the same `--out`, that
file fails with a clear message. It is never served the wrong cached index.

### Sharpness and the `blurry` flag

Every shot carries `sharpness`: the variance of the Laplacian of the sharpest 3x3 tile, taken on
the shot's sharpest keyframe resized to 480 px wide. Higher means sharper. It exists for
**ranking**: when M2 has a choice between shots, it prefers the sharper one. The score depends
heavily on content (texture, light), so it compares well only between similar shots, and best
within one file.

`blurry` is a flag, **never a gate**. `quality.usable_shots` and `quality.rejected` count only
`black` and `frozen`. A blurry shot is still captioned and still usable, and its quality note
says so. The flag marks only clearly unusable blur. The exact rule, applied per file in
`quality.blur_verdicts`, is the same for indexing and `--calibrate-blur`:

> A shot that is not `black` gets `blurry` when **either**
> 1. **absolute**: `sharpness < blur_threshold` (default **35**), or
> 2. **relative**: the same file has at least `blur_relative_min_others` (default **2**) *other*
>    non-black shots, and `sharpness < blur_relative × median(sharpness of those other shots)`
>    (default **0.25**, i.e. under a quarter of the file's typical shot).
>
> Black shots are never flagged and are left out of every other shot's median.

Why both: the relative rule alone flags nothing when every shot in a file is blurred, so the
floor catches that. The floor alone misfires on plain content (night, fog, sky, bare walls),
so it stays low and the relative rule catches softness that is visible only next to the
file's other shots, which share camera, lens and light. With 0 or 1 other shot there's no
meaningful reference, so only the floor applies.

Where 0.25 comes from (two fixtures only, so treat it as a starting point): within one
continuous take, real frames went down to about 0.45× the clip median. Synthetic defocus of
radius 2 px or more dropped the same frames to about 0.10–0.20×.

All three values live in `[quality]` in `videoauto.toml`: `blur_threshold`, `blur_relative`
and `blur_relative_min_others`.

### Calibrating the blur rule

```
python -m videoauto.index <folder> --out .index/ --calibrate-blur
```

This runs local analysis only, with no model calls, no Whisper and no index written. It uses
the same shots, keyframes, sharpness metric and blur rule as indexing. It prints every shot
sorted by sharpness: score, ratio to the median of the file's other shots (`/tệp`), flagged and
why (`abs` / `rel`), near a boundary, and keyframe path. "Near" means within 0.67–1.5× of the
floor, or a ratio under 2× `blur_relative`. It also writes:
- `calibrate_blur.tsv`, the full table, including `file_median_others`, `ratio` and `reason`
- `calibrate_blur_flagged_NN.jpg` and `calibrate_blur_near_NN.jpg`, contact sheets whose cell
  labels match the `#` column

## M2: planner, M3: builder (thin vertical slice)

```
python -m videoauto.index tests/fixtures --out output/index      # M1
python -m videoauto.plan  output/index   --out output/plan.json  # M2: ONE model call
python -m videoauto.build output/plan.json --out output/final.mp4 # M3: drives `occ`
```

`config.yaml` (cwd, or `--config`) holds:
- the brief: `duration`, `aspect`, `fps`, `platform`, `notes`
- `media_dir`: the footage folder that was indexed
- `catalogue`: a copy of OpenChatCut's `catalogue.json`, not committed
- `openchatcut`: `repo`, `data_dir`, `node`, `resolution`, `timeout`, `max_fail_ratio`

The vision provider still comes from `videoauto.toml`.

**Planner** (`videoauto/planner/`)
- It reads every valid video index. When `media_dir` is set, it keeps only indexes whose file
  is there with the same sha256.
- It rejects locally, with **no model call**, when the usable footage (shots without
  black/frozen) is under `min_material_ratio` (0.6) × the target duration.
- Otherwise it makes **one** call through the same provider interface as M1. This is a text-only
  call: the Claude CLI runs with `--tools ""`. The prompt lists each usable shot with its
  sharpness and its rank. The rules are:
  - prefer the sharper shot when choosing between similar options;
  - prefer the target orientation;
  - one LUT for the whole piece;
  - a fade in and a fade out;
  - transitions are optional;
  - reply `{"reject": "…"}` instead of inventing a plan.
- A malformed reply is not retried. It becomes a `reject` plan.
- It writes an **EditPlan** (`videoauto/schemas/editplan.schema.json`). The spec's shape gains
  four fields:
  - `fades {in,out}` (seconds)
  - `sources` (file → sha256, duration, orientation, so the builder can check the media)
  - `reason` (required when the tier is `reject`)
  - `model_calls`

  Effect `params` are positional, in the catalogue's param order.

**Hard validation** (`planner/validate.py`, run by both M2 and M3). Each invalid part is dropped
with a Vietnamese warning and never passed on. It checks that:
- every effect and transition id exists in the catalogue with the right kind;
- every parameter is inside its published range;
- `in`/`out` are inside the source, and each clip is at least 0.5 s;
- no clip touches a black or frozen shot (M2 only);
- no two clips reuse the same source seconds;
- there is at most one LUT per clip;
- each transition sits exactly on a cut, with at most one per cut, and is no longer than half
  of either neighbouring clip;
- each fade is at most 2 s and at most half of its clip.

If no clips survive, the tier becomes `reject`.

**Tier:**
- `reject` when there is no usable plan;
- `basic` when confidence is below 0.6, anything was dropped, or the total is under 80% of the target;
- `full` otherwise.

**Builder** (`videoauto/builder/`)
- It runs `node --import tsx cli/main.ts … --json` inside the OpenChatCut repo, with **one**
  `OPENCHATCUT_DATA_DIR` for every call. Tool arguments go through `--args @file.json`. It never
  imports OpenChatCut code.
- The sequence is:
  1. `project new` (size and fps from the target)
  2. `import_folder media_dir`
  3. one `edit_item` add per clip. `fromFrame` is cumulative; `sourceStartFrame` and
     `sourceDurationInFrames` are in timeline frames. The add rejects `srcInFrame`, but stores
     these values as `srcInFrame` and `durationInFrames`.
  4. one `manage_effects add` per clip effect
  5. one `edit_item` transition per cut
  6. `edit_item` updates for `fadeInSeconds` / `fadeOutSeconds`
  7. `render`
- Every command and its result goes to the log and to `<out>.build.json`.
- If an item command fails, that item is skipped and recorded. Its dependants are skipped too:
  later clips pack left, and transitions next to a missing clip are dropped.
- The run aborts once failures exceed `max_fail_ratio` (30%) of the item commands.
- A failure in `project new`, `import_folder` or `render` is fatal.

Exit codes: plan `5` = reject; build `6` = aborted, `2` = a reject plan or bad input.

### Install

```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .[music,dev]     # or just -e . for video only (no librosa / numba / scipy)
winget install Gyan.FFmpeg      # full build: drawtext is needed for contact sheets
```

**Windows Smart App Control.** It blocks `numpy/random/_sfc64.pyd` from numpy 2.5.x, which
librosa imports through scipy/numba. The `music` extra caps numpy below 2.3 (2.2.6 loads fine).
Nobody needs to turn Smart App Control off. The video path doesn't use `numpy.random` and works
on current numpy.

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
2. Schema validation rejects a malformed index, and nothing is written. That includes a missing
   `sharpness`, an old `schema_version`, and a blurry shot counted as rejected.
3. A simulated rate-limit error mid-run stops cleanly: finished files are cached, the partly done
   file isn't, no stray or partial `.json` files remain, and the message is in Vietnamese.

Run them with ffmpeg on PATH: `.venv\Scripts\python -m pytest`

Exit codes: 0 ok, 1 some files failed, 2 user error (missing ffmpeg/claude, bad path or config),
3 limit reached (partial run, safe to re-run), 4 invalid index (a bug).

### Layout

```
videoauto/
  index.py plan.py build.py   CLI entries (python -m videoauto.index / .plan / .build)
  planner/  catalogue.py validate.py planner.py    M2
  builder/  occ.py builder.py                      M3 (occ subprocess only)
  brief.py              config.yaml loader
  indexer.py            orchestration, cross-file batching, cache writes
  probe.py crop.py frames.py speech.py audio.py keyframes.py quality.py
  vision/base.py claude_cli.py openai_compat.py contact_sheet.py captioner.py
  cache.py schema.py config.py errors.py tools.py
  schemas/index.schema.json
tests/
```
