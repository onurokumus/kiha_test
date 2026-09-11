import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

// Compile only these pure helpers with the project's existing TypeScript.
// No DOM shim, generated files, or additional test runtime is required.
const moduleUrl = async (path, imports = {}) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, url] of Object.entries(imports))
    source = source.replace(`'${specifier}'`, `'${url}'`);
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  });
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`;
};
const rangesUrl = await moduleUrl('../src/utils/timePlotRanges.ts');
const { validAxisRange } = await import(rangesUrl);
const { visibleLineExtents, paddedVisibleRange, visibleYRange, visibleYAutoFitPlugin } = await import(
  await moduleUrl('../src/utils/visibleYRange.ts', { './timePlotRanges': rangesUrl })
);

test('offscreen peaks do not set the visible Y range', () => {
  assert.deepEqual(visibleLineExtents([
    { x: [0, 1, 2, 3], y: [1000, 10, 20, 9000] },
  ], [1, 2]), [10, 20]);
});

test('a visible line between sparse samples fits its clipped endpoints', () => {
  assert.deepEqual(visibleLineExtents([
    { x: [0, 10], y: [100, 200] },
  ], [2, 4]), [120, 140]);
});

test('missing samples break boundary interpolation', () => {
  for (const missing of [null, undefined, NaN, Infinity, -Infinity]) {
    assert.equal(visibleLineExtents([
      { x: [0, 5, 10], y: [100, missing, 200] },
    ], [2, 4]), null);
  }
});

test('independent TP grids and envelope edges contribute their own samples', () => {
  assert.deepEqual(visibleLineExtents([
    { x: [0, 1, 2, 3], y: [1000, 5, 6, 1000] },
    { x: [0, 1.5, 2.5], y: [1, -4, -2] },
  ], [1, 2]), [-4, 6]);
});

test('empty windows, all-missing data and invalid times have no extents', () => {
  assert.equal(visibleLineExtents([{ x: [0, 1], y: [5, 6] }], [2, 3]), null);
  assert.equal(visibleLineExtents([{ x: [0, 1], y: [null, NaN] }], null), null);
  assert.equal(visibleLineExtents([{ x: [null, Infinity], y: [5, 6] }], null), null);
});

test('padding handles ordinary, zero, constant, tiny and near-flat signals', () => {
  assert.deepEqual(paddedVisibleRange([10, 20]), [9, 21]);
  assert.deepEqual(paddedVisibleRange([0, 0]), [-1, 1]);
  assert.deepEqual(paddedVisibleRange([1500, 1500]), [1498.5, 1501.5]);
  for (const extents of [[1e-12, 2e-12], [1e9, 1e9 + .01], [-1e-12, -1e-12]]) {
    const range = paddedVisibleRange(extents);
    assert.ok(validAxisRange(range), `${extents}: ${range}`);
    assert.ok(range[0] < extents[0] && range[1] > extents[1]);
  }
});

test('faceted and aligned plots fit only enabled series and preserve empty-window Y', () => {
  const series = [{}, { show: true }, { show: false }];
  const x = [0, 1, 2, 3], y = [1000, 10, 20, 9000], hidden = [9000, 9000, 9000, 9000];
  const aligned = { series, data: [x, y, hidden], scales: { x: { min: 1, max: 2 }, y: { min: 0, max: 100 } } };
  const faceted = { ...aligned, series: series.map((s, i) => i ? { ...s, facets: [{}, {}] } : s), data: [null, [x, y], [x, hidden]] };
  assert.deepEqual(visibleYRange(aligned, 10, 9000), [9, 21]);
  assert.deepEqual(visibleYRange(faceted, 10, 9000), [9, 21]);
  aligned.scales.x = { min: 4, max: 5 };
  assert.deepEqual(visibleYRange(aligned, null, null), [0, 100]);
});

test('auto-refit coalesces X updates and respects manual overrides and destruction', async () => {
  let manual = false;
  const calls = [];
  const plot = { setScale: (...args) => calls.push(args) };
  const plugin = visibleYAutoFitPlugin(() => manual);
  plugin.hooks.setScale(plot, 'y');
  plugin.hooks.setScale(plot, 'x');
  plugin.hooks.setScale(plot, 'x');
  await Promise.resolve();
  assert.deepEqual(calls, [['y', { min: null, max: null }]]);
  plugin.hooks.setScale(plot, 'x');
  manual = true;
  await Promise.resolve();
  assert.equal(calls.length, 1);
  manual = false;
  plugin.hooks.setScale(plot, 'x');
  plugin.hooks.destroy();
  await Promise.resolve();
  assert.equal(calls.length, 1);
});
