import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

async function moduleUrl(path, replacements = {}) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [from, to] of Object.entries(replacements)) source = source.replaceAll(from, to);
  const { outputText } = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext,
  } });
  return 'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64');
}
const colorsUrl = await moduleUrl('../src/constants/colors.ts');
const comparisonUrl = await moduleUrl('../src/utils/fullFlightComparison.ts', { '../constants/colors': colorsUrl });
const { flightWindowTraces, windowsAlign, windowColumnsAlign, FULL_FLIGHT_VARIABLE_DASHES } = await import(
  await moduleUrl('../src/utils/fullFlightTime.ts', { './fullFlightComparison': comparisonUrl }));
const raw = (t = [10, 10.5, 11], series = { rpm: [100, null, 300], torque: [1, 2, 3] }) =>
  ({ mode: 'raw', level: 1, n_raw: t.length, i0: 20, i1: 20 + t.length, t, series });
const entry = (window = raw(), patch = {}) => ({
  source: { test: 'Flight A', color: '#a04020', columns: ['rpm', 'torque'], timeColumn: 'time', timeOffset: -10, fs: 2 },
  columns: ['rpm', 'torque'], window, query: { t0: null, t1: null, px: 600, display: 'auto' }, ...patch,
});

test('flights retain independent native sample grids while shifting displayed time', () => {
  const first = entry();
  const second = entry(raw([100, 100.2, 100.4, 100.6], { rpm: [2, 4, 6, 8] }), {
    source: { ...first.source, test: 'Flight B', timeOffset: -100, fs: 5 }, columns: ['rpm'],
  });
  const a = flightWindowTraces(first, first.window, ['rpm', 'torque'], 'original', false);
  const b = flightWindowTraces(second, second.window, ['rpm', 'torque'], 'original', false);
  assert.deepEqual(a[0].t, [0, .5, 1]);
  assert.equal(b[0].t.length, 4);
  assert.ok(Math.abs(b[0].t[1] - .2) < 1e-12);
  assert.deepEqual(b[0].y, [2, 4, 6, 8]);
  assert.deepEqual(first.window.t, [10, 10.5, 11]);
  assert.deepEqual(second.window.t, [100, 100.2, 100.4, 100.6]);
});

test('missing variables retain their global line pattern and flight hue', () => {
  const e = entry(raw([10, 11], { torque: [8, 9] }), { columns: ['torque'] });
  const [trace] = flightWindowTraces(e, e.window, ['rpm', 'torque'], 'original', false);
  assert.equal(trace.column, 'torque');
  assert.deepEqual(trace.dash, FULL_FLIGHT_VARIABLE_DASHES[1]);
  assert.equal(trace.color, e.source.color);
  assert.match(trace.label, /Flight A · torque · original/);
});

test('envelopes retain min/max identities and never join different flights or processing layers', () => {
  const window = { mode: 'envelope', level: 16, n_raw: 48, i0: 0, i1: 48,
    t: [10, 11], series: { rpm: { min: [2, 3], max: [8, 9] } } };
  const e = entry(window, { columns: ['rpm'] });
  const original = flightWindowTraces(e, window, ['rpm'], 'original', true);
  const filtered = flightWindowTraces(e, window, ['rpm'], 'filtered', false);
  assert.deepEqual(original.map(trace => trace.edge), ['max', 'min']);
  assert.deepEqual(original.map(trace => trace.y), [[8, 9], [2, 3]]);
  assert.equal(original[0].band, original[1].band);
  assert.notEqual(original[0].band, filtered[0].band);
  assert.deepEqual(original[0].dash, filtered[0].dash);
  assert.equal(original[0].subdued, true);
});

test('invalid times remove the corresponding values from every envelope edge', () => {
  const window = { mode: 'envelope', level: 16, n_raw: 64, i0: 0, i1: 64,
    t: [10, null, Infinity, 11], series: { rpm: { min: [1, 100, 200, 2], max: [4, 101, 201, 5] } } };
  const traces = flightWindowTraces(entry(window, { columns: ['rpm'] }), window, ['rpm'], 'original', false);
  assert.deepEqual(traces[0].t, [0, 1]);
  assert.deepEqual(traces.map(trace => trace.y), [[4, 5], [1, 2]]);
});

test('filter alignment requires exact source rows, reduction and timestamps', () => {
  const original = raw();
  assert.equal(windowsAlign(original, structuredClone(original), ['rpm', 'torque']), true);
  for (const patch of [{ i0: 21 }, { i1: 24 }, { level: 2 }, { t: [10, 10.6, 11] },
    { t: [10, 11] }, { series: { rpm: [1, 2, 3] } }]) {
    assert.equal(windowsAlign(original, { ...original, ...patch }, ['rpm', 'torque']), false);
  }
});

test('malformed returned variable arrays are rejected before plotting', () => {
  assert.equal(windowColumnsAlign(raw(), ['rpm', 'torque']), true);
  assert.equal(windowColumnsAlign(raw(), ['missing']), false);
  assert.equal(windowColumnsAlign(raw([10, 11], { rpm: [1] }), ['rpm']), false);
  assert.equal(windowColumnsAlign({ mode: 'envelope', t: [1, 2], series: { rpm: { min: [1], max: [3, 4] } } }, ['rpm']), false);
});

test('original and filtered traces share variable pattern while keeping explicit processing identity', () => {
  const e = entry();
  const original = flightWindowTraces(e, e.window, ['rpm', 'torque'], 'original', true);
  const filtered = flightWindowTraces(e, e.window, ['rpm', 'torque'], 'filtered', false);
  for (let i = 0; i < original.length; i++) {
    assert.deepEqual(original[i].dash, filtered[i].dash);
    assert.equal(original[i].color, filtered[i].color);
    assert.equal(original[i].kind, 'original');
    assert.equal(filtered[i].kind, 'filtered');
    assert.equal(original[i].subdued, true);
    assert.equal(filtered[i].subdued, false);
  }
});

test('all six variable patterns remain distinct and null values retain gaps', () => {
  assert.equal(new Set(FULL_FLIGHT_VARIABLE_DASHES.map(dash => JSON.stringify(dash))).size, 6);
  const e = entry();
  assert.deepEqual(flightWindowTraces(e, e.window, ['rpm'], 'original', false)[0].y, [100, null, 300]);
});
