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
- **Chữ trên hình**: mỗi dòng `TEXT: …` trong kịch bản thành một câu chữ nhảy lên giữa khung, đúng
  nguyên văn, màu theo `brand.colors`. Không có dòng TEXT thì không có chữ; công cụ không tự đặt chữ.
  Mỗi trường chữ tối đa 22 ký tự: dài hơn sẽ bị rút gọn theo từ, có cảnh báo
  (ví dụ "HOÀNG GIA GYM & FITNESS" thành "HOÀNG GIA GYM"). Chỉ dùng template đã qua kiểm tra font
  tiếng Việt, và chữ không bao giờ nằm ở góc có logo.

Cần cài: Python 3.12, Node 24, ffmpeg, Claude Code CLI (đã đăng nhập), và một bản OpenChatCut đặt
**cạnh** thư mục videoauto, đã chạy `npm install` và đã tạo `catalogue.json`. Làm theo đúng thứ tự
các bước tiếng Anh bên dưới (bước 2, OpenChatCut, là bước dễ sai nhất). Mọi thông báo lỗi của công cụ
đều bằng tiếng Việt và nói rõ cần làm gì; xem mục **Troubleshooting**.

**Chạy liên tục**: `python -m videoauto.watch` hỏi ba thư mục (video nguồn, kịch bản, xuất video),
lưu vào `videoauto.local.yaml` nếu bạn đồng ý, rồi chạy mãi: mỗi tệp kịch bản `.txt` mới thả vào thư
mục kịch bản thành một video (`tap-nguc.txt` → `tap-nguc.mp4`), lần lượt từng cái một. Kịch bản xong
chuyển vào `done/`, lỗi chuyển vào `failed/` kèm tệp `.loi.txt` ghi lý do và cách xử lý. Khi chạy lúc
đăng nhập (Task Scheduler) hoặc dạng dịch vụ (NSSM) phải thêm `--no-prompt`. Xem mục 6.

Chưa làm được: phụ đề theo lời nói, nhạc nền; phần nhận giọng nói chưa được kiểm thử.
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
out, an optional logo, text overlays for the lines your script asks for, sometimes a transition.
It is not a finished edit with subtitles or music (see **What this does not do yet**).
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
  colors:                            # optional; fills the colour fields of text overlays (README)
    accent: "#ED1C24"                # Hoàng Gia red. Keep the quotes: YAML reads # as a comment
    ink: "#1A1A1A"                   # near-black: cards and bars behind text
    on_accent: "#FFFFFF"             # text

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
`logo_scale` outside 0.05–0.5, an unknown `logo_position` or `logo_mode`, a brand colour that is not
`#RGB`/`#RRGGBB`, a missing `footage_dir` each stop the run in the first second with a Vietnamese
message, e.g.
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

### Text overlays (`TEXT:` lines and `brand.colors`)

A beat in the script gets on-screen text only when the script names it with a `TEXT:` line, at the
start of a line or of a `|` column:

```
0-3s   | Cận cảnh bàn tay đeo dây quấn nắm chặt tay cầm máy tập.
       | TEXT: KHÔNG GIAN SỐNG ĐỘNG

3-6s   | Toàn cảnh khu máy tập, thấy được độ rộng và ánh sáng.
       | TEXT: MÔI TRƯỜNG SANG TRỌNG
```

The planner places one overlay per TEXT line on its beat and records it in `plan.json`:

```json
"overlays": [{"template": "library:motion-graphic:ss-keyword-pop", "at": 0.3, "duration": 2.5,
              "fields": {"keyword": "KHÔNG GIAN SỐNG ĐỘNG", "subText": ""}}],
"text_lines": ["KHÔNG GIAN SỐNG ĐỘNG", "MÔI TRƯỜNG SANG TRỌNG", "..."]
```

The rules are enforced in `videoauto/planner/validate.py`, not only asked for in the prompt. Each
broken rule drops that overlay with a Vietnamese warning (the plan tier drops to `basic`):

- **Vietnamese-safe templates only.** A template needs `"vi_ok": true` in `catalogue.json` (136 of
  235 pass OpenChatCut's Vietnamese font audit), and its orientation must match the target (no 16:9
  template on a 9:16 video). For 9:16 the planner is offered the `social-shorts` group, with
  `竖屏·重点词弹出` (keyword pop, `ss-keyword-pop`) first as the default for a short line.
