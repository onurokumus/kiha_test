import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

// Execute the actual persistence entrypoints and their local TypeScript imports.
const nodeRequire = createRequire(import.meta.url);
const modules = new Map();
function loadTs(path) {
  if (modules.has(path)) return modules.get(path).exports;
  const module = { exports: {} };
  modules.set(path, module);
  const { outputText } = ts.transpileModule(readFileSync(path, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS },
  });
  const require = specifier => specifier.startsWith('.')
    ? loadTs(resolve(dirname(path), `${specifier}.ts`)) : nodeRequire(specifier);
  new Function('require', 'module', 'exports', outputText)(require, module, module.exports);
  return module.exports;
}

const { defaultAnalysisSession, normalizeAnalysisSession, loadAnalysisSession, saveAnalysisSession } =
  loadTs(fileURLToPath(new URL('../src/services/analysisSession.ts', import.meta.url)));
const roundTrip = value => normalizeAnalysisSession(JSON.parse(JSON.stringify(value)));
const { resolveSessionSources } =
  loadTs(fileURLToPath(new URL('../src/services/sessionSources.ts', import.meta.url)));
const { validWaterfallColorRange, waterfallColorTicks, normalizeWaterfallColorRanges, changeWaterfallColorRange } =
  loadTs(fileURLToPath(new URL('../src/utils/waterfallColorRange.ts', import.meta.url)));

const waterfall = session => ({
  window: session.waterfallWindow,
  overlap: session.waterfallOverlap,
  resolution: session.waterfallResolution,
  band: session.waterfallBand,
});

test('new workspaces and sessions predating Waterfall use the fine low-band preset', () => {
  const expected = { window: 1024, overlap: 75, resolution: 0.25, band: 'low' };
  assert.deepEqual(waterfall(defaultAnalysisSession()), expected);
  assert.deepEqual(waterfall(normalizeAnalysisSession(null)), expected);
  assert.deepEqual(waterfall(normalizeAnalysisSession({ version: 1 })), expected);
  const older = defaultAnalysisSession();
  delete older.waterfallWindow;
  delete older.waterfallOverlap;
  delete older.waterfallResolution;
  delete older.waterfallBand;
  assert.deepEqual(waterfall(roundTrip(older)), expected);
});

test('legacy window-only sessions retain manual analysis and full frequency band', () => {
  const legacy = defaultAnalysisSession();
  delete legacy.waterfallResolution;
  delete legacy.waterfallBand;
  legacy.waterfallWindow = 8192;
  legacy.waterfallOverlap = 50;
  legacy.specMode = 'waterfall';
  legacy.specSource = 'tp';
  const reopened = roundTrip(legacy);
  assert.deepEqual(waterfall(reopened), { window: 8192, overlap: 50, resolution: null, band: 'full' });
  assert.equal(reopened.specSource, 'tp');
  delete legacy.waterfallOverlap;
  assert.equal(normalizeAnalysisSession(legacy).waterfallOverlap, 50);
});

test('browser restoration preserves every supported spacing and manual null', () => {
  for (const resolution of [0.5, 0.25, 0.1, null]) {
    for (const band of ['low', 'full']) {
      const session = { ...defaultAnalysisSession(), waterfallResolution: resolution,
        waterfallBand: band, waterfallWindow: 16384, waterfallOverlap: 0 };
      assert.deepEqual(waterfall(roundTrip(session)), waterfall(session));
    }
  }
});

test('browser autosave and reload retain fine settings and recover legacy settings once', () => {
  const storage = new Map();
  globalThis.window = { localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  } };
  try {
    assert.equal(loadAnalysisSession().waterfallResolution, 0.25);
    const fine = { ...defaultAnalysisSession(), waterfallResolution: 0.1, waterfallBand: 'full',
      waterfallColorRanges: normalizeWaterfallColorRanges([
        { column: 'vibration', linear: [0, 0.2], log: [-8, -1] },
      ]) };
    saveAnalysisSession(fine);
    assert.deepEqual(waterfall(loadAnalysisSession()), waterfall(fine));
    assert.deepEqual(loadAnalysisSession().waterfallColorRanges, fine.waterfallColorRanges);
    const legacy = { ...fine, waterfallWindow: 4096, waterfallOverlap: 25 };
    delete legacy.waterfallResolution;
    delete legacy.waterfallBand;
    saveAnalysisSession(legacy);
    const recovered = loadAnalysisSession();
    assert.deepEqual(waterfall(recovered), { window: 4096, overlap: 25, resolution: null, band: 'full' });
    saveAnalysisSession(recovered);
    assert.deepEqual(waterfall(loadAnalysisSession()), waterfall(recovered));
  } finally {
    delete globalThis.window;
  }
});

