// Overlay template preview renderer, driven by `python -m videoauto.templates --preview`.
//
//   node preview_render.mjs <job.json>        (cwd = the OpenChatCut repo)
//
// Renders one transparent still per template with OpenChatCut's own Remotion serve bundle
// (the same pipeline `occ render` uses), then lays the stills out on labelled contact sheets.
// Nothing in the OpenChatCut repo is changed; its packages are resolved from job.repo, so this
// file can live inside videoauto. No network calls except the font loads Remotion itself makes.
//
// job.json: { mode, repo, out, concurrency, frameCandidates, minTextPx, title, colors,
//             templates: [{ n, id, source, name, group, overrides, fill, text }] }
//   overrides: props set on top of the template defaults (brand colours + sample text)
//   fill:      the text props that carry the sample text; the frame is chosen by rendering them
//              as a zero-width space too and keeping the first candidate where text is visible.
// mode "preview" (default): writes <out>/frames/NNN.png, <out>/sheets/*.png, <out>/render-results.json.
// mode "measure": brand-independent colour attribution (no brand colours in overrides). For the
//   pixels of the sample text it finds which colour prop paints the text and which paints what is
//   behind it (else the fixed colour, or "video" where the overlay is transparent), by rendering
//   every colour prop once with a probe colour. Writes <out>/colour-measure.json.
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

/** Premultiplied difference of pixel i between two RGBA buffers. */
function pdiff(a, b, i) {
  const o = i * 4; const aa = a[o + 3] / 255; const ba = b[o + 3] / 255;
  let d = Math.abs(a[o + 3] - b[o + 3]);
  for (let c = 0; c < 3; c++) d += Math.abs(a[o + c] * aa - b[o + c] * ba);
  return d;
}

const T_TEXT = 80;     // text pixel: sample vs zero-width render differ by more than this
const T_CHANGE = 60;   // a probe colour changed this pixel
const PROBES = ['#00ff00', '#ff00ff'];

function hexRgb(v) {
  const m = /^#([0-9a-f]{6})/i.exec(String(v ?? '').trim());
  return m ? [0, 2, 4].map((k) => parseInt(m[1].slice(k, k + 2), 16)) : null;
}

/** The probe colour farther from the prop's default, so the change always shows. */
function probeFor(def) {
  const d = hexRgb(def);
  if (!d) return PROBES[0];
  const dist = (p) => hexRgb(p).reduce((s, v, k) => s + Math.abs(v - d[k]), 0);
  return dist(PROBES[0]) >= dist(PROBES[1]) ? PROBES[0] : PROBES[1];
}

function median(values) {
  const v = Float64Array.from(values).sort();
  return v.length ? v[v.length >> 1] : 0;
}

function medianColour(buf, idx) {
  const ch = [0, 1, 2, 3].map((c) => Math.round(median(idx.map((i) => buf[i * 4 + c]))));
  return `rgba(${ch[0]},${ch[1]},${ch[2]},${(ch[3] / 255).toFixed(2)})`;
}

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
        const blankPng = t.fill.length ? await still(tpl, blank, f) : null;
        const textPx = blankPng ? diffCount(await rawOf(png), await rawOf(blankPng)) : null;
        if (!best || (textPx ?? 0) > (best.textPx ?? 0)) best = { frame: f, png, blankPng, textPx };
        if (textPx === null || textPx >= job.minTextPx) break;
      }
      if (job.mode === 'measure') {
        results[index] = { ...base, ...(await measure(tpl, props, blank, best)) };
        return;
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
  if (job.mode === 'measure') {
    writeFileSync(join(OUT, 'colour-measure.json'), JSON.stringify({
      elapsedSeconds: Math.round((Date.now() - started) / 1000), results,
    }, null, 1));
    return;
  }

  /** Who paints each sample-text pixel, and who paints what is behind it. */
  async function measure(tpl, props, blank, best) {
    const colourKeys = (tpl.propSchema ?? []).filter((s) => s.type === 'color').map((s) => s.key);
    const defaults = Object.fromEntries(colourKeys.map((k) => [k, tpl.props?.[k] ?? null]));
    const out = { frame: best.frame, textPx: best.textPx, defaults, groups: [] };
    if (!best.blankPng) return { ...out, status: 'no_text_field' };
    const S0 = await rawOf(best.png); const Z0 = await rawOf(best.blankPng);
    const mask = [];
    for (let i = 0; i < S0.length / 4; i++) if (pdiff(S0, Z0, i) > T_TEXT) mask.push(i);
    if (mask.length < job.minTextPx) return { ...out, status: 'text_not_visible' };
    const textSrc = new Int16Array(mask.length).fill(-1); const textBest = new Float32Array(mask.length);
    const bdSrc = new Int16Array(mask.length).fill(-1); const bdBest = new Float32Array(mask.length);
    for (const [ki, key] of colourKeys.entries()) {
      const probe = probeFor(defaults[key]);
      const S = await rawOf(await still(tpl, { ...props, [key]: probe }, best.frame));
      const Z = await rawOf(await still(tpl, { ...blank, [key]: probe }, best.frame));
      for (let j = 0; j < mask.length; j++) {
        const i = mask[j]; const ds = pdiff(S, S0, i); const dz = pdiff(Z, Z0, i);
        if (dz > T_CHANGE && dz > bdBest[j]) { bdBest[j] = dz; bdSrc[j] = ki; }
        if (ds > T_CHANGE && dz <= T_CHANGE && ds > textBest[j]) { textBest[j] = ds; textSrc[j] = ki; }
      }
    }
    const groups = new Map();
    for (let j = 0; j < mask.length; j++) {
      const i = mask[j];
      const bd = bdSrc[j] >= 0 ? `p${bdSrc[j]}` : Z0[i * 4 + 3] < 128 ? 'video' : 'fixed';
      const k = `${textSrc[j]}|${bd}`;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(i);
    }
    const minPx = Math.max(150, Math.round(mask.length * 0.03));
    for (const [k, idx] of groups) {
      if (idx.length < minPx) continue;
      const [ts, bd] = k.split('|');
      out.groups.push({
        px: idx.length,
        text: Number(ts) >= 0 ? { param: colourKeys[Number(ts)] } : { fixed: medianColour(S0, idx) },
        backdrop: bd === 'video' ? { video: true } : bd === 'fixed' ? { fixed: medianColour(Z0, idx) }
          : { param: colourKeys[Number(bd.slice(1))] },
      });
    }
    out.groups.sort((a, b) => b.px - a.px);
    return { ...out, status: 'ok' };
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