- **Script text only, verbatim.** Every non-empty text field must equal one of the script's TEXT
  lines (compared after Unicode NFC normalisation, so a decomposed `ộ` still matches), and each line
  is used once. Text the script did not ask for is dropped; a script without TEXT lines gets no
  overlays at all. Text fields the plan leaves out are set to `""`: the templates ship Chinese
  sample text (`subText` of keyword pop is `每天进步一点点`), which would otherwise be rendered.
- **22 characters per field.** In the font audit, longer text overflowed its box. A longer line is
  cut at a word boundary, a dangling `&`/`-`/`,` is dropped, and a warning names both versions:
  `chữ "HOÀNG GIA GYM & FITNESS" dài 23 ký tự (tối đa 22), rút gọn thành "HOÀNG GIA GYM" để không bị
  cắt`. Shorten the TEXT line in the script yourself if you want different words.
- **No overlap.** Overlays never overlap in time; they are clamped to the video and are at least 1 s.
- **Not in the logo's corner.** Where a template draws is fixed in its code, so videoauto keeps a
  table of positions (`PLACEMENT` in `videoauto/planner/overlays.py`, read from
  `assets/templates/social-shorts-templates.json`). Seven templates are centred (`ss-keyword-pop`,
  quote card, step list, big number, cover title, checklist, before/after); `ss-chapter` is a top
  bar; `ss-episode-badge` is top-left; `ss-price-tag` and `ss-note-sticker` are upper right;
  `ss-big-caption`, `ss-cta-follow` and `ss-name-bar` are bottom bars (both bottom corners);
  `ss-social-actions` is bottom-right. An overlay that reaches the logo's corner while the logo is
  showing is dropped. A template that is not in the table has an unknown position and is refused
  whenever a logo is showing. With the logo bottom-right, the planner is only offered templates that
  are centred or in the upper part of the frame.
- **No transition under an overlay.** OpenChatCut paints transitions over overlays as it does over
  the logo, so a transition whose window overlaps an overlay is dropped.

**Brand colours.** `brand.colors` has three roles, each `"#RGB"` or `"#RRGGBB"`, quoted: `accent`
(brand colour), `ink` (dark surface behind text) and `on_accent` (text colour). A bad value stops
the run at load: `Lỗi: brand.colors.accent = 'red' không phải mã màu hex hợp lệ. …`. The builder
fills a template's colour fields by **exact field name**:

| Role | Template fields filled |
|---|---|
| `accent` | `accentColor`, `accentColor1`, `highlightColor`, `brandColor`, `themeColor`, `accentLineColor`, `badgeColor` |
| `ink` | `bgColor`, `backgroundColor`, `cardColor`, `cardBgColor`, `cardBackgroundColor`, `panelColor`, `boxColor`, `badgeBgColor`, `inkColor` |
| `on_accent` | `textColor`, `titleColor`, `nameColor`, `headerTextColor` |

Every other colour field keeps the template's default (e.g. `paperColor`, `gradientColor`,
`primaryColor`, whose meaning differs between templates). Surfaces get `ink` and text gets
`on_accent`, so light text stays on a dark surface and contrast survives the swap. When the default
was translucent, the alpha is kept: `rgba(0,0,0,0.72)` becomes `rgba(26,26,26,0.72)` and
`#0f172aee` becomes `#1A1A1AEE`. For keyword pop that means `accentColor` (text outline, glow and
underline bar) = `#ED1C24` and `textColor` = `#FFFFFF`. Without `brand.colors`, templates keep their
own colours.

**How it is built** (probed against `occ` before writing the code): `edit_track create` makes one
video track for all overlays. It is created **before** the logo track, because `occ` stacks a new
track on top, so the order is V1 < overlays < logo. Then, per overlay:
1. `edit_item` `adds:[{type:"motion-graphic", assetId, track, fromFrame}]`. A motion-graphic add
   accepts no duration and, unlike video and image adds, returns **no itemId**;
2. `read_timeline` finds the new item (motion-graphic on that track at that frame);
3. `edit_item` `updates:[{type:"motion-graphic", itemId, durationInFrames}]`;
4. `update_item_props` `{itemId, props}` with every text field plus the brand colours.

The add copies the template's Chinese sample props into the item. If step 3 or 4 fails, the item is
removed again (`remove_item`), so sample text never reaches the render. A failed overlay is skipped
and recorded under `"overlays"` in `final.mp4.build.json` and in the warnings. It is never fatal and
does not count towards `max_fail_ratio`.

