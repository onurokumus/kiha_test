import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

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

const { defaultAnalysisSession, normalizeAnalysisSession, normalizeFullPlotExtraColumns,
  loadAnalysisSession, saveAnalysisSession } =
  loadTs(fileURLToPath(new URL('../src/services/analysisSession.ts', import.meta.url)));
const roundTrip = value => normalizeAnalysisSession(JSON.parse(JSON.stringify(value)));
const { resolveSessionSources } =
  loadTs(fileURLToPath(new URL('../src/services/sessionSources.ts', import.meta.url)));
const emptySlots = () => Array.from({ length: 9 }, () => []);

test('full-flight comparison restores visibility, alignment, colors and independent time crop', () => {
  const session = { ...defaultAnalysisSession(), currentTest: 'A', fullRange: [100, 110],
    fullFlightRange: [2, 7], fullFlightComparison: { timeBasis: 'elapsed', flights: [
      { test: 'A', hidden: false, color: '#d55e00', offset: 0 },
      { test: 'B', hidden: true, color: '#21834a', offset: -12.5 },
    ] } };
  const normalized = roundTrip(session);
  assert.deepEqual(normalized.fullFlightComparison, session.fullFlightComparison);
  assert.deepEqual(normalized.fullFlightRange, [2, 7]);
  assert.deepEqual(normalized.fullRange, [100, 110]);
  const { fullFlightComparison, fullFlightRange, ...legacy } = session;
  assert.equal(normalizeAnalysisSession(legacy).fullFlightComparison, null);
  assert.equal(normalizeAnalysisSession(legacy).fullFlightRange, null);
});

test('flight recovery follows durable identities through rename and skips same-name replacements', () => {
  const saved = { ...defaultAnalysisSession(), currentTest: 'A', fullFlightRange: [2, 7],
    fullFlightComparison: { timeBasis: 'elapsed', flights: [
      { test: 'A', hidden: false, color: '#d55e00', offset: 1 },
      { test: 'B', hidden: true, color: '#21834a', offset: -5 },
    ] }, sources: [
      { name: 'A', id: 'a-id', revision: 'r1', test_points: [] },
      { name: 'B', id: 'b-id', revision: 'r2', test_points: [] },
    ] };
  const catalog = [
    { name: 'Renamed A', id: 'a-id', revision: 'r1', status: 'ready', test_points: [] },
    { name: 'B', id: 'b-id', revision: 'r2', status: 'ready', test_points: [] },
  ];
  const renamed = resolveSessionSources(saved, catalog);
  assert.equal(renamed.fullFlightComparison.flights[0].test, 'Renamed A');
  assert.equal(renamed.fullFlightComparison.flights[0].offset, 1);
  assert.deepEqual(renamed.fullFlightRange, [2, 7]);
  assert.equal(renamed.fullFlightComparison.flights[1].hidden, true);
  const replaced = resolveSessionSources(saved, [catalog[0], { ...catalog[1], id: 'replacement-id' }]);
  assert.equal(replaced.fullFlightComparison.flights.length, 1);
  assert.equal(replaced.fullFlightRange, null);
  const changed = resolveSessionSources(saved, [catalog[0], { ...catalog[1], revision: 'changed' }]);
  assert.equal(changed.fullFlightComparison.flights[1].offset, 0);
  assert.equal(changed.fullFlightRange, null);
  const legacy = resolveSessionSources({ ...saved, sources: undefined }, catalog);
  assert.deepEqual(legacy.fullFlightComparison.flights, []);
});

test('flight references include hidden sources even when the active test differs', () => {
  const { captureSessionSources } = loadTs(fileURLToPath(new URL('../src/services/sessionSources.ts', import.meta.url)));
  const session = { ...defaultAnalysisSession(), currentTest: 'C', fullFlightComparison: { timeBasis: 'stored',
    flights: [{ test: 'A', hidden: true, color: '#d55e00', offset: 0 }] } };
  const saved = captureSessionSources(session, ['A', 'C'].map(name => ({ name, id: name + '-id', status: 'ready', test_points: [] })));
  assert.deepEqual(saved.map(source => source.name), ['C', 'A']);
});

test('legacy time-note visibility is ignored when restoring browser state', () => {
  for (const annotationsVisible of [true, false]) {
    const current = { ...defaultAnalysisSession(), currentTest: 'legacy-test',
      viewMode: 'full', plotConfigs: ['rpm'], timeZoom: [1, 4] };
    const legacy = { ...current, annotationsVisible };
    const restored = normalizeAnalysisSession(legacy);
    assert.deepEqual(restored, normalizeAnalysisSession(current));
    assert.equal('annotationsVisible' in restored, false);
    assert.equal(JSON.stringify(roundTrip(legacy)).includes('annotationsVisible'), false);
  }
});

