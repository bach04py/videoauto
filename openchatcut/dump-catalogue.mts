// Dump the effect / LUT / transition / motion-graphic template catalogue to a
// single JSON file. Read-only: imports the same registries the editor and the
// headless tools use and writes nothing but the output file.
//
//   npx tsx scripts/dump-catalogue.mts [out=catalogue.json]
//
// Shape: { generatedAt, sources, counts, items: CatalogueItem[] } where every item
// has id, assetId (what edit_item / manage_effects / add_motion_graphic accept),
// name, kind and params[] with default/min/max (null where the code defines none).
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ALL_FX, FX_EFFECTS, FX_ORDER, LUT_EFFECTS, LUT_ORDER, type FxDef } from '../src/gl/fx/effects.ts';
import { GLSL_TRANSITIONS } from '../src/gl/transitions.ts';
import { TRANSITION_LABELS } from '../src/editor/types.ts';
import type { PropSpec, Tpl } from '../src/types.ts';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = resolve(process.argv[2] ?? join(ROOT, 'catalogue.json'));

const TEMPLATE_FILES = [
  'assets/templates/openchatcut-templates.json',
  'assets/templates/social-shorts-templates.json',
  'assets/templates/koubo-scenes-templates.json',
];

interface CatalogueParam {
  key: string;
  label: string | null;
  type: string;
  default: unknown;
  min: number | null;
  max: number | null;
  step: number | null;
  /** false = baked into the shader/def; not settable per item. */
  editable: boolean;
  options?: unknown[];
  uniform?: string;
  note?: string;
}

interface CatalogueItem {
  id: string;
  assetId: string;
  name: string;
  kind: 'effect' | 'lut' | 'custom-effect' | 'transition' | 'template';
  description: string | null;
  category?: string;
  params: CatalogueParam[];
  [extra: string]: unknown;
}

const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

function fxParams(def: FxDef): CatalogueParam[] {
  return def.props.map((prop) => {
    if (prop.kind === 'color') {
      return {
        key: prop.key, label: prop.label, type: 'color-rgb01', default: prop.default,
        min: 0, max: 1, step: null, editable: true,
        ...(prop.uniform ? { uniform: prop.uniform } : {}),
        note: 'RGB array, each channel 0..1',
      };
    }
    return {
      key: prop.key, label: prop.label, type: 'number', default: prop.default,
      min: num(prop.min), max: num(prop.max), step: num(prop.step), editable: true,
      ...(prop.uniform ? { uniform: prop.uniform } : {}),
    };
  });
}

function fxItem(def: FxDef, kind: CatalogueItem['kind']): CatalogueItem {
  return {
    id: def.id,
    assetId: def.id,
    name: def.name,
    kind,
    description: def.desc ?? null,
    params: fxParams(def),
    passes: def.passes?.length ?? (def.pipeline ? 'pipeline' : 1),
    ...(def.cube ? { cube: def.cube } : {}),
  };
}

function ordered(record: Record<string, FxDef>, order: readonly string[]): FxDef[] {
  const seen = new Set<string>();
  const out: FxDef[] = [];
  for (const id of [...order, ...Object.keys(record)]) {
    if (seen.has(id) || !record[id]) continue;
    seen.add(id);
    out.push(record[id]);
  }
  return out;
}

function transitionItems(): CatalogueItem[] {
  return Object.entries(GLSL_TRANSITIONS).map(([type, def]) => {
    const uniforms = def.uniforms({ time: 0, aspect: 16 / 9, direction: 'left' });
    const fixed: CatalogueParam[] = Object.entries(uniforms)
      .filter(([key]) => key !== 'u_time' && key !== 'u_ratio')
      .map(([key, value]) => ({
        key, label: null, type: Array.isArray(value) ? `vec${value.length}` : 'number',
        default: value, min: null, max: null, step: null, editable: false,
        note: 'shader uniform hard-coded in src/gl/transitions.ts',
      }));
    const itemLevel: CatalogueParam[] = [
      {
        key: 'durationInFrames', label: 'Duration', type: 'integer', default: null,
        min: 1, max: null, step: 1, editable: true,
        note: 'TransitionItem.durationInFrames; half before and half after the cut. No default in the type; edit_item applies its own.',
      },
      {
        key: 'direction', label: 'Direction', type: 'enum', default: 'left',
        min: null, max: null, step: null, editable: true,
        options: ['left', 'right', 'up', 'down'],
        note: 'TransitionItem.direction; only directional transitions read it',
      },
    ];
    const labels = TRANSITION_LABELS as Record<string, string | undefined>;
    return {
      id: type,
      assetId: `builtin:tr-${type}`,
      name: labels[type] ?? type,
      kind: 'transition' as const,
      description: null,
      params: [...itemLevel, ...fixed],
    };
  });
}