test('color limits validate exact finite units without rejecting small amplitudes', () => {
  for (const bounds of [[0, 1], [0, Number.MIN_VALUE], [1e-20, 2e-20], [0, Number.MAX_VALUE]]) {
    assert.equal(validWaterfallColorRange(bounds, false), true);
    assert.equal(validWaterfallColorRange(bounds, true), true);
  }
  assert.equal(validWaterfallColorRange([-9, -2], true), true);
  assert.equal(validWaterfallColorRange([-1, 0], false), false);
  for (const bounds of [null, {}, [], [0], [0, 1, 2], ['0', 1], [0, '1'],
    [null, 1], [0, true], [NaN, 1], [0, Infinity], [1, 1], [2, 1],
    [-Number.MAX_VALUE, Number.MAX_VALUE]]) {
    assert.equal(validWaterfallColorRange(bounds, false), false);
    assert.equal(validWaterfallColorRange(bounds, true), false);
  }
});

test('color ticks increase precision for narrow ranges without inventing adjacent-double midpoints', () => {
  assert.deepEqual(waterfallColorTicks([1, 1.00001]), [
    { fraction: 0, label: '1' }, { fraction: 0.5, label: '1.000005' },
    { fraction: 1, label: '1.00001' },
  ]);
  assert.deepEqual(waterfallColorTicks([1, 1 + Number.EPSILON]), [
    { fraction: 0, label: '1' }, { fraction: 1, label: '1.0000000000000002' },
  ]);
  assert.deepEqual(waterfallColorTicks([-1 - Number.EPSILON, -1]), [
    { fraction: 0, label: '-1.0000000000000002' }, { fraction: 1, label: '-1' },
  ]);
});

test('color ticks keep ordinary and tiny ranges readable and reject non-finite widths', () => {
  assert.deepEqual(waterfallColorTicks([0, 1]), [
    { fraction: 0, label: '0' }, { fraction: 0.5, label: '0.5' }, { fraction: 1, label: '1' },
  ]);
  assert.deepEqual(waterfallColorTicks([-8, -2]), [
    { fraction: 0, label: '-8' }, { fraction: 0.5, label: '-5' }, { fraction: 1, label: '-2' },
  ]);
  assert.deepEqual(waterfallColorTicks([1e-20, 2e-20]), [
    { fraction: 0, label: '1e-20' }, { fraction: 0.5, label: '1.5e-20' },
    { fraction: 1, label: '2e-20' },
  ]);
  assert.deepEqual(waterfallColorTicks([0, Number.MIN_VALUE]), [
    { fraction: 0, label: '0' }, { fraction: 1, label: '5e-324' },
  ]);
  const largeTicks = waterfallColorTicks([1e308, 1.2e308]);
  assert.equal(largeTicks.length, 3);
  assert.deepEqual(largeTicks.map(tick => tick.label), ['1e+308', '1.1e+308', '1.2e+308']);
  for (const range of [[0, Number.MAX_VALUE], [-Number.MAX_VALUE, -1e308]]) {
    const ticks = waterfallColorTicks(range);
    assert.equal(ticks.length, 3);
    assert.ok(ticks.every(tick => Number.isFinite(Number(tick.label))));
    assert.equal(new Set(ticks.map(tick => tick.label)).size, 3);
    assert.equal(ticks[range[0] === 0 ? 2 : 0].label,
      String(range[0] === 0 ? Number.MAX_VALUE : -Number.MAX_VALUE));
  }
  for (const range of [[0, Infinity], [1, 1], [-Number.MAX_VALUE, Number.MAX_VALUE]]) {
    assert.deepEqual(waterfallColorTicks(range), []);
  }
});

test('color range recovery preserves nine independent slots and discards malformed entries', () => {
  const valid = { column: 'vibration', linear: [0, 0.5], log: [-7, -1] };
  const recovered = normalizeWaterfallColorRanges([
    valid, { ...valid, linear: [-1, 1] }, { ...valid, log: null },
    { ...valid, log: [1, 1] }, { ...valid, column: 3 }, null,
    { column: 'vibration', linear: null, log: null }, valid, valid, valid,
  ]);
  assert.equal(recovered.length, 9);
  assert.deepEqual(recovered.slice(0, 7), [valid, null, { ...valid, log: null }, null, null,
    null, { column: 'vibration', linear: null, log: null }]);
  assert.notEqual(recovered[0], valid);
  assert.notEqual(recovered[0].linear, valid.linear);
  for (const input of [undefined, null, 'bad', {}]) {
    assert.deepEqual(normalizeWaterfallColorRanges(input), Array(9).fill(null));
  }
});