**Verified on the real footage** (the 17-clip gym shoot, `scripts/buoi-tap.txt` with five TEXT
lines). One frame was extracted from the middle of each overlay at 1080x1920:
- all five lines were placed on their beats; the fifth was shortened to `HOÀNG GIA GYM` with the
  warning above;
- every diacritic renders: Ô Ố Ộ Ồ Ư Ơ Ọ Ế Ị Ỉ Đ Ở Ử À, no boxes and no clipped stacked accents.
  Long lines wrap onto two centred lines;
- the text block keeps at least 96 px from every frame edge (`HOÀNG GIA GYM`, the widest line) and
  ends 600–660 px above the logo;
- outline, glow and bar are `#ED1C24` and the fill is `#FFFFFF`, as set in `brand.colors`.

The run took 225 s: indexing 3 s (cache), planning 23 s, building 199 s with 40 `occ` commands and
0 failures. Each overlay costs 4 commands, about 7 s.

Two things this run showed that are not fixed:
- Keyword pop sets no font family, so it renders in a **serif fallback face**, not a heavy sans.
- White text with a thin outline is **hard to read over bright backgrounds** (a window, a lit
  ceiling). Choose calm, darker shots for TEXT beats if you can.

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
| `plan.json` | the EditPlan: clips (source in/out), LUT, transitions, fades, text overlays, the planner's script and warnings |
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

## 6. Chạy liên tục (folder watcher)

```powershell
python -m videoauto.watch
```

Start it once from a terminal in the videoauto folder (virtual environment active). It stays running
and builds **one video for every new script** (`.txt`) that appears in the scripts folder. The
footage folder is a standing library, not a trigger: adding clips does not start a build, but every
build first indexes any clip not yet in the cache (already-indexed clips cost zero model calls).
`brief`, `brand`, `openchatcut` and `catalogue` still come from `config.yaml`; its `footage_dir`
and `script_file` are not used by the watcher.

**1. Startup checks.** ffmpeg, Node, the OpenChatCut repo, `catalogue.json` and model access are
checked once, before any question. A missing one prints the same `Lỗi: …` message as
`videoauto.run` (see **Troubleshooting**) and exits with code 2.

**2. Three questions** (Vietnamese, in this order):

```
1. Thư mục video nguồn [D:\quay\thu-vien]:
2. Thư mục kịch bản [D:\kich-ban]:
3. Thư mục xuất video [D:\video\xuat]:
Tóm tắt: …
Lưu lại cho lần sau? [Y/n]
```

The first time there is no `[default]`. Later runs show the saved value: Enter keeps it, typing a
path replaces it. Answers are saved to `videoauto.local.yaml` in the current folder (git-ignored;
`config.yaml` stays the committed example), and only when you answer yes.
`--paths` prints the saved paths and exits; `--reset` forgets them.

Each answer is checked as you type it and asked again if it is wrong, so nothing fails later:
surrounding quotes (drag-and-drop) and a trailing `\` are removed; the footage folder must exist and
contain at least one video; a missing scripts folder is offered for creation; the output folder is
created if missing and must be writable; the three folders must differ; the scripts folder must not
be inside the footage folder (a script would be indexed as a source), and neither may the output
folder (finished videos would be indexed as footage).

**3. Folders.** Beside the output folder (in its parent) the watcher keeps:

| Folder | What it holds |
|---|---|
| `processing/` | the script being built right now (at most one) |
| `done/` | scripts that produced a video |
| `failed/` | scripts that failed, each with `<name>.loi.txt` saying what failed and what to do |
| `.index/` | the index cache shared by every build |
| `.logs/` | `runs.log` (one line per run: time, script, result, duration, model calls) and one full log per run |

So with output `D:\video\xuat` they are `D:\video\processing`, `D:\video\done`, and so on. Pick an
output folder that has a parent of its own, not a drive root.

**4. What happens to a script.** For `tap-nguc.txt` dropped into the scripts folder:
1. it waits until the file size has not changed for 3 s (an editor may still be saving it);
2. it moves to `processing/` at once, so it can never be picked up twice;
3. index (new clips only) → plan → build, exactly as `videoauto.run`, with this script as `script_file`;
4. `tap-nguc.mp4`, `tap-nguc.mp4.build.json` and `tap-nguc.plan.json` are written into the output
   folder (a video with the same name is overwritten);
5. the script moves to `done/`, or to `failed/` with `tap-nguc.loi.txt`, and the next one starts.

Only `.txt` files are watched; anything else in the folder is ignored. Builds run **strictly one at
a time** and the rest wait in a queue: OpenChatCut is a single instance and the render is
memory-heavy (a parallel run was killed for lack of memory on the development laptop). A script left
in `processing/` by a crash goes back to the scripts folder once at the next start; if it is found
in `processing/` a second time it goes to `failed/`. Ctrl+C abandons the current build cleanly: the
script goes back to the scripts folder (it is rebuilt at the next start), a half-rendered video is
deleted and no half-written index file stays behind.

A failure never stops the watcher. It is printed immediately as a block:

```
========================================================================
!! LỖI  2026-11-16 18:40:02  tap-nguc.txt
   Bước lỗi:   lập kế hoạch dựng
   Lỗi:        Kế hoạch bị từ chối, không dựng video: …
   Cách xử lý: Thêm cảnh quay phù hợp vào thư mục video nguồn hoặc sửa kịch bản cho khớp …
   Kịch bản đã chuyển vào: D:\video\failed\tap-nguc.txt
   Lý do ghi tại:          D:\video\failed\tap-nguc.loi.txt