test('new and legacy sessions have nine independent empty comparison slots', () => {
  const session = defaultAnalysisSession();
  assert.deepEqual(session.fullPlotExtraColumns, emptySlots());
  session.fullPlotExtraColumns[0].push('rpm');
  assert.deepEqual(session.fullPlotExtraColumns[1], []);
  assert.deepEqual(defaultAnalysisSession().fullPlotExtraColumns, emptySlots());
  delete session.fullPlotExtraColumns;
  assert.deepEqual(normalizeAnalysisSession(session).fullPlotExtraColumns, emptySlots());
  assert.deepEqual(roundTrip(session).fullPlotExtraColumns, emptySlots());
});

test('browser restoration preserves nine positional comparisons, filters and overlays', () => {
  const session = defaultAnalysisSession();
  session.viewMode = 'full';
  session.plotConfigs = Array(9).fill('rpm');
  session.fullPlotExtraColumns = Array.from({ length: 9 }, (_, index) =>
    [`thrust ${index}`, 'Temperature / C', 'Torque', 'power', 'vibration']);
  session.plotFilters[3] = { ...session.plotFilters[3], kind: 'despike' };
  session.plotShowOriginal[3] = true;
  const reopened = roundTrip(session);
  assert.deepEqual(reopened.fullPlotExtraColumns, session.fullPlotExtraColumns);
  assert.deepEqual(reopened.plotFilters, session.plotFilters);
  assert.deepEqual(reopened.plotShowOriginal, session.plotShowOriginal);
});

test('browser recovery sanitizes comparisons without shifting slots or variable identities', () => {
  const slots = normalizeFullPlotExtraColumns([
    ['rpm', '', null, 'thrust', 'thrust', 3, 'Torque', 't', 'p', 'q', 'overflow'],
    null,
    [' Temperature / C ', 'temperature / c', 'rpm'],
    ['orphan'],
  ], ['rpm', 'thrust', 'thrust']);
  assert.deepEqual(slots, [
    ['thrust', 'Torque', 't', 'p', 'q'], [], [' Temperature / C ', 'temperature / c', 'rpm'],
    [], [], [], [], [], [],
  ]);
  for (const value of [undefined, null, true, 'rpm', {}]) {
    assert.deepEqual(normalizeFullPlotExtraColumns(value, ['rpm']), emptySlots());
  }
});

test('autosave and reload retain comparison settings independently of the active mode', () => {
  const storage = new Map();
  globalThis.window = { localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  } };
  try {
    const session = { ...defaultAnalysisSession(), viewMode: 'tp', plotConfigs: ['rpm', 'rpm'],
      fullPlotExtraColumns: [['thrust', 'Torque'], ['Temperature / C'], ...emptySlots().slice(2)] };
    saveAnalysisSession(session);
    const reopened = loadAnalysisSession();
    assert.equal(reopened.viewMode, 'tp');
    assert.deepEqual(reopened.fullPlotExtraColumns, session.fullPlotExtraColumns);
  } finally {
    delete globalThis.window;
  }
});

test('source recovery preserves comparison slots while metadata is unavailable or renamed', () => {
  const extras = [['thrust', 'Torque'], ['rpm'], ...emptySlots().slice(2)];
  const saved = { ...defaultAnalysisSession(), viewMode: 'full', currentTest: 'original',
    plotConfigs: ['rpm', 'Temperature / C'], fullPlotExtraColumns: extras,
    sources: [{ name: 'original', id: 'same-id', revision: 'before', test_points: [] }] };
  for (const source of [
    { name: 'renamed', id: 'same-id', revision: 'before', test_points: [], status: 'rebuilding' },
    { name: 'renamed', id: 'same-id', revision: 'after', test_points: [], status: 'ready', columns: ['rpm'] },
  ]) {
    const recovery = resolveSessionSources(saved, [source]);
    assert.equal(recovery.currentTest, 'renamed');
    assert.equal(recovery.plotsUserEdited, true);
    assert.deepEqual(recovery.plotConfigs, saved.plotConfigs);
    assert.deepEqual(recovery.fullPlotExtraColumns, extras);
  }
});


test('full-test Y and spectrum Y crops survive reload while older browser state remains valid', () => {
  const session = defaultAnalysisSession();
  assert.deepEqual(session.plotViewports.full, Array(9).fill(null));
  session.plotViewports.full[2] = { context: 'full-source-and-columns', x: [2, 8], y: [-0.2, 0.7] };
  session.plotViewports.spectrum[0] = { context: 'fft-source', x: [20, 100], y: [-6, -2] };
  const reopened = roundTrip(session);
  assert.deepEqual(reopened.plotViewports, session.plotViewports);
  const legacy = defaultAnalysisSession();
  delete legacy.plotViewports.full;
  assert.deepEqual(roundTrip(legacy).plotViewports.full, Array(9).fill(null));
});

test('browser restoration clears malformed or reversed full-test Y bounds', () => {
  for (const full of [{}, 'bad', Array(10).fill(null), [{context:'full', x:[0,1], y:[4,2]}]]) {
    const session = defaultAnalysisSession();
    session.plotViewports.full = full;
    assert.deepEqual(roundTrip(session).plotViewports.full, Array(9).fill(null));
  }
});