test('changing one color mode preserves the other and prevents cross-variable limits', () => {
  const saved = { column: 'vibration', linear: [0, 0.5], log: [-7, -1] };
  const changed = changeWaterfallColorRange(saved, 'vibration', true, [-6, -2]);
  assert.deepEqual(changed, { ...saved, log: [-6, -2] });
  assert.deepEqual(changeWaterfallColorRange(changed, 'vibration', true, null), {
    ...saved, log: null,
  });
  assert.deepEqual(changeWaterfallColorRange(saved, 'temperature', false, [0, 30]), {
    column: 'temperature', linear: [0, 30], log: null,
  });
  assert.deepEqual(changeWaterfallColorRange(null, 'temperature', true, [-4, 1]), {
    column: 'temperature', linear: null, log: [-4, 1],
  });
  assert.deepEqual(saved, { column: 'vibration', linear: [0, 0.5], log: [-7, -1] });
});

test('new and legacy browser state uses Auto; reload retains per-slot linear and log limits', () => {
  const empty = Array(9).fill(null);
  assert.deepEqual(defaultAnalysisSession().waterfallColorRanges, empty);
  const legacy = defaultAnalysisSession();
  delete legacy.waterfallColorRanges;
  assert.deepEqual(normalizeAnalysisSession(legacy).waterfallColorRanges, empty);
  assert.deepEqual(roundTrip(legacy).waterfallColorRanges, empty);
  const ranges = normalizeWaterfallColorRanges([
    { column: 'vibration', linear: [0, 0.5], log: [-7, -1] },
    { column: 'vibration', linear: [0.1, 0.2], log: null },
    { column: 'temperature', linear: null, log: [-2, 3] },
  ]);
  const session = { ...legacy, plotConfigs: ['vibration', 'vibration', 'temperature'],
    waterfallColorRanges: ranges, specLogY: true };
  const restored = roundTrip(session);
  assert.deepEqual(restored.waterfallColorRanges, ranges);
  assert.equal(restored.specLogY, true);
});

test('browser restoration clears malformed color limits while keeping valid slots', () => {
  const valid = { column: 'vibration', linear: [0, 0.5], log: [-7, -1] };
  const invalid = [false, [], {}, { ...valid, column: '' }, { ...valid, column: 2 },
    { column: 'vibration', linear: [0, 1] }, { ...valid, linear: [-1, 2] },
    { ...valid, linear: ['0', 1] }, { ...valid, linear: [0, null] },
    { ...valid, log: [0, 0] }, { ...valid, log: [2, 1] },
    { ...valid, log: [-Number.MAX_VALUE, Number.MAX_VALUE] }];
  for (const entry of invalid) {
    const session = { ...defaultAnalysisSession(), waterfallColorRanges: [valid, entry] };
    assert.deepEqual(normalizeAnalysisSession(session).waterfallColorRanges.slice(0, 2), [valid, null]);
  }
  for (const ranges of [null, {}, 'bad', Array(10).fill(null)]) {
    const restored = roundTrip({ ...defaultAnalysisSession(), waterfallColorRanges: ranges });
    assert.equal(restored.waterfallColorRanges.length, 9);
    assert.deepEqual(restored.waterfallColorRanges, Array(9).fill(null));
  }
});

test('source recovery retains manual color limits across rename, changed data and unavailable variables', () => {
  const ranges = normalizeWaterfallColorRanges([
    { column: 'vibration', linear: [0, 0.5], log: [-7, -1] },
  ]);
  const saved = { ...defaultAnalysisSession(), currentTest: 'original', plotConfigs: ['vibration'],
    waterfallColorRanges: ranges, sources: [{ name: 'original', id: 'same-id',
      revision: 'before', test_points: [] }] };
  const recovery = resolveSessionSources(saved, [{ name: 'renamed', id: 'same-id',
    revision: 'after', test_points: [], status: 'ready', columns: ['temperature'] }]);
  assert.equal(recovery.currentTest, 'renamed');
  assert.deepEqual(recovery.waterfallColorRanges, ranges);
  assert.deepEqual(recovery.plotConfigs, ['vibration']);
  assert.equal(recovery.plotsUserEdited, true);
});