function templateParam(spec: PropSpec): CatalogueParam {
  return {
    key: spec.key,
    label: spec.label ?? null,
    type: spec.type,
    default: spec.defaultValue,
    min: num(spec.min),
    max: num(spec.max),
    step: num(spec.step),
    editable: true,
    ...(spec.options ? { options: spec.options } : {}),
  };
}

function templateItems(): CatalogueItem[] {
  const items: CatalogueItem[] = [];
  for (const file of TEMPLATE_FILES) {
    const templates = JSON.parse(readFileSync(join(ROOT, file), 'utf8')) as Tpl[];
    for (const tpl of templates) {
      items.push({
        id: tpl.id,
        assetId: `library:motion-graphic:${tpl.id}`,
        name: tpl.name,
        kind: 'template',
        description: tpl.description ?? null,
        category: tpl.category,
        source: file,
        width: tpl.width,
        height: tpl.height,
        fps: tpl.fps,
        durationInFrames: tpl.durationInFrames,
        tags: tpl.tags ?? [],
        params: (tpl.propSchema ?? []).map(templateParam),
      });
    }
  }
  return items;
}

// Vietnamese diacritics verdicts (vi-font-review.json, produced from
// scripts/vi-font-check.mts renders + visual review) are merged onto templates.
interface ViReview { id: string; vi_ok: boolean; vi_status: string; vi_issues: unknown[]; vi_warnings?: string[] }
const VI_REVIEW_PATH = join(ROOT, 'vi-font-review.json');
const viReview = new Map<string, ViReview>();
try {
  for (const entry of (JSON.parse(readFileSync(VI_REVIEW_PATH, 'utf8')) as { templates: ViReview[] }).templates) {
    viReview.set(entry.id, entry);
  }
} catch {
  // No review file yet: templates are emitted without vi_* fields.
}

function withViReview(item: CatalogueItem): CatalogueItem {
  const review = viReview.get(item.id);
  if (!review) return item;
  return {
    ...item,
    vi_ok: review.vi_ok,
    vi_status: review.vi_status,
    vi_issues: review.vi_issues,
    ...(review.vi_warnings ? { vi_warnings: review.vi_warnings } : {}),
  };
}

const effects = ordered(FX_EFFECTS, FX_ORDER).map((def) => fxItem(def, 'effect'));
const luts = ordered(LUT_EFFECTS, LUT_ORDER).map((def) => fxItem(def, 'lut'));
const known = new Set([...Object.keys(FX_EFFECTS), ...Object.keys(LUT_EFFECTS)]);
// ALL_FX = FX_EFFECTS + LUT_EFFECTS + anything registered at runtime (empty here).
const custom = Object.values(ALL_FX).filter((def) => !known.has(def.id)).map((def) => fxItem(def, 'custom-effect'));
const transitions = transitionItems();
const templates = templateItems().map(withViReview);
const items = [...effects, ...luts, ...custom, ...transitions, ...templates];

const ids = new Set<string>();
const duplicates = items.filter((item) => (ids.has(item.assetId) ? true : (ids.add(item.assetId), false)));

const catalogue = {
  generatedAt: new Date().toISOString(),
  sources: {
    effects: 'src/gl/fx/shaderEffects.ts (FX_EFFECTS)',
    luts: 'src/gl/fx/lutEffects.ts (LUT_EFFECTS)',
    allFx: 'src/gl/fx/effects.ts (ALL_FX)',
    transitions: 'src/gl/transitions.ts (GLSL_TRANSITIONS) + TRANSITION_LABELS',
    templates: TEMPLATE_FILES,
  },
  counts: {
    effect: effects.length,
    lut: luts.length,
    customEffect: custom.length,
    allFx: Object.keys(ALL_FX).length,
    transition: transitions.length,
    template: templates.length,
    total: items.length,
    duplicateAssetIds: duplicates.map((item) => item.assetId),
    templateViOk: templates.filter((item) => item.vi_ok === true).length,
    templateViReviewed: templates.filter((item) => typeof item.vi_ok === 'boolean').length,
  },
  items,
};

writeFileSync(OUT, `${JSON.stringify(catalogue, null, 2)}\n`);
console.log(`wrote ${OUT}`);
console.log(JSON.stringify(catalogue.counts));