========================================================================
```

To retry, fix the cause and copy the script from `failed/` back into the scripts folder. An empty
script, or one not saved as UTF-8, fails at once with that reason.

### Starting it at logon (later)

Started without a terminal, the watcher cannot ask anything, so it must run with `--no-prompt` (it
also switches to that mode by itself when stdin is not a terminal, including `< NUL`). Run it once in
a terminal first and answer **Y** to save the paths. In `--no-prompt` mode a missing or invalid saved
path exits with code 2 and a message naming the path, e.g.
`Lỗi: 'Thư mục kịch bản' đã lưu (D:\kich-ban) không dùng được: … Hãy chạy python -m videoauto.watch một lần trong cửa sổ terminal…`

The working folder matters: `config.yaml` and `videoauto.local.yaml` are read from it.

**Task Scheduler** (runs as you, at logon, with your Claude login):

```powershell
$py  = "C:\path\to\videoauto\.venv\Scripts\python.exe"
$act = New-ScheduledTaskAction -Execute $py -Argument "-m videoauto.watch --no-prompt" `
         -WorkingDirectory "C:\path\to\videoauto"
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName "videoauto watch" -Action $act -Trigger $trg
```

ffmpeg and Node must be findable from that task. The scheduler does not read your PowerShell
profile, so if Node comes from fnm, set `openchatcut.node` in `config.yaml` to the full `node.exe`
path. The status lines are not visible there; read `.logs/runs.log` and `failed/*.loi.txt` instead.

**NSSM** (a Windows service that is restarted if it stops):

```powershell
nssm install videoauto-watch "C:\path\to\videoauto\.venv\Scripts\python.exe" "-m videoauto.watch --no-prompt"
nssm set videoauto-watch AppDirectory "C:\path\to\videoauto"
nssm set videoauto-watch ObjectName ".\<your-user>" "<password>"
nssm set videoauto-watch AppStdout "C:\path\to\videoauto\watch.out.log"
nssm set videoauto-watch AppStderr "C:\path\to\videoauto\watch.out.log"
nssm start videoauto-watch
```

The service must run as **your** user (`ObjectName`), not LocalSystem: the Claude CLI login and fnm
live in your profile. Neither setup has been tested yet.

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

- No subtitles from speech. On-screen text comes only from `TEXT:` lines in your script, through
  the 15 social-shorts templates whose position is known (see **Text overlays**). While the logo or
  an overlay is on screen there are no transitions.
- The brand tagline is not written on screen unless you put it in a `TEXT:` line.
- Overlay templates keep their own font: keyword pop falls back to a serif face.
- No music: files in `music/` are analysed (BPM, beats) but never placed in the edit; the output
  carries the source clips' own sound.
- The watcher (`videoauto.watch`) has not yet been run at logon or as a service.
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
  → `manage_effects` (LUT) → `edit_item` transitions → fades → text overlays (`edit_track`, then per
  overlay `edit_item` motion-graphic, `read_timeline`, `edit_item` duration, `update_item_props`)
  → logo (`import_asset`, `edit_track`, `edit_item` image + transform) → `occ render`, all with one
  `OPENCHATCUT_DATA_DIR`. A failed item is skipped and recorded; the build aborts above 30% failures
  (overlays are skipped and recorded but never abort).

## License

AGPL-3.0-or-later, the same as OpenChatCut. See `LICENSE`.
