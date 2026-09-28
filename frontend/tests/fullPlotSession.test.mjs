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
const { parseSessionFile } =
  loadTs(fileURLToPath(new URL('../src/services/sessionFiles.ts', import.meta.url)));
const { resolveSessionSources } =
  loadTs(fileURLToPath(new URL('../src/services/sessionSources.ts', import.meta.url)));
const emptySlots = () => Array.from({ length: 9 }, () => []);

test('legacy time-note visibility is ignored when reopening and resaving sessions', () => {
  for (const annotationsVisible of [true, false]) {
    const current = { ...defaultAnalysisSession(), currentTest: 'legacy-test',
      viewMode: 'full', plotConfigs: ['rpm'], timeZoom: [1, 4] };
    const legacy = { ...current, annotationsVisible };
    const restored = normalizeAnalysisSession(legacy);
    const imported = parseSessionFile(JSON.stringify({ format: 'ptt-analysis-session', version: 1,
      name: 'Legacy notes', savedAt: '2026-09-28T10:00:00Z', session: legacy })).session;
    assert.deepEqual(restored, normalizeAnalysisSession(current));
    assert.deepEqual(imported, normalizeAnalysisSession(current));
    assert.equal('annotationsVisible' in restored, false);
    assert.equal(JSON.stringify(imported).includes('annotationsVisible'), false);
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
  assert.deepEqual(parseSessionFile(JSON.stringify(session)).session.fullPlotExtraColumns, emptySlots());
});

test('named session reopening preserves nine positional comparisons, filters and overlays', () => {
  const session = defaultAnalysisSession();
  session.viewMode = 'full';
  session.plotConfigs = Array(9).fill('rpm');
  session.fullPlotExtraColumns = Array.from({ length: 9 }, (_, index) =>
    [`thrust ${index}`, 'Temperature / C', 'Torque', 'power', 'vibration']);
  session.plotFilters[3] = { ...session.plotFilters[3], kind: 'despike' };
  session.plotShowOriginal[3] = true;
  const reopened = parseSessionFile(JSON.stringify({ format: 'ptt-analysis-session', version: 1,
    name: 'Full test comparisons', savedAt: '2026-09-16T10:00:00Z', session })).session;
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

test('explicit session files reject malformed, duplicate, primary and orphan comparisons', () => {
  const base = { ...defaultAnalysisSession(), plotConfigs: ['rpm', 'thrust'] };
  const invalid = [null, {}, 'rpm', Array(10).fill([]), [null], ['rpm'], [[1]], [['']],
    [['rpm']], [['Torque', 'Torque']], [['a', 'b', 'c', 'd', 'e', 'f']],
    [[], [], ['orphan']], [[], ['thrust']]];
  for (const fullPlotExtraColumns of invalid) {
    assert.throws(() => parseSessionFile(JSON.stringify({ ...base, fullPlotExtraColumns })),
      /Invalid session fullPlotExtraColumns/);
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
    assert.equal(recovery.session.currentTest, 'renamed');
    assert.equal(recovery.session.plotsUserEdited, true);
    assert.deepEqual(recovery.session.plotConfigs, saved.plotConfigs);
    assert.deepEqual(recovery.session.fullPlotExtraColumns, extras);
  }
});


test('full-test Y and spectrum Y crops survive session files while older sessions remain valid', () => {
  const session = defaultAnalysisSession();
  assert.deepEqual(session.plotViewports.full, Array(9).fill(null));
  session.plotViewports.full[2] = { context: 'full-source-and-columns', x: [2, 8], y: [-0.2, 0.7] };
  session.plotViewports.spectrum[0] = { context: 'fft-source', x: [20, 100], y: [-6, -2] };
  const reopened = parseSessionFile(JSON.stringify(session)).session;
  assert.deepEqual(reopened.plotViewports, session.plotViewports);
  const legacy = defaultAnalysisSession();
  delete legacy.plotViewports.full;
  assert.deepEqual(parseSessionFile(JSON.stringify(legacy)).session.plotViewports.full, Array(9).fill(null));
});

test('explicit full-test ranges reject malformed or reversed Y bounds', () => {
  for (const full of [{}, 'bad', Array(10).fill(null), [{context:'full', x:[0,1], y:[4,2]}]]) {
    const session = defaultAnalysisSession();
    session.plotViewports.full = full;
    assert.throws(() => parseSessionFile(JSON.stringify(session)), /Invalid session full ranges/);
  }
});
