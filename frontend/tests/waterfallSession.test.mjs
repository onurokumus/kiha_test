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
const { parseSessionFile } =
  loadTs(fileURLToPath(new URL('../src/services/sessionFiles.ts', import.meta.url)));

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
  assert.deepEqual(waterfall(parseSessionFile(JSON.stringify(older)).session), expected);
});

test('legacy window-only sessions retain manual analysis and full frequency band', () => {
  const legacy = defaultAnalysisSession();
  delete legacy.waterfallResolution;
  delete legacy.waterfallBand;
  legacy.waterfallWindow = 8192;
  legacy.waterfallOverlap = 50;
  legacy.specMode = 'waterfall';
  legacy.specSource = 'tp';
  const reopened = parseSessionFile(JSON.stringify(legacy)).session;
  assert.deepEqual(waterfall(reopened), { window: 8192, overlap: 50, resolution: null, band: 'full' });
  assert.equal(reopened.specSource, 'tp');
  delete legacy.waterfallOverlap;
  assert.equal(normalizeAnalysisSession(legacy).waterfallOverlap, 50);
});

test('named session files preserve every supported spacing and manual null', () => {
  for (const resolution of [0.5, 0.25, 0.1, null]) {
    for (const band of ['low', 'full']) {
      const session = { ...defaultAnalysisSession(), waterfallResolution: resolution,
        waterfallBand: band, waterfallWindow: 16384, waterfallOverlap: 0 };
      const file = parseSessionFile(JSON.stringify({ format: 'ptt-analysis-session', version: 1,
        name: 'Fine vibration', savedAt: '2026-09-11T10:00:00Z', session }));
      assert.equal(file.name, 'Fine vibration');
      assert.deepEqual(waterfall(file.session), waterfall(session));
    }
  }
});

test('session import rejects unsupported settings instead of silently changing the analysis', () => {
  for (const resolution of [0, -0.1, 0.01, 0.125, 1, '0.25', true, [], {}]) {
    assert.throws(() => parseSessionFile(JSON.stringify({ ...defaultAnalysisSession(),
      waterfallResolution: resolution })), /Invalid session waterfallResolution/);
  }
  for (const band of [null, '', '0-200', 'LOW', 200, true, [], {}]) {
    assert.throws(() => parseSessionFile(JSON.stringify({ ...defaultAnalysisSession(),
      waterfallBand: band })), /Invalid session waterfallBand/);
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
    const fine = { ...defaultAnalysisSession(), waterfallResolution: 0.1, waterfallBand: 'full' };
    saveAnalysisSession(fine);
    assert.deepEqual(waterfall(loadAnalysisSession()), waterfall(fine));
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
