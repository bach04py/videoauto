// Overlay template preview renderer, driven by `python -m videoauto.templates --preview`.
//
//   node preview_render.mjs <job.json>        (cwd = the OpenChatCut repo)
//
// Renders one transparent still per template with OpenChatCut's own Remotion serve bundle
// (the same pipeline `occ render` uses), then lays the stills out on labelled contact sheets.
// Nothing in the OpenChatCut repo is changed; its packages are resolved from job.repo, so this
// file can live inside videoauto. No network calls except the font loads Remotion itself makes.
//
// job.json: { repo, out, concurrency, frameCandidates, minTextPx, title, colors,
//             templates: [{ n, id, source, name, group, overrides, fill, text }] }
//   overrides: props set on top of the template defaults (brand colours + sample text)
//   fill:      the text props that carry the sample text; the frame is chosen by rendering them
//              as a zero-width space too and keeping the first candidate where text is visible.
// Writes <out>/frames/NNN.png, <out>/sheets/*.png and <out>/render-results.json.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const job = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const REPO = resolve(job.repo);
const OUT = resolve(job.out);
const req = createRequire(join(REPO, 'package.json'));
const { openBrowser, renderStill, selectComposition } = req('@remotion/renderer');
const sharp = req('sharp');
const { createCanvas, loadImage } = req('@napi-rs/canvas');
const { getServeUrl, disposeServeBundle } = await import(pathToFileURL(join(REPO, 'remotion', 'serve-bundle.mjs')).href);

const ZW = '​';
const pad3 = (n) => String(n).padStart(3, '0');

const sources = new Map();
function template(source, id) {
  if (!sources.has(source)) {
    const list = JSON.parse(readFileSync(join(REPO, source), 'utf8'));
    sources.set(source, new Map(list.map((t) => [t.id, t])));
  }
  const tpl = sources.get(source).get(id);
  if (!tpl) throw new Error(`template ${id} not found in ${source}`);
  return tpl;
}

function stateFor(tpl, props) {
  return {
    fps: tpl.fps || 30, width: tpl.width, height: tpl.height, selectedId: null,
    tracks: { track_v1: { kind: 'video' } }, trackOrder: ['track_v1'],
    items: [{
      id: `pv_${tpl.id}`, kind: 'motion-graphic', templateId: tpl.id, name: tpl.name, code: tpl.code,
      props, width: tpl.width, height: tpl.height, track: 'track_v1', startFrame: 0,
      durationInFrames: tpl.durationInFrames,
    }],
  };
}

/** Pixels that differ between two RGBA buffers of the same size (premultiplied). */
function diffCount(a, b, threshold = 40) {
  let count = 0;
  for (let o = 0; o < a.length; o += 4) {
    const aa = a[o + 3] / 255; const ba = b[o + 3] / 255;
    let d = Math.abs(a[o + 3] - b[o + 3]);
    for (let c = 0; c < 3; c++) d += Math.abs(a[o + c] * aa - b[o + c] * ba);
    if (d > threshold) count++;
  }
  return count;
}

const rawOf = async (png) => (await sharp(png).ensureAlpha().raw().toBuffer());

async function main() {
  mkdirSync(join(OUT, 'frames'), { recursive: true });
  mkdirSync(join(OUT, 'sheets'), { recursive: true });
  const serveUrl = await getServeUrl();
  const browser = await openBrowser('chrome', { chromiumOptions: { gl: 'angle' } });
  const started = Date.now();

  async function still(tpl, props, frame) {
    const inputProps = { state: stateFor(tpl, props), transparent: true };
    const composition = await selectComposition({
      serveUrl, id: 'timeline', inputProps, puppeteerInstance: browser, timeoutInMilliseconds: 90_000,
    });
    const f = Math.max(0, Math.min(composition.durationInFrames - 1, frame));
    const { buffer } = await renderStill({
      serveUrl, composition, inputProps, frame: f, imageFormat: 'png', output: null,
      puppeteerInstance: browser, chromiumOptions: { gl: 'angle' }, timeoutInMilliseconds: 90_000,
    });
    if (!buffer) throw new Error('renderStill returned no buffer');
    return buffer;
  }

  const results = new Array(job.templates.length);
  let next = 0; let done = 0;

  async function renderOne(index) {
    const t = job.templates[index];
    const base = { n: t.n, id: t.id };
    try {
      const tpl = template(t.source, t.id);
      const props = { ...tpl.props, ...t.overrides };
      const blank = { ...props };
      for (const k of t.fill) blank[k] = ZW;
      const dur = Math.max(1, tpl.durationInFrames);
      let best = null;
      for (const fraction of job.frameCandidates) {
        const f = Math.round((dur - 1) * fraction);
        const png = await still(tpl, props, f);
        // No text field to fill: nothing to compare, take the first candidate.
        const textPx = t.fill.length ? diffCount(await rawOf(png), await rawOf(await still(tpl, blank, f))) : null;
        if (!best || (textPx ?? 0) > (best.textPx ?? 0)) best = { frame: f, png, textPx };
        if (textPx === null || textPx >= job.minTextPx) break;
      }
      const file = `frames/${pad3(t.n)}.png`;
      writeFileSync(join(OUT, file), best.png);
      results[index] = {
        ...base, status: best.textPx === null || best.textPx >= job.minTextPx ? 'ok' : 'text_not_visible',
        frame: best.frame, durationInFrames: dur, textPx: best.textPx, file, width: tpl.width, height: tpl.height,
      };
    } catch (error) {
      results[index] = { ...base, status: 'error', error: String(error?.message ?? error).slice(0, 400) };
    } finally {
      done++;
      if (done % 10 === 0 || done === job.templates.length) {
        console.error(`  ${done}/${job.templates.length}  ${Math.round((Date.now() - started) / 1000)}s`);
      }
    }
  }

  const worker = async () => { while (next < job.templates.length) await renderOne(next++); };
  try {
    await Promise.all(Array.from({ length: Math.max(1, job.concurrency) }, worker));
  } finally {
    await browser.close({ silent: true }).catch(() => undefined);
    await disposeServeBundle().catch(() => undefined);
  }
  await buildSheets(results);
  writeFileSync(join(OUT, 'render-results.json'), JSON.stringify({
    elapsedSeconds: Math.round((Date.now() - started) / 1000), results,
  }, null, 1));
}

