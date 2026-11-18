# videoauto

Thư mục video thô vào, video ngắn hoàn chỉnh ra. Raw footage folder in, finished short video out.

## Cài đặt (install)

Trên máy đã có Node 24, Python 3.12, ffmpeg, git và Claude Code đã đăng nhập:

```
git clone https://github.com/bach04py/videoauto
cd videoauto
pip install -e .
python -m videoauto.setup
```

Gặp vấn đề: xem **Troubleshooting** ngay bên dưới.

## Troubleshooting

### When `python -m videoauto.setup` stops

Every stop ends with `Chưa cài đặt xong.` and the exact command to run; run setup again afterwards
(finished steps are skipped in seconds). "Cài đặt xong" is printed only after step 7 rendered a real
video.

**Step 1: `Thiếu N công cụ`.** All missing tools are listed at once, each with its install command
(Windows: `winget …`). Install them, **open a new terminal** (winget updates PATH for new windows
only) and run setup again. Node must be 24 (at least the version in OpenChatCut's `.nvmrc`): with a
newer Node as the default, `fnm install 24.22.1` is enough, videoauto finds fnm's Node 24 itself.
`claude chưa đăng nhập`: run `claude auth login`.

**Step 2: OpenChatCut at another commit.** `../OpenChatCut` exists but is not at the pinned commit.
Answer **Y** to switch (git checks the pinned commit out, detached). If git refuses because of local
changes: `git -C ../OpenChatCut stash`, then setup again. `không phải một git clone`: the folder is a
download, not a clone; rename it and let setup clone. Under `--no-prompt` setup never switches; it
prints the two git commands.

**Step 3: install scripts blocked.** On machines whose npm policy blocks install scripts:

```
Thư viện của OpenChatCut cài chưa đủ:
  - ffmpeg-static: thiếu ffmpeg.exe (script cài đặt của nó không chạy)
  - esbuild: không chạy được (…)
  Máy này chặn install script của npm. Cho phép đúng các gói cần thiết:
      npm --prefix "C:\…\OpenChatCut" install-scripts approve ffmpeg-static esbuild
  rồi chạy lại: python -m videoauto.setup   (nó tự chạy npm rebuild cho các gói đó)
```

Run the printed `approve` line, then setup again: it runs `npm rebuild` for those packages and
checks again. Only these two are needed; the other packages npm lists (onnxruntime-node, koffi,
electron-winstaller, protobufjs) belong to desktop features videoauto does not use. `approve`
writes an `allowScripts` entry into OpenChatCut's `package.json`. If `npm ci` itself fails, it is
usually the network or a company proxy (`npm config set proxy http://…`).

**Step 6 under `--no-prompt`.** Pass the folders: `--footage <thư mục> --scripts <thư mục>
--output <thư mục>`, or run setup once in a terminal to choose and save them.

**Step 7: `Dựng thử thất bại`.** The 1-second check video (the bundled 16:9 sample cropped into
9:16, one `TEXT:` line) did not come out. The message has OpenChatCut's own error; every command and
its result is in `<output>/.setup-check/build.json`, with the plan next to it. When it works, the
video is `<output>/kiem-tra-cai-dat.mp4`: open it to see the text and the crop.

### Messages while running

All messages are printed as `Lỗi: …` with the fix in the message.

**ffmpeg not found**
```
Lỗi: Không tìm thấy ffmpeg trong PATH. Cài bằng lệnh: winget install Gyan.FFmpeg, rồi mở terminal mới và chạy lại.
```
Install ffmpeg (setup step 1), then **open a new terminal**: winget only updates PATH for new windows.
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
wrong folder; `Repo OpenChatCut tại '…' chưa cài thư viện. Chạy: python -m videoauto.setup` means OpenChatCut's
libraries are not installed: setup step 3 installs and checks them.

**catalogue.json missing or not generated**
```
Lỗi: Không tìm thấy catalogue '…\catalogue.json'. videoauto tự tạo tệp này từ OpenChatCut khi chạy; tạo ngay bằng: python -m videoauto.setup (cài OpenChatCut nếu chưa có, rồi tạo catalogue). Không cần chép tay tệp nào.
```
videoauto generates `catalogue.json` itself (setup step 4). This message means it could not: the same
report lists the OpenChatCut problem to fix first (wrong `openchatcut.repo`, OpenChatCut not
installed). `python -m videoauto.setup` fixes both and makes the catalogue.

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

**Old indexes (`bỏ qua N chỉ mục cũ không còn tệp tương ứng trong thư mục video`)**
The index folder still holds indexes for clips that were replaced or removed. They are skipped and
do no harm. To remove them, run the clean-up explicitly:
```
python -m videoauto.index --prune --out D:\video\xuat\.index
python -m videoauto.index D:\quay\thu-vien --prune --out output\index    # name the footage folder
```
Without a folder argument, the footage folder is the watcher's saved one when `--out` is the
watcher's `.index`, otherwise `footage_dir` from `config.yaml`. The command lists every index it
would remove and deletes only after you answer `y`; Enter means no. It **refuses to run** when the
footage folder is missing or contains no video, because that usually means the drive is not
connected, not that the library is gone. An index whose clip comes back later is simply rebuilt,
which costs model calls again.

One case still gets its own warning: two clips with the **same file name but different content**
both in the footage folder (e.g. `ngay1\C0140.MP4` and `ngay2\C0140.MP4`). The plan refers to clips
by name, so only one can be used; the warning names both and says which one the plan used. Rename
one of them to use both.

**A clip is skipped with `Không đọc được tệp …`**
The file could not be decoded by ffmpeg. The run continues with the other clips; check the file
plays in a normal video player.

### Tiếng Việt (tóm tắt)

**videoauto** biến một thư mục video thô thành một video ngắn hoàn chỉnh (mặc định 15 giây, dọc 9:16)
chỉ bằng một lệnh. Công cụ phân tích từng clip (cắt cảnh, độ nét, chuyển động, mô tả cảnh bằng
Claude), lập kế hoạch dựng bằng **một** lượt gọi model, rồi dựng và xuất MP4 bằng
[OpenChatCut](https://github.com/0xsline/OpenChatCut). Đây là bản thử nghiệm sớm: kết quả còn thô.

Lệnh chính sau khi cài: `python -m videoauto.watch`. Lần đầu nó hỏi ba thư mục (video nguồn, kịch bản, xuất video), lưu vào
`videoauto.local.yaml` nếu bạn đồng ý, rồi chạy mãi: mỗi tệp kịch bản `.txt` mới thả vào thư mục
kịch bản thành một video (`tap-nguc.txt` → `tap-nguc.mp4`), lần lượt từng cái một; video cũ không
bao giờ bị ghi đè (lần sau thành `tap-nguc-2.mp4`). Kịch bản xong chuyển vào `done/`, lỗi chuyển
vào `failed/` kèm tệp `.loi.txt` ghi lý do và cách xử lý; video của một lần dựng lỗi được giữ lại
với tên `<tên>.loi.mp4`, không bao giờ bị xoá. Các thư mục này nằm trong thư mục xuất video. Khi
chạy lúc đăng nhập (Task Scheduler) hoặc dạng dịch vụ (NSSM) phải thêm `--no-prompt`. Xem **The folder watcher**.

Dựng **một lần** không cần watcher (cho người dùng quen dòng lệnh; xem **One build from the command line**):
`python -m videoauto.run` dùng thư mục video (`footage_dir`) và kịch bản (`script_file`) đặt trong
`config.yaml`, hoặc `python -m videoauto.run D:\quay\buoi-khac --script kich-ban.txt`.

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
  tiếng Việt, và chữ không bao giờ nằm ở góc có logo. Kịch bản có dòng TEXT mà không đặt được chữ
  (không có template hợp khung hình, model bỏ sót một dòng, OpenChatCut báo lỗi) thì **dừng và
  báo lý do**, không bao giờ xuất video thiếu chữ. Logo cũng vậy.

Cài đặt: bốn lệnh ở đầu tệp này. `python -m videoauto.setup` tự tải đúng bản OpenChatCut (fork có
bản vá font tiếng Việt, đúng commit ghim trong `openchatcut.pin`), cài thư viện, tạo `catalogue.json`,
hỏi ba thư mục và dựng thử một video 1 giây; chỉ khi video đó ra đời mới báo "Cài đặt xong". Mọi
thông báo lỗi đều bằng tiếng Việt và nói đúng lệnh cần chạy.

Khi đang chờ, cửa sổ watcher luôn hiện dòng gợi ý
`[S] sửa phong cách   [P] xem đường dẫn   [Q] thoát`. Nhấn **S** để tự gõ lại bối cảnh (`context`),
giọng điệu (`tone`), nhịp (`pace`: fast | medium | calm) và LUT ưu tiên (`prefer_luts`) ngay trong
cửa sổ, không cần mở tệp; Enter = giữ giá trị cũ, `-` = xoá. Chỉ khi trả lời **Y** ở câu
"Lưu vào config.yaml?" thì mới ghi, và mọi chú thích trong `config.yaml` được giữ nguyên. Video
đang dựng giữ phong cách cũ; phong cách mới áp dụng từ kịch bản tiếp theo.

**GPU**: `openchatcut.gpu_encode: auto` dùng bộ mã hoá phần cứng (NVENC…) khi thử mã hoá 1 giây
thành công, nếu không thì tự dùng CPU và ghi lý do vào log. Bộ mã hoá thực sự đã dùng được ghi
trong `<video>.mp4.build.json`. Cùng bitrate, mã hoá GPU cho chất lượng hơi thấp hơn x264 (CPU):
khi chất lượng quan trọng hơn tốc độ, đặt `gpu_encode: off`.

**Video quay ngang trong khung dọc**: mặc định (`brief.fit: fill`) clip 16:9 được phóng to cho đầy
khung 9:16 và cắt giữa, không có viền đen; hai bên hình bị cắt, nên chủ thể cần ở giữa khung.
Muốn giữ nguyên cả hình (có viền đen) thì đặt `brief.fit: fit`.

Chưa làm được: phụ đề theo lời nói, nhạc nền, cắt bám theo chủ thể (hiện cắt chính giữa); phần
nhận giọng nói (tắt mặc định) chưa được kiểm thử trên clip có lời nói.
Chỉ mới chạy thử trên Windows 11.


### What this does

videoauto turns a folder of raw video clips into one finished short video, with no clicks.
It indexes every clip locally (shots, sharpness, motion, quality flags) and captions the shots with
Claude, asks a model **once** for an edit plan (which shots, in what order, one colour look, fades),
validates that plan hard, and then drives the [OpenChatCut](https://github.com/0xsline/OpenChatCut)
`occ` command line to assemble and render the MP4.

The main command, once installed, is `python -m videoauto.watch` (the folder watcher, below): it asks for three folders once, then turns every script
(`.txt`) dropped into the scripts folder into a video. For a single run from the command line,
`python -m videoauto.run` uses `footage_dir` (and optionally `script_file` and `brand`) from
`config.yaml` (**One build from the command line**). The output is a crude first cut: picked and ordered shots, one LUT, a fade in and
out, an optional logo, text overlays for the lines your script asks for, sometimes a transition.
It is not a finished edit with subtitles or music (see **What this does not do yet**).
It has been developed and run on **Windows 11 only**. The macOS/Linux commands below are the
standard ones for each tool but have not been tested with videoauto.


### Installing the tools by hand

`python -m videoauto.setup` step 1 checks git, Python 3.12, Node.js 24, ffmpeg and the Claude Code CLI
(logged in) and prints the install command for each one that is missing. The same commands, in full:

**Downloads to expect.** OpenChatCut's `npm ci` (setup step 3) takes about a minute on a fast line. Speech recognition
(Whisper, which transcribes what is said in a clip for the planner) is **off by default** and
downloads nothing. Turn it on in `config.yaml` (`whisper: model:`) only if your footage has speech
that matters; the first run then downloads the model once: `tiny` 75 MB, `base` 145 MB, `small`
485 MB, `medium` 1.5 GB, `large-v3` 3.1 GB (`auto` = `large-v3` with a working NVIDIA GPU and its
CUDA 12 libraries, otherwise `medium`). Speech recognition never stops a build: if the model
cannot load (no `cublas64_12.dll`, a card without fast float16, no disk space), videoauto logs one
warning, falls back to the CPU or skips it, and the clips get `transcript: null`.

#### Windows (PowerShell)

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

#### macOS (Homebrew, untested)

```bash
brew install git python@3.12 fnm ffmpeg
eval "$(fnm env --use-on-cd)"      # add this line to ~/.zshrc too
fnm install 24 && fnm use 24
curl -fsSL https://claude.ai/install.sh | bash
claude auth login
```

#### Ubuntu 24.04 (untested)

```bash
sudo apt update && sudo apt install -y git python3.12 python3.12-venv ffmpeg fonts-dejavu-core
curl -fsSL https://fnm.vercel.app/install | bash      # then open a new terminal
fnm install 24 && fnm use 24
curl -fsSL https://claude.ai/install.sh | bash
claude auth login
```

Every model call (captions and the plan) goes through `claude -p` and counts against your Claude
plan's usage. A 17-clip folder took 8 calls in total (see **Run it**).


### OpenChatCut, the video engine (setup steps 2-4)

videoauto does not render video itself. It runs OpenChatCut's `occ` command line as a subprocess
(it never imports OpenChatCut code), and reads OpenChatCut's catalogue of effects, LUTs,
transitions and text templates from `catalogue.json`. `python -m videoauto.setup` puts all of it in
place:

- **Step 2** clones **the fork** `https://github.com/bach04py/OpenChatCut`, branch `videoauto`, next
  to videoauto (`../OpenChatCut`, the `openchatcut.repo` of `config.yaml`), at exactly the commit in
  `openchatcut.pin`. The fork carries the Vietnamese font fix (Georgia's stacked marks Ố Ầ Ắ … drawn
  from bundled Gelasio, an open-licence font) and the fix for templates that name a font list;
  upstream OpenChatCut has neither, and stacked marks break again. The commit is pinned because a
  `catalogue.json` from another OpenChatCut version made Remotion fail with exit 1 in `readFile`.
  An existing `../OpenChatCut` at another commit is reported and switched only if you answer **Y**
  (under `--no-prompt` it stops and prints the git commands).
- **Step 3** runs `npm ci` there (with `ELECTRON_SKIP_BINARY_DOWNLOAD=1`: videoauto never starts the
  desktop app), then checks what npm's install scripts should have produced: the ffmpeg binary of
  `ffmpeg-static`, a working `esbuild`, and `occ --help` through tsx. npm exits 0 even when a policy
  blocked those scripts (`8 packages have install scripts not yet covered by allowScripts`), so the
  check is on the files, not on npm's exit code. It is skipped on later runs while
  `package-lock.json` and Node are unchanged.
- **Step 4** generates `catalogue.json` (about a second). It copies its generator
  (`openchatcut/dump-catalogue.mts`) into OpenChatCut's `scripts/`, runs it with OpenChatCut's Node,
  removes it again and writes `videoauto/catalogue.json`. The Vietnamese font verdicts (`vi_ok`:
  which templates draw Vietnamese text correctly) come from audit results bundled with videoauto
  (`openchatcut/vi-font-review.json`, and `vi-stack-review.georgia-fix.json` for the fork), so a
  fresh machine gets them without re-running the audit:

```
catalogue: đã ghi …\catalogue.json: 33 hiệu ứng, 16 LUT, 16 chuyển cảnh, 235 template; vi_ok (dùng được với chữ tiếng Việt): 134/235 (dọc 20, ngang 114, vuông 0)
```

`catalogue.json` carries a fingerprint of the OpenChatCut it came from; when OpenChatCut changes, or
a `catalogue.json` is copied from another machine, videoauto regenerates it before doing anything
else. By hand: `python -m videoauto.occ_catalogue` (`--refresh` forces a new one).

The same by hand, if you ever need it (Node 24 on PATH):

```
git clone --branch videoauto https://github.com/bach04py/OpenChatCut.git
cd OpenChatCut
git checkout --detach <the commit= line of videoauto/openchatcut.pin>
npm ci
```

**Maintainer: moving the pin.** Commit and push the change on the fork's `videoauto` branch, check
it out in `../OpenChatCut`, then run `python -m videoauto.setup --pin` in videoauto: it writes
`openchatcut.pin` from `git rev-parse HEAD` there (and refuses while OpenChatCut has uncommitted
changes). Commit `openchatcut.pin`.

You do not need to open the OpenChatCut app. videoauto uses its own OpenChatCut data folder
(`.occ-data/` inside videoauto) so it never touches your OpenChatCut projects.

### Python packages and extras

`pip install -e .` installs everything videoauto imports (`tests/test_install.py` checks that every
imported library is declared in `pyproject.toml`). A virtual environment is optional but keeps
other projects apart: `py -3.12 -m venv .venv` then `.venv\Scripts\activate` before
`pip install -e .` (macOS/Linux: `python3.12 -m venv .venv && source .venv/bin/activate`).

Optional extras:

- `pip install -e ".[music]"` adds BPM, beat and energy analysis for audio files placed in a
  `music/` subfolder (librosa). The plan does not use music yet, so you can skip this.
  The extra caps numpy below 2.3 on purpose: **Windows Smart App Control** blocks
  `numpy/random/_sfc64.pyd` from numpy 2.5.x, which librosa loads through scipy/numba. numpy 2.2.6
  loads fine. Do not turn Smart App Control off; the cap is the fix. The video path does not use
  `numpy.random` and works on current numpy without the extra.
- `pip install -e ".[dev]"` adds pytest. Tests run on your own clips in `tests/fixtures/`
  (`gym.mp4`, `sunset.mp4`); footage is never committed, so tests that need it are skipped.

`faster-whisper` (speech) is installed with the base package but unused while `whisper.model` is
`off` (the default; see **Downloads to expect** above).


### Configure: `config.yaml`

`config.yaml` in the videoauto folder is read from the current directory (or pass
`--config path`). Relative paths are relative to the file itself. This is the committed file; the
lines marked **CHANGE** are the ones you must edit:

```yaml
brief:
  duration: 15          # target length in seconds
  aspect: "9:16"        # 9:16 | 16:9 | 1:1 | 4:5 | 4:3 | 3:4
  fps: 30               # 24 | 25 | 30 | 50 | 60
  platform: tiktok      # free text for the planner
  fit: fill             # fill: footage of another shape fills the frame, centre crop, no black bars
                        # fit: the whole picture stays visible, black bars where the shapes differ
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
  # logo: brand/logo.png             # optional; PNG with transparency. Put your logo there and remove the "# "
  logo_position: bottom-right        # top-left | top-right | bottom-left | bottom-right
  logo_scale: 0.22                   # logo width as a fraction of frame width (0.05-0.5)
  logo_opacity: 0.85                 # 0.05-1
  logo_margin: 0.04                  # distance from the edges, fraction of frame width (0-0.25)
  logo_mode: always                  # always | end  (end = last 3 seconds only)
  colors:                            # optional; fills the colour fields of text overlays (README)
    accent: "#ED1C24"                # Hoàng Gia red. Keep the quotes: YAML reads # as a comment
    ink: "#1A1A1A"                   # near-black: cards and bars behind text
    on_accent: "#FFFFFF"             # text

style:                               # optional; steers the planner. Editable live: watcher key S
  tone: ""                           # one line, e.g. "mạnh mẽ, năng động" or "tươi mát, thư giãn"
  pace: ""                           # fast | medium | calm  (empty = planner decides)
  prefer_luts: []                    # LUT ids from catalogue.json, e.g. [builtin:look-teal-orange]

catalogue: catalogue.json     # made by python -m videoauto.setup (and again whenever OpenChatCut changes)

whisper:                      # speech recognition in the clips (optional; README, Prerequisites)
  model: off                  # off | tiny | base | small | medium | large-v3 | auto
                              # the first use downloads the model once: tiny 75 MB, base 145 MB,
                              # small 485 MB, medium 1.5 GB, large-v3 3.1 GB (auto: large-v3 with a
                              # working NVIDIA GPU, else medium). off = no download, no transcripts
  # language: vi              # optional; empty = detect per clip
min_material_ratio: 0.6       # refuse to plan when usable footage < 0.6 x duration

openchatcut:
  repo: ../OpenChatCut        # CHANGE if OpenChatCut is not next to the videoauto folder
  data_dir: .occ-data         # OPENCHATCUT_DATA_DIR used for every occ call
  # node: C:/path/to/node.exe # only if videoauto cannot find Node (see Troubleshooting)
  resolution: 1080p
  video_bitrate_mbps: 10      # H.264 target; 8-12 suits 1080x1920 social video
  timeout: 1800               # seconds allowed for occ render
  max_fail_ratio: 0.30        # abort the build when more than 30% of occ item commands fail
  gpu_encode: auto            # auto | on | off: hardware H.264 (NVENC/...) when a trial encode works (README)
```

Every setting is checked before any work starts (`kiểm tra môi trường…`): a missing logo file, a
`logo_scale` outside 0.05–0.5, an unknown `logo_position` or `logo_mode`, a brand colour that is not
`#RGB`/`#RRGGBB`, a `style.pace` other than fast/medium/calm, a `gpu_encode` other than
auto/on/off, a missing `footage_dir` each stop the run in the first second with a Vietnamese
message, e.g.
`Lỗi: brand.logo_scale = 0.6 không hợp lệ: phải là số trong khoảng 0.05–0.5.`

The example brand, logo and script are not part of this repository. The sample `logo:` line is
commented out, so a fresh clone runs without a logo; to add yours, put it at `brand/logo.png` (the
folder is git-ignored) and remove the `# ` in front of `logo:`. Put your script at
`scripts/buoi-tap.txt`, or pass `--script`.

#### Brand: worked example

With the `brand` block above, `name`, `tagline` and `context` go into the **planning** prompt only,
so shot choice, pacing and the written script reflect the business. They are never sent with the
caption requests: captions describe only what is in the frame. On a real 17-clip gym shoot the
planner opened on a close-up of a strapped hand gripping the bar, ended the script with
"Hoàng Gia Gym & Fitness 24h – Khỏe mỗi ngày", and warned on its own:
`Footage chỉ có phòng gym; không có hình hồ bơi hay sân pickleball nên video không đề cập đến các
tiện ích này.` The brand context does not make it claim footage it does not have.

#### Logo overlay, and why there are no transitions under it

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

#### Style: tone, pace, preferred looks (`style`)

`brand.context` says what the place is and who it is for; the `style` block says how the edit
should feel. All four go into the **planning** prompt only, as a `STYLE` block (nothing is added
when they are all empty):

| Field | Effect on the plan |
|---|---|
| `brand.context` | free text, several lines: what this place is, who the video is for |
| `style.tone` | one line, e.g. `mạnh mẽ, năng động` or `tươi mát, thư giãn` |
| `style.pace` | `fast` = mostly 1.5–3 s clips, quick cuts; `medium` = about 2.5–4.5 s; `calm` = 4–6 s, few cuts; empty = the planner decides |
| `style.prefer_luts` | LUT ids from `catalogue.json`; the planner picks one of them unless none suits the footage, and then says why in a warning |

The footage still comes first: style never makes the planner use a shot it would otherwise
reject. A `prefer_luts` id that is not a LUT in `catalogue.json` stops the run at the startup
check (`style.prefer_luts trong config.yaml có id không phải LUT…`), with a few valid ids to copy. In the watcher these four can be edited without
opening the file (key **S**, **The folder watcher**).

#### GPU encoding (`gpu_encode`)

**What OpenChatCut already does.** `occ render` has no encoder option. For H.264 it chooses by
itself: on Windows it tries NVIDIA NVENC, Intel Quick Sync and AMD AMF in that order (macOS:
VideoToolbox; Linux: NVENC, Quick Sync, VA-API), each with a one-frame trial encode, and uses the
first that works **and** that its renderer's ffmpeg (Remotion's) has compiled in; otherwise
software x264. Two environment variables steer it: `OPENCHATCUT_DISABLE_HARDWARE_ENCODING=1` and
`OPENCHATCUT_H264_ENCODER=<name>`. It does not report which encoder it ended up using.

Remotion's bundled Windows ffmpeg has only **NVENC** compiled in, so through `occ render` an NVIDIA
card is the only hardware encoder that can be used on Windows today. Quick Sync and AMF are probed but
reported as missing from the renderer.

**What videoauto adds:**

| `gpu_encode` | Behaviour |
|---|---|
| `auto` (default) | a hardware encoder is used when the renderer's ffmpeg lists it **and** a one-second trial encode with OpenChatCut's ffmpeg succeeds. A GPU with missing or broken drivers falls back to x264 silently; why is written to the log (`mã hoá video: libx264 … h264_nvenc: exit -1: Cannot load nvcuda.dll`) |
| `on` | the same check, but no usable hardware encoder is an error at startup (`Lỗi: openchatcut.gpu_encode = on nhưng không có bộ mã hoá GPU nào dùng được: …`); a render that OpenChatCut still finished in software fails the build too |
| `off` | always software x264 (`OPENCHATCUT_DISABLE_HARDWARE_ENCODING=1`) |

The probe runs once per process (about a second). Its result is passed to `occ render` through the
two variables above, overriding whatever your shell has set.

**What was actually used** is read back from the finished file: x264 writes its version string
(`x264 - core …`) into the H.264 stream and hardware encoders do not. So a fallback inside
OpenChatCut during the render is caught too. It is recorded in three places:
- the run summary (`mã hoá:    h264_nvenc (NVIDIA NVENC), GPU; gpu_encode=auto`);
- the watcher's `XONG …` line (`…, mã hoá libx264`);
- `<out>.mp4.build.json`, under `"encoder"`: `mode`, `requested`, `used`, `hardware`, `evidence`,
  `reason` and every probe result.

If someone complains about quality later, that file says how the video was encoded.

**Quality.** At the same bitrate (`video_bitrate_mbps`), hardware encoders give slightly lower
quality than software x264: more blocking in fast motion and fine texture such as water, foliage or
gym equipment. Their advantage is speed. When quality matters more than render time, set
`gpu_encode: off`.

On the development laptop (AMD Radeon RX 6600M + integrated Radeon), ffmpeg lists `h264_nvenc`,
`h264_qsv` and `h264_amf`, but none can encode: NVENC has no NVIDIA driver (`Cannot load
nvcuda.dll`), Quick Sync has no Intel GPU, and AMF fails with `CreateComponent(AMFVideoEncoderVCE_AVC)
failed with error 30`. So `auto` uses x264 there and logs those three reasons. The hardware path is
covered by tests with a faked probe, not yet by a real GPU.

#### Following your own script (`script_file`)

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

#### Text overlays (`TEXT:` lines and `brand.colors`)

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

**Asked-for text is delivered or nothing is.** A video without the text its script asks for is
worse than no video: nobody notices until they watch it. So:

- a script with TEXT lines when `catalogue.json` offers no usable template for the target (no
  `vi_ok` template, none in the right orientation, or all in the logo's corner) stops at the
  startup check, before any indexing, with the cause and the fix;
- a plan in which any TEXT line has no overlay after validation stops before building;
- an overlay (or the logo) that OpenChatCut fails to place stops the build before rendering.

The rules are enforced in `videoauto/planner/validate.py`, not only asked for in the prompt. Each
broken rule drops that overlay with a Vietnamese warning (and the rule above then stops the run if
a TEXT line is left without its overlay):

- **Vietnamese-safe templates only.** A template needs `"vi_ok": true` in `catalogue.json` (131 of
  235 pass OpenChatCut's two Vietnamese font audits with upstream OpenChatCut: M2, and M3, which
  checks that the second mark of stacked letters such as Ố, Ớ and Ắ stays attached; 134 with the
  fork's branch `videoauto`, whose font loader completes Windows Georgia with the open Gelasio font
  and reads font lists written `'"Playfair Display", …'`; videoauto bundles the verdicts for both and
  picks the matching ones), and its orientation must match the target (no 16:9
  template on a 9:16 video). This is checked twice: the planner is only shown matching templates,
  and the validator drops any other, including a template whose size is unknown. Either way only
  20 are 9:16. The other ~100 are not font problems (no text field, text too long for its box,
  accents colliding with the line above, props the template ignores) and need template changes. For 9:16 the planner is offered the `social-shorts` group, with
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
`primaryColor`, whose meaning differs between templates). When the default was translucent, the
alpha is kept: `rgba(0,0,0,0.72)` becomes `rgba(26,26,26,0.72)` and `#0f172aee` becomes
`#1A1A1AEE`. For keyword pop that means `accentColor` (text outline, glow and underline bar) =
`#ED1C24` and `textColor` = `#FFFFFF`. Without `brand.colors`, templates keep their own colours.

**Contrast check: a brand colour is never forced onto unreadable text.** Field names alone do not
say what sits behind the text. Many templates draw text on a light card that is fixed in their
code, and some fix the text colour in code while exposing the card colour. Blindly applying the
roles above put white text on near-white cards, or ink cards under dark text. With the example
colours (`#ED1C24` / `#1A1A1A` / `#FFFFFF`) that hit 30 of the 131 `vi_ok` templates. So before a brand colour
is applied, videoauto checks each piece of the template's text against what is behind it. If the
brand colours bring the contrast below **3:1** (WCAG AA for large text) *and* below the template's
own contrast, that colour is not applied:
- the brand colour on the text is dropped first;
- otherwise the one on the backdrop is dropped;
- the template's own colour stays.

Each colour that is held back becomes a plan warning, for example
`overlay 1 (…): giữ màu template cho textColor (chữ): màu thương hiệu #FFFFFF chỉ cho độ tương phản 1.1:1
(cần ≥ 3:1; màu gốc 13.7:1)`. The overlay itself is kept. Text drawn straight over the video is not
checked, because the video behind it is unknown. A translucent card is judged over both black and
white video.

What paints each template's text and its backdrop is **measured**, not guessed, in
`videoauto/planner/overlay_colours.json`. The table does not depend on the brand. To make it, each
template is rendered in its own colours, then once per colour field with that field set to a probe
colour; the pixels that change show which field paints the text and which paints what is behind it.
Rebuild it after OpenChatCut's templates change:
`python -m videoauto.templates --measure-colours` (about 7 minutes; `--only <id>` re-measures one).
A template missing from the table keeps all of its own colours, with a warning.

**How it is built** (probed against `occ` before writing the code): `edit_track create` makes one
video track for all overlays. It is created **before** the logo track, because `occ` stacks a new
track on top, so the order is V1 < overlays < logo. Then, per overlay:
1. `edit_item` `adds:[{type:"motion-graphic", assetId, track, fromFrame}]`. A motion-graphic add
   accepts no duration and, unlike video and image adds, returns **no itemId**;
2. `read_timeline` finds the new item (motion-graphic on that track at that frame);
3. `edit_item` `updates:[{type:"motion-graphic", itemId, durationInFrames}]`;
4. `update_item_props` `{itemId, props}` with every text field plus the brand colours.

The add copies the template's Chinese sample props into the item. If step 3 or 4 fails, the item is
removed again (`remove_item`), so sample text never reaches the render. A failed overlay is
recorded under `"overlays"` in `final.mp4.build.json` and in the warnings, does not count towards
`max_fail_ratio`, and **stops the build before the render** (exit code 6): the script asked for that
text, so a video without it is not delivered. The same holds for the logo.

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

##### Choosing a template by eye: `python -m videoauto.templates --preview`

```
python -m videoauto.templates --preview --out previews/
```

This renders **one frame** of every `vi_ok` template (the only ones a plan may use) with your
`brand.colors` and the sample line `ĐỐT MỠ ĐÓN TẾT`, through OpenChatCut's own Remotion bundle.
It makes no model calls and needs no footage. For each template it tries 60 % of the duration
first, then 45, 80, 30 and 95 %, and keeps the first frame where the text is visible. Output:

- `previews/sheets/NN-horizontal.png` and `NN-vertical.png`: labelled contact sheets on a grey
  checkerboard, so transparent areas show. Each cell is labelled `#n name`, then group, id and
  frame.
- `previews/templates.tsv`: one row per template: `n` (the number on the sheet), `id`, `name`,
  `group`, `orientation`, `animation_fields` (timing knobs such as `wordDelay` and
  `animationSpeed`), `shadow_outline_fields` (`shadowColor`, `strokeWidth`, `borderColor`, glows),
  the text and brand-colour fields that were filled, `kept_template_colour` (brand colours held
  back by the contrast check, and why), the frame, the sheet, and the status. The preview uses
  exactly the colours a build would use.
- `previews/frames/NNN.png`: the full-resolution transparent frame.

The sample line goes only into text fields that hold words. Fields whose default is a number,
year, percentage, URL, a one- or two-character unit, or empty keep their default.
`--text "..."` changes the line and `--only ss-keyword-pop,Quote` limits the run. Without
`--preview`, the command only lists the templates. A full run takes a few minutes and needs
network access for Google Fonts, like `occ render`.

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


### The folder watcher (`python -m videoauto.watch`)

```powershell
python -m videoauto.watch
```

Start it once from a terminal in the videoauto folder (virtual environment active). It stays running
and builds **one video for every new script** (`.txt`) that appears in the scripts folder. The
footage folder is a standing library, not a trigger: adding clips does not start a build, but every
build first indexes any clip not yet in the cache (already-indexed clips cost zero model calls).
`brief`, `brand`, `style`, `openchatcut` and `catalogue` still come from `config.yaml`; its
`footage_dir` and `script_file` are not used by the watcher. The file is read at startup, and read
again after every save from key **S** (below).

**1. Startup checks.** ffmpeg, Node, the OpenChatCut repo, `catalogue.json` (generated if missing
or made from another OpenChatCut), the logo and model access are checked once. **Every** problem is
listed in one message (`Lỗi: Còn 3 việc cần sửa trước khi chạy …`), so one round of fixes is enough;
then it exits with code 2. On a first run (no `videoauto.local.yaml` yet, or a `config.yaml` that
still holds the sample paths marked `CHANGE`) the three questions below come **first** and are
saved, and the check runs after them: a new user always gets to answer them.

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

**3. Folders.** Everything goes **inside the output folder** you chose, next to the videos; the
watcher never writes anywhere you did not name:

| Folder | What it holds |
|---|---|
| `processing/` | the script being built right now (at most one) |
| `done/` | scripts that produced a video |
| `failed/` | scripts that failed, each with `<name>.loi.txt` saying what failed and what to do |
| `.index/` | the index cache shared by every build |
| `.logs/` | `runs.log` (one line per run: time, script, result, duration, model calls) and one full log per run |

So with output `D:\video\xuat` they are `D:\video\xuat\processing`, `D:\video\xuat\done`, and so on.

**Footage is hashed once.** The index cache is keyed by each file's sha256, and that stays the
identity check. Reading a 4K library to hash it takes seconds per GB, though, so videoauto remembers
each file's hash together with its path, size and modification time (`.index/hashes.memo`) and
reads the file again only when one of those changed. An unchanged library costs no hashing on later
builds, also after a restart; a replaced or re-exported clip is hashed and, if its content differs,
indexed again. A file rewritten with the same size **and** the same modification time would be
missed. Cameras and editors do not do that; only a tool that deliberately restores the old time
could.

**Old indexes are kept, not deleted.** When a clip is replaced or removed, its old index stays in
`.index/`. The planner skips it and says so in a single line,
`bỏ qua 15 chỉ mục cũ không còn tệp tương ứng trong thư mục video`, rather than one warning per
file. Nothing is deleted automatically: if the footage folder is on a network or external drive that
is briefly unreachable, an automatic clean-up would throw away indexes that cost model calls to
rebuild. To clean up on purpose, see **Old indexes** under **Troubleshooting**.

**4. What happens to a script.** For `tap-nguc.txt` dropped into the scripts folder:
1. it waits until the file size has not changed for 3 s (an editor may still be saving it);
2. it moves to `processing/` at once, so it can never be picked up twice;
3. index (new clips only) → plan → build, exactly as `videoauto.run`, with this script as `script_file`;
4. `tap-nguc.mp4`, `tap-nguc.mp4.build.json` and `tap-nguc.plan.json` are written into the output
   folder. **Nothing is ever overwritten:** if `tap-nguc.mp4` (or its `.plan.json` or
   `.mp4.build.json`) already exists, the new video is `tap-nguc-2.mp4` with its own
   `tap-nguc-2.plan.json` and `tap-nguc-2.mp4.build.json`, then `-3`, and so on;
5. the script moves to `done/` (as `tap-nguc-2.txt` in that case, matching its video), or to
   `failed/` with `tap-nguc.loi.txt`, and the next one starts. A failed build keeps its `.plan.json`
   / `.build.json` for diagnosis, so the retry gets the next free name.

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

**5. Keys while it runs.** In a console window the watcher reads single key presses without
blocking, and shows this hint line every time it goes idle:

```
[15:02:11] Đang chờ kịch bản mới trong D:\kich-ban (Ctrl+C để dừng)
           [S] sửa phong cách   [P] xem đường dẫn   [Q] thoát
```

- **S**: edit the four fields that steer the planner (see **Style** under **Configure**), typed by you
  rather than picked from presets:

  ```
  Sửa phong cách (Enter = giữ nguyên, '-' = xoá trống).
  1. context (bối cảnh: nơi này là gì, dành cho ai). Hiện tại:
       Chuỗi phòng tập, hồ bơi và sân pickleball ở Bình Dương.
     Gõ nội dung mới, nhiều dòng; kết thúc bằng một dòng chỉ có dấu chấm (.)
     Enter ngay ở dòng đầu = giữ nguyên.
     > Phòng tập 24h ở Thủ Dầu Một, dành cho sinh viên.
     > .
  2. tone (giọng điệu, một dòng) [(trống)]: mạnh mẽ, năng động
  3. pace (fast | medium | calm) [(trống)]: fast
  4. prefer_luts (id LUT, cách nhau bằng dấu phẩy) [(trống)]: builtin:look-teal-orange
  Giá trị mới:
   *1. context:
         Phòng tập 24h ở Thủ Dầu Một, dành cho sinh viên.
   *2. tone: mạnh mẽ, năng động
   …
  Lưu vào config.yaml? [Y/n]
  ```

  Each field shows its current value; Enter keeps it and `-` clears it. `context` takes several
  lines and ends with a line holding only a dot. A `pace` other than fast/medium/calm, or a LUT id
  that is not in `catalogue.json`, is asked again (the available LUT ids are listed). Nothing is
  written unless you answer **Y** at the end.
- **P**: show the folders and the `config.yaml` in use.
- **Q**: quit. During a build it asks `Thoát sau khi dựng xong? [Y/n]` and exits once that build is
  finished; it never cuts a build short (Ctrl+C still abandons one, as above).

How a save works:
- `config.yaml` is rewritten with ruamel.yaml in round-trip mode, so every comment (the **CHANGE**
  markers too), the key order, the quoting and the comment columns stay as they were. Only the four
  values change. A file without a `style:` block gets one after `brand:`.
- The write is atomic: the new text goes to a temporary file in the same folder, is flushed to disk
  and checked (it must load as a valid config), and only then replaces `config.yaml`. A crash or
  power cut mid-save leaves the old `config.yaml` whole, never a truncated one.
- If `config.yaml` was saved by something else after the editor opened, the watcher says so and asks
  `Vẫn ghi 4 giá trị này vào bản mới nhất? [y/N]` (default no). On yes, the four values go into the
  file as it is now, so the other person's edits are kept.

When the new values apply:
- **Next build.** The next build uses the new values. A build that is already running keeps the
  config it started with: **S** during a build first says the change will apply after the current
  build and asks `Tiếp tục sửa?`.
- **No build starts mid-edit.** While the editor is open, no new build starts, so a script dropped
  in at that moment is built with what you are typing. Status lines from a running build wait until
  the editor closes, so they never break into a question.

The keys use `msvcrt` (Windows). On other systems the watcher prints
`Phím tắt (S/P/Q) không dùng được trên hệ điều hành này; vẫn theo dõi bình thường…` and keeps
watching; edit `config.yaml` by hand there. Under `--no-prompt`, or when stdin is not a console
(Task Scheduler, a service, `< NUL`), no keyboard listener is started at all and no hint is shown.

#### Starting it at logon (later)

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


### One build from the command line (`python -m videoauto.run`)

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

#### Debugging one stage at a time

```
python -m videoauto.index <folder> --out output/index
python -m videoauto.plan  output/index --out output/plan.json        # footage_dir/script_file from config.yaml
python -m videoauto.build output/plan.json --out output/final.mp4    # both accept --footage; plan accepts --script
python -m videoauto.index <folder> --out output/index --calibrate-blur  # local blur report, no model calls
python -m videoauto.index [<folder>] --prune --out output/index        # list, confirm, remove old indexes
```

Add `--debug` to any command for a full Python traceback.


### What this does not do yet

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
  Footage whose orientation differs from the target (e.g. landscape into 9:16) is cropped at the
  centre to fill the frame (`brief.fit`), not reframed around the subject.
- The per-shot `motion` value saturates (0.83–1.00 on every shot of a real 4K gym shoot), so it
  does not yet distinguish calm from busy shots.
- Shot choice leans heavily on `sharpness`: on a 17-clip test the plan used exactly the five
  sharpest shots. Sharpness also depends on content, so detail close-ups tend to rank low.
- Developed and tested on Windows 11 only.


### How it works (short)

- **M1 index** (`videoauto/index.py`): ffprobe metadata; PySceneDetect shot boundaries; keyframes;
  OpenCV motion, camera movement and per-shot `sharpness`; ffmpeg black/frozen/silent detection;
  Vietnamese captions from 3x3 contact sheets, one model call per sheet. A source larger than
  1280 px is decoded **once** into a 640 px proxy (`videoauto/proxy.py`, on the GPU with NVDEC when
  an NVIDIA driver and an ffmpeg with cuda are present) and cuts, motion, bars, black, freeze and
  silence are measured on it; before, each 4K file was decoded in full three times. On 21–30 s 4K
  clips this halved the local analysis (8.5 s → 4.2 s per clip, same results). Keyframes for the
  captions still come from the source. Up to `vision.concurrency` (default 3, `videoauto.toml`)
  sheets are described at the same time, while the next files are analysed. Only a clip's first
  indexing pays this; a cached clip costs nothing. One JSON per
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
  `OPENCHATCUT_DATA_DIR`. A clip of another shape than the target gets an `edit_item` transform
  scale that fills the frame (`brief.fit`). A failed item is skipped and recorded; the build aborts
  above 30% failures, and always when a text overlay or the logo could not be placed. The render
  counts as done only when the file exists, is not empty and is as long as the timeline; a failed
  build keeps its video as `<name>.loi.mp4`. Render time is OpenChatCut's (Remotion draws every
  frame); videoauto does not change it.

---

## License

AGPL-3.0-or-later, the same as OpenChatCut. See `LICENSE`.
