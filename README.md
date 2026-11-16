# videoauto

## Tiếng Việt (tóm tắt)

**videoauto** biến một thư mục video thô thành một video ngắn hoàn chỉnh (mặc định 15 giây, dọc 9:16)
chỉ bằng một lệnh. Công cụ phân tích từng clip (cắt cảnh, độ nét, chuyển động, mô tả cảnh bằng
Claude), lập kế hoạch dựng bằng **một** lượt gọi model, rồi dựng và xuất MP4 bằng
[OpenChatCut](https://github.com/0xsline/OpenChatCut). Đây là bản thử nghiệm sớm: kết quả còn thô.

```
python -m videoauto.run
```

Lệnh trên **không cần tham số**: thư mục video (`footage_dir`), kịch bản cần theo (`script_file`),
thông tin thương hiệu và logo (`brand`) đều đặt sẵn trong `config.yaml`. Có thể ghi đè:
`python -m videoauto.run D:\quay\buoi-khac --script kich-ban.txt`.

- **Thương hiệu**: tên, khẩu hiệu và mô tả giúp model chọn cảnh, nhịp và viết lời dẫn hợp thương hiệu
  (chỉ đưa vào bước lập kế hoạch, không đưa vào bước mô tả cảnh).
- **Logo**: chèn PNG trong suốt ở một góc, suốt video hoặc 3 giây cuối. Khi logo đang hiện, video
  không dùng hiệu ứng chuyển cảnh (OpenChatCut vẽ chuyển cảnh đè lên logo). Logo ngang và có chữ
  nhỏ nên đặt `logo_scale` khoảng 0.30–0.35 cho khung dọc 9:16.
- **Kịch bản**: nếu có `script_file`, công cụ làm theo kịch bản đó; cảnh nào footage không có sẽ được
  cảnh báo rõ, không bịa.

Cần cài: Python 3.12, Node 24, ffmpeg, Claude Code CLI (đã đăng nhập), và một bản OpenChatCut đặt
**cạnh** thư mục videoauto, đã chạy `npm install` và đã tạo `catalogue.json`. Làm theo đúng thứ tự
các bước tiếng Anh bên dưới (bước 2, OpenChatCut, là bước dễ sai nhất). Mọi thông báo lỗi của công cụ
đều bằng tiếng Việt và nói rõ cần làm gì; xem mục **Troubleshooting**.

Chưa làm được: chữ/phụ đề, nhạc nền, theo dõi thư mục tự động; phần nhận giọng nói chưa được kiểm thử.
Chỉ mới chạy thử trên Windows 11.

---

## What this does

videoauto turns a folder of raw video clips into one finished short video, with no clicks.
It indexes every clip locally (shots, sharpness, motion, quality flags) and captions the shots with
Claude, asks a model **once** for an edit plan (which shots, in what order, one colour look, fades),
validates that plan hard, and then drives the [OpenChatCut](https://github.com/0xsline/OpenChatCut)
`occ` command line to assemble and render the MP4.

```
python -m videoauto.run
```

With `footage_dir` (and optionally `script_file` and `brand`) set in `config.yaml`, that is the
whole command. The output is a crude first cut: picked and ordered shots, one LUT, a fade in and
out, an optional logo, sometimes a transition. It is not a finished edit with titles or music
(see **What this does not do yet**).
It has been developed and run on **Windows 11 only**. The macOS/Linux commands below are the
standard ones for each tool but have not been tested with videoauto.

---

## 1. Prerequisites

You need git, Python 3.12, Node.js 24, ffmpeg and the Claude Code CLI.

### Windows (PowerShell)

```powershell
winget install -e --id Git.Git
winget install -e --id Python.Python.3.12
winget install -e --id Schniz.fnm          # Node version manager
winget install -e --id Gyan.FFmpeg         # the full build: drawtext is needed for contact sheets
irm https://claude.ai/install.ps1 | iex    # Claude Code CLI
```

Close and reopen PowerShell, then:

```powershell
fnm env --use-on-cd --shell powershell | Out-String | Invoke-Expression
fnm install 24
fnm use 24
node --version        # v24.x
ffmpeg -version       # first line: ffmpeg version ...
claude --version
claude auth login     # opens the browser; uses your Claude subscription
claude auth status    # should show you are logged in
```

To have `node` in every new PowerShell window, add the `fnm env ...` line to your profile
(`notepad $PROFILE`). videoauto itself also finds an fnm-installed Node on Windows without it.

### macOS (Homebrew, untested)

```bash
brew install git python@3.12 fnm ffmpeg
eval "$(fnm env --use-on-cd)"      # add this line to ~/.zshrc too
fnm install 24 && fnm use 24
curl -fsSL https://claude.ai/install.sh | bash
claude auth login
```

### Ubuntu 24.04 (untested)

```bash
sudo apt update && sudo apt install -y git python3.12 python3.12-venv ffmpeg fonts-dejavu-core
curl -fsSL https://fnm.vercel.app/install | bash      # then open a new terminal
fnm install 24 && fnm use 24
curl -fsSL https://claude.ai/install.sh | bash
claude auth login
```

Every model call (captions and the plan) goes through `claude -p` and counts against your Claude
plan's usage. A 17-clip folder took 8 calls in total (see **Run it**).

---

## 2. OpenChatCut (required, separate project)

videoauto does not render video itself. It runs OpenChatCut's `occ` command line as a subprocess
(it never imports OpenChatCut code), and it needs OpenChatCut's catalogue of effects and transitions
as a JSON file. **Both must be set up before the first run.**

Put the two projects **side by side** in the same parent folder. The default `config.yaml`
expects exactly this layout:

```
some-folder/
  OpenChatCut/      <- step 2
  videoauto/        <- step 3
```

```bash
cd some-folder
git clone https://github.com/0xsline/OpenChatCut.git
git clone https://github.com/bach04py/videoauto.git
cd OpenChatCut
fnm use                     # reads .nvmrc (Node 24); answer "y" if fnm offers to install it
npm install                 # takes several minutes; must finish without errors
```

Now generate `catalogue.json`. **The generator script is not part of upstream OpenChatCut**; it ships
with videoauto in `openchatcut/dump-catalogue.mts` and has to be copied into OpenChatCut's
`scripts/` folder, because it imports OpenChatCut's own source files by relative path:

```bash
# still inside OpenChatCut/
cp ../videoauto/openchatcut/dump-catalogue.mts scripts/          # Windows: copy ..\videoauto\openchatcut\dump-catalogue.mts scripts\
npx tsx scripts/dump-catalogue.mts ../videoauto/catalogue.json
```

The last line should end with something like
`{"effect":33,"lut":16,...,"transition":16,"template":235,"total":300,...}` and the file
`videoauto/catalogue.json` (about 0.8 MB) must now exist. That is where videoauto looks for it
(`catalogue:` in `config.yaml`). Regenerate it whenever you update OpenChatCut.

Optional check that `occ` runs:

```bash
npx tsx cli/main.ts --help          # prints "occ — OpenChatCut command line"
```

You do not need to open the OpenChatCut app. videoauto uses its own OpenChatCut data folder
(`.occ-data/` inside videoauto) so it never touches your OpenChatCut projects.

---

## 3. Install videoauto

```powershell
cd ..\videoauto
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
```

macOS/Linux: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -e .`

Optional extras:

- `pip install -e ".[music]"` adds BPM, beat and energy analysis for audio files placed in a
  `music/` subfolder (librosa). The plan does not use music yet, so you can skip this.
  The extra caps numpy below 2.3 on purpose: **Windows Smart App Control** blocks
  `numpy/random/_sfc64.pyd` from numpy 2.5.x, which librosa loads through scipy/numba. numpy 2.2.6
  loads fine. Do not turn Smart App Control off; the cap is the fix. The video path does not use
  `numpy.random` and works on current numpy without the extra.
- `pip install -e ".[dev]"` adds pytest. Tests run on your own clips in `tests/fixtures/`
  (`gym.mp4`, `sunset.mp4`); footage is never committed, so tests that need it are skipped.

`faster-whisper` (speech) is installed with the base package. The first file that contains speech
downloads a Whisper model (`medium`, about 1.5 GB, on CPU). The speech path is untested.

---

## 4. Configure: `config.yaml`

`config.yaml` in the videoauto folder is read from the current directory (or pass
`--config path`). Relative paths are relative to the file itself. This is the committed file; the
lines marked **CHANGE** are the ones you must edit:

```yaml
brief:
  duration: 15          # target length in seconds
  aspect: "9:16"        # 9:16 | 16:9 | 1:1 | 4:5 | 4:3 | 3:4
  fps: 30               # 24 | 25 | 30 | 50 | 60
  platform: tiktok      # free text for the planner
  notes: ""             # optional extra instructions for the planner

footage_dir: D:/quay/buoi-tap        # CHANGE: folder with the raw clips (override: run <folder> or --footage)
script_file: scripts/buoi-tap.txt    # optional: the script to follow; absent or empty = planner writes one
                                     # (override: --script <file>)

brand:
  name: "Hoàng Gia Gym & Fitness 24h"
  tagline: "Khỏe mỗi ngày"           # optional
  context: |                         # optional free text for the planner (never sent to captioning)
    Chuỗi phòng tập, hồ bơi và sân pickleball ở Bình Dương.
    Giọng điệu khỏe khoắn, hướng tới hội viên mới.
  logo: brand/logo.png               # optional; PNG with transparency. CHANGE or remove this line
  logo_position: bottom-right        # top-left | top-right | bottom-left | bottom-right
  logo_scale: 0.22                   # logo width as a fraction of frame width (0.05-0.5)
  logo_opacity: 0.85                 # 0.05-1
  logo_margin: 0.04                  # distance from the edges, fraction of frame width (0-0.25)
  logo_mode: always                  # always | end  (end = last 3 seconds only)

catalogue: catalogue.json     # generated from OpenChatCut (README step 2)
min_material_ratio: 0.6       # refuse to plan when usable footage < 0.6 x duration

openchatcut:
  repo: ../OpenChatCut        # CHANGE if OpenChatCut is not next to the videoauto folder
  data_dir: .occ-data         # OPENCHATCUT_DATA_DIR used for every occ call
  # node: C:/path/to/node.exe # only if videoauto cannot find Node (see Troubleshooting)
  resolution: 1080p
  video_bitrate_mbps: 10      # H.264 target; 8-12 suits 1080x1920 social video
  timeout: 1800               # seconds allowed for occ render
  max_fail_ratio: 0.30        # abort the build when more than 30% of occ item commands fail
```

Every setting is checked before any work starts (`kiểm tra môi trường…`): a missing logo file, a
`logo_scale` outside 0.05–0.5, an unknown `logo_position` or `logo_mode`, a missing `footage_dir`
each stop the run in the first second with a Vietnamese message, e.g.
`Lỗi: brand.logo_scale = 0.6 không hợp lệ: phải là số trong khoảng 0.05–0.5.`

The example brand, logo and script are not part of this repository: put your own logo at
`brand/logo.png` and your script at `scripts/buoi-tap.txt` (both folders are git-ignored), or remove
those lines.

### Brand: worked example

With the `brand` block above, `name`, `tagline` and `context` go into the **planning** prompt only,
so shot choice, pacing and the written script reflect the business. They are never sent with the
caption requests: captions describe only what is in the frame. On a real 17-clip gym shoot the
planner opened on a close-up of a strapped hand gripping the bar, ended the script with
"Hoàng Gia Gym & Fitness 24h – Khỏe mỗi ngày", and warned on its own:
`Footage chỉ có phòng gym; không có hình hồ bơi hay sân pickleball nên video không đề cập đến các
tiện ích này.` The brand context does not make it claim footage it does not have.

### Logo overlay, and why there are no transitions under it

The logo is a real overlay in OpenChatCut: `import_asset` (the PNG), `edit_track create` (a video
track above V1), `edit_item` (an image item on that track), then an `edit_item` update with
`transform` `{scale, x, y, opacity}`. Measured on occ: an image without a transform is fitted
"contain" into the frame and centred; `scale` multiplies that size; `x`/`y` move its centre by a
percentage of the frame width/height. videoauto converts `logo_scale`, `logo_margin` and
`logo_position` into those values, so the logo keeps its aspect ratio and is exactly `logo_scale`
of the frame width.

**Limitation found in OpenChatCut:** its renderer draws GL transitions on top of every track except
captions (`src/editor/TimelineComposition.tsx`, "painted over both clips, beneath captions"). The
transition window is `L` frames starting `floor(L/2)` frames before the cut. For that whole window
the logo disappears; a test render with a 30-frame cross-dissolve showed the logo at 2.5 s and no
logo in the middle of the transition.

**What videoauto does:** no transitions while the logo is on screen. With `logo_mode: always` the
planner is told to use plain cuts, and the validator (in both planner and builder) drops any
transition whose window overlaps the logo, with a warning. With `logo_mode: end`, transitions
before the last 3 s are kept. The alternative, cutting the logo into segments around each
transition, still loses the logo during every transition and makes it blink for 0.1–0.3 s at each
cut, which looks like a fault on a brand mark. The chosen approach was verified by render: the logo
is present at 2.5 s and at the cut; in `end` mode a frame in the middle of an earlier transition
shows the transition, and the logo appears from 3 s before the end.

**Logo size on 9:16:** `logo_scale` is a fraction of the frame **width**, and a 9:16 frame is
narrow. A wide logo with small lettering (this one is 1065x786 with a "Gym&Fitness 24h" arc) is
about 238 px wide at 0.22, and its small text is unreadable on a phone. Use 0.30–0.35 for logos
like that; the default 0.22 suits compact marks.

### Following your own script (`script_file`)

When `script_file` exists and is not empty, the planner **follows** it instead of inventing a
narrative: plain text, with or without timecodes, for example

```
0-3s: cận cảnh tay nắm tạ
3-8s: toàn cảnh phòng tập
8-12s: cảnh hồ bơi
12-15s: kết bằng tên phòng tập
```

The model must report, beat by beat, which clips cover each part. A beat the footage cannot cover
becomes a warning that names it, such as `kịch bản: không có cảnh phù hợp cho "cảnh hồ bơi"`, and
the closest match is used only where it is reasonable; nothing is fabricated and no beat is
dropped silently (a reply without that report is itself flagged). Any such warning lowers the plan to
tier `basic`. `plan.json` records `"script_source": "user"`. When the file is absent or empty, the
planner writes the script as before (`"script_source": "planner"`).

The model provider is configured separately and defaults to the Claude CLI; nothing to do if
`claude auth status` shows you are logged in. To use an OpenAI-compatible endpoint instead, create
`videoauto.toml`:

```toml
[vision]
provider = "openai-compatible"

[vision.openai_compatible]
base_url = "https://.../v1"
model = "..."
api_key_env = "VIDEOAUTO_API_KEY"   # the key is read from this environment variable
```

---

## 5. Run it

From the videoauto folder, with the virtual environment active and `footage_dir` set:

```powershell
python -m videoauto.run
```

Everything can be overridden for one run:
`python -m videoauto.run D:\footage\other-shoot --script my-script.txt --out output\other`
(`--footage <folder>` works the same as the positional folder; `--out` defaults to `output/`).

Progress is printed in Vietnamese, one block per stage:

```
kiểm tra môi trường…                       # checks ffmpeg, Node, OpenChatCut, catalogue, claude first
━━ [1/3] Lập chỉ mục …                     # index every clip; captions batched 9 keyframes per call
━━ [2/3] Lập kế hoạch dựng …               # ONE model call -> plan.json
━━ [3/3] Dựng video bằng OpenChatCut …     # occ: project, import, clips, LUT, transitions, fades, render
━━ XONG trong 532s (lập chỉ mục 358s, kế hoạch 12s, dựng 162s); 8 lượt gọi model
```

What appears in `output/`:

| Path | What it is |
|---|---|
| `final.mp4` | the video (1080x1920 H.264 for 9:16) |
| `final.mp4.build.json` | every `occ` command with its arguments, result and time; warnings |
| `plan.json` | the EditPlan: clips (source in/out), LUT, transitions, fades, the planner's script and warnings |
| `index/<sha256>.json` | one index per source file (shots, captions, sharpness, motion, flags) |
| `index/frames/` | the keyframes the captions were made from |

Measured on one Windows 11 laptop (no GPU) with 17 clips (15 of them 4K, 2.3 GB in total):
indexing 358 s with 7 caption calls; planning 12 s with 1 call; building 162 s, of which importing
2.3 GB into OpenChatCut and the render take most (each other `occ` command costs about 2–3 s of
start-up). Total 532 s and 8 model calls for a 15 s, 20 MB video.

Running again on the same folder reuses the cached indexes (`cache: TRÚNG …`, zero caption calls),
then makes one new plan call and a new build.

Exit codes: `0` done; `2` setup or input problem; `3` usage limit reached (safe to re-run later);
`5` the planner refused (not enough usable footage); `6` the build was aborted.

### Debugging one stage at a time

```
python -m videoauto.index <folder> --out output/index
python -m videoauto.plan  output/index --out output/plan.json        # footage_dir/script_file from config.yaml
python -m videoauto.build output/plan.json --out output/final.mp4    # both accept --footage; plan accepts --script
python -m videoauto.index <folder> --out output/index --calibrate-blur  # local blur report, no model calls
```

Add `--debug` to any command for a full Python traceback.

---

## Troubleshooting

All messages are printed as `Lỗi: …` with the fix in the message.

**ffmpeg not found**
```
Lỗi: Không tìm thấy ffmpeg trong PATH. Cài bằng lệnh: winget install Gyan.FFmpeg, rồi mở terminal mới và chạy lại.
```
Install ffmpeg (step 1), then **open a new terminal**: winget only updates PATH for new windows.
`ffmpeg -version` must work in the terminal you run videoauto from.

**Node not on PATH / not found**
```
Lỗi: Không tìm thấy Node.js ('node'). Cài Node (bản trong .nvmrc của OpenChatCut) hoặc đặt openchatcut.node trong config.yaml.
```
On Windows, videoauto looks for `node` on PATH, then for the fnm-installed version named in
OpenChatCut's `.nvmrc`. If neither works, set the full path in `config.yaml`, e.g.
`node: C:/Users/<you>/AppData/Roaming/fnm/node-versions/v24.22.1/installation/node.exe`
(macOS/Linux: the output of `fnm exec --using=24 which node`). Related messages:
`openchatcut.repo = '…' không phải repo OpenChatCut (thiếu cli/main.ts)` means `repo:` points to the
wrong folder; `Repo OpenChatCut chưa cài thư viện. Chạy npm install …` means step 2's `npm install`
did not finish.

**catalogue.json missing**
```
Lỗi: Không tìm thấy catalogue '…\catalogue.json'. Chép catalogue.json từ thư mục gốc repo OpenChatCut (tạo bằng scripts/dump-catalogue.mts) và đặt đường dẫn 'catalogue' trong config.yaml.
```
Redo the catalogue part of step 2, or point `catalogue:` at the file you generated.

**Claude CLI not installed or not logged in**
```
Lỗi: Không tìm thấy lệnh `claude`. Cài Claude Code CLI rồi đăng nhập bằng lệnh: claude auth login (hoặc đặt vision.provider = "openai-compatible" trong videoauto.toml).
Lỗi: Không có quyền truy cập Claude: <the CLI's own message>. Đăng nhập bằng lệnh: claude auth login (kiểm tra: claude auth status).
```
Run `claude auth login`, check with `claude auth status`, and run videoauto again. The second line
quotes whatever the Claude CLI reported, for example `Invalid API key · Please run /login`.

**Windows Smart App Control blocks numpy** (only with the `[music]` extra)
```
ImportError: DLL load failed while importing _sfc64: An Application Control policy has blocked this file.
```
videoauto reports it as `Windows Smart App Control chặn một thư viện của librosa (thường do numpy
quá mới). Chạy: pip install "numpy>=2.0,<2.3" rồi thử lại.` Run exactly that. Do not disable Smart
App Control. Video indexing keeps working either way.

**Usage limit reached**
```
Dừng: Đã chạm giới hạn sử dụng/tốc độ của Claude. Đã dừng an toàn: các tệp đã xử lý xong được lưu vào cache, tệp đang dở không được ghi. Hãy chạy lại lệnh khoảng 19:00 ngày 16/11; các tệp đã có trong cache sẽ không tốn lượt gọi nào.
```
Exit code 3. Nothing half-finished is written. Run the same command after the time shown; finished
files are cache hits and cost no calls.

**The video is sideways**
Some cameras record portrait footage as landscape (e.g. 3840x2160) without a rotation tag, so every
tool, including OpenChatCut, shows it sideways. Tag copies of the files losslessly and run on the
copies (`90` = turn counter-clockwise; use `-90` if they come out upside down):
```
ffmpeg -display_rotation 90 -i C0144.MP4 -map 0:v:0 -map 0:a:0 -c copy upright/C0144.MP4
```

**A clip is skipped with `Không đọc được tệp …`**
The file could not be decoded by ffmpeg. The run continues with the other clips; check the file
plays in a normal video player.

---

## What this does not do yet

- No titles, captions or motion-graphics templates. The only overlay is the logo, and while it is
  on screen there are no transitions (see **Logo overlay**).
- The brand tagline is not written on screen; it only steers the planner.
- No music: files in `music/` are analysed (BPM, beats) but never placed in the edit; the output
  carries the source clips' own sound.
- No folder watcher: you run the command yourself.
- Speech transcription (Whisper) is wired in but untested on real speech.
- Planning is minimal: shot choice and order, one LUT for the whole piece, fades, optional transitions.
  Footage whose orientation differs from the target (e.g. landscape into 9:16) is letterboxed, not
  reframed.
- The per-shot `motion` value saturates (0.83–1.00 on every shot of a real 4K gym shoot), so it
  does not yet distinguish calm from busy shots.
- Shot choice leans heavily on `sharpness`: on a 17-clip test the plan used exactly the five
  sharpest shots. Sharpness also depends on content, so detail close-ups tend to rank low.
- Developed and tested on Windows 11 only.

---

## How it works (short)

- **M1 index** (`videoauto/index.py`): ffprobe metadata; PySceneDetect shot boundaries; keyframes;
  OpenCV motion, camera movement and per-shot `sharpness`; ffmpeg black/frozen/silent detection;
  Vietnamese captions from 3x3 contact sheets, one model call per sheet, sequential. One JSON per
  source at `<out>/<sha256>.json`, validated against `videoauto/schemas/index.schema.json`. A
  `blurry` flag marks only extreme blur (sharpness < 35, or < 25% of the median of the same file's
  other shots) and never removes a shot from `usable_shots`.
- **M2 plan** (`videoauto/planner/`): rejects locally (no call) when usable footage is under
  `min_material_ratio` x duration; otherwise one text-only model call; every id and parameter is
  checked against `catalogue.json`, in/out against the source, no overlaps, transitions only on cuts.
  Invalid parts are dropped with a warning, never passed on. Brand text and the user script go
  into this prompt only. Schema: `videoauto/schemas/editplan.schema.json`.
- **M3 build** (`videoauto/builder/`): `occ project new` → `import_folder` → one `edit_item` per clip
  → `manage_effects` (LUT) → `edit_item` transitions → fades → logo (`import_asset`, `edit_track`,
  `edit_item` image + transform) → `occ render`, all with one
  `OPENCHATCUT_DATA_DIR`. A failed item is skipped and recorded; the build aborts above 30% failures.

## License

AGPL-3.0-or-later, the same as OpenChatCut. See `LICENSE`.