// ------------------------------------------------------------------ contact sheets

const LAYOUTS = {   // per orientation: cell size and grid
  horizontal: { cw: 640, ch: 360, cols: 3, rows: 4 },
  vertical: { cw: 300, ch: 533, cols: 5, rows: 2 },
  square: { cw: 420, ch: 420, cols: 4, rows: 3 },
};
const GAP = 16; const LABEL = 46; const HEADER = 64;
const FONT = '"Segoe UI", "Microsoft YaHei", "Noto Sans", sans-serif';

function orientationOf(r) {
  return r.width === r.height ? 'square' : r.height > r.width ? 'vertical' : 'horizontal';
}

function checker(ctx, x, y, w, h) {
  const s = 16;
  for (let yy = 0; yy < h; yy += s) {
    for (let xx = 0; xx < w; xx += s) {
      ctx.fillStyle = ((xx + yy) / s) % 2 ? '#6b7280' : '#7c8492';
      ctx.fillRect(x + xx, y + yy, Math.min(s, w - xx), Math.min(s, h - yy));
    }
  }
}

function fit(ctx, text, maxWidth) {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let s = text;
  while (s.length > 1 && ctx.measureText(`${s}…`).width > maxWidth) s = s.slice(0, -1);
  return `${s}…`;
}

async function buildSheets(results) {
  const meta = new Map(job.templates.map((t) => [t.n, t]));
  const groups = { horizontal: [], vertical: [], square: [] };
  for (const r of results) if (r?.file) groups[orientationOf(r)].push(r);
  const pages = [];
  for (const [orient, list] of Object.entries(groups)) {
    const per = LAYOUTS[orient].cols * LAYOUTS[orient].rows;
    for (let i = 0; i < list.length; i += per) pages.push({ orient, items: list.slice(i, i + per) });
  }
  const counts = {};
  for (const [p, page] of pages.entries()) {
    const { cw, ch, cols, rows } = LAYOUTS[page.orient];
    counts[page.orient] = (counts[page.orient] ?? 0) + 1;
    const name = `sheets/${String(p + 1).padStart(2, '0')}-${page.orient}.png`;
    const W = GAP + cols * (cw + GAP);
    const H = HEADER + rows * (ch + LABEL + GAP) + GAP;
    const canvas = createCanvas(W, H);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#16181d'; ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = '#f3f4f6'; ctx.font = `bold 22px ${FONT}`;
    ctx.fillText(`${job.title}  ·  ${page.orient}  ·  sheet ${p + 1}/${pages.length}`, GAP, 30);
    ctx.font = `15px ${FONT}`; ctx.fillStyle = '#9ca3af';
    ctx.fillText(`#${page.items[0].n}–#${page.items.at(-1).n}  ·  text: "${job.text}"  ·  number = row n in templates.tsv`, GAP, 52);
    let sx = W - GAP;
    for (const [role, hex] of Object.entries(job.colors ?? {}).reverse()) {
      ctx.font = `14px ${FONT}`;
      const label = `${role} ${hex}`;
      const lw = ctx.measureText(label).width;
      sx -= lw + 30;
      ctx.fillStyle = hex; ctx.fillRect(sx, 18, 20, 20);
      ctx.strokeStyle = '#9ca3af'; ctx.strokeRect(sx + 0.5, 18.5, 19, 19);
      ctx.fillStyle = '#d1d5db'; ctx.fillText(label, sx + 26, 33);
      sx -= 14;
    }
    for (const [i, r] of page.items.entries()) {
      const x = GAP + (i % cols) * (cw + GAP);
      const y = HEADER + Math.floor(i / cols) * (ch + LABEL + GAP);
      checker(ctx, x, y, cw, ch);
      const img = await loadImage(readFileSync(join(OUT, r.file)));
      ctx.drawImage(img, x, y, cw, ch);
      if (r.status !== 'ok') {
        ctx.fillStyle = '#b91c1c'; ctx.fillRect(x, y, 150, 22);
        ctx.fillStyle = '#fff'; ctx.font = `bold 13px ${FONT}`; ctx.fillText('text not visible', x + 8, y + 16);
      }
      const t = meta.get(r.n);
      ctx.fillStyle = '#fbbf24'; ctx.font = `bold 16px ${FONT}`;
      ctx.fillText(fit(ctx, `#${r.n}  ${t.name}`, cw), x, y + ch + 19);
      ctx.fillStyle = '#9ca3af'; ctx.font = `13px ${FONT}`;
      ctx.fillText(fit(ctx, `${t.group} · ${/^[0-9a-f]{8}-/.test(t.id) ? t.id.slice(0, 8) : t.id} · frame ${r.frame}/${r.durationInFrames}`, cw), x, y + ch + 38);
      r.sheet = name;
    }
    writeFileSync(join(OUT, name), canvas.toBuffer('image/png'));
  }
}

await main();
