import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/splitPlotPreferences.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { normalizeSplitColumns, loadSplitColumns, saveSplitColumns } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
);

test('preferences preserve variable order and reject missing, duplicate or invalid columns', () => {
  assert.deepEqual(normalizeSplitColumns(['rpm', 'removed', 1, 'thrust', 'rpm'],
    ['thrust', 'rpm', 'temperature']), ['rpm', 'thrust']);
  assert.deepEqual(normalizeSplitColumns(['old'], ['new', 'other']), ['new']);
  assert.deepEqual(normalizeSplitColumns({ columns: ['rpm'] }, ['thrust', 'rpm']), ['thrust']);
  assert.deepEqual(normalizeSplitColumns(['rpm'], []), []);
  const columns = Array.from({ length: 12 }, (_, index) => `channel ${index}`);
  assert.deepEqual(normalizeSplitColumns(columns, columns), columns.slice(0, 9));
});

test('per-test choices restore independently; malformed or blocked storage falls back safely', () => {
  const storage = new Map();
  globalThis.window = { localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  } };
  try {
    saveSplitColumns('alpha', ['temperature', 'rpm']);
    saveSplitColumns('beta', ['thrust']);
    assert.deepEqual(loadSplitColumns('alpha', ['rpm', 'temperature']), ['temperature', 'rpm']);
    assert.deepEqual(loadSplitColumns('beta', ['rpm', 'thrust']), ['thrust']);
    assert.deepEqual(loadSplitColumns('alpha', ['rpm', 'new']), ['rpm']);
    window.localStorage.getItem = () => '{broken';
    assert.deepEqual(loadSplitColumns('alpha', ['rpm']), ['rpm']);
    window.localStorage.getItem = () => { throw new Error('storage disabled'); };
    window.localStorage.setItem = () => { throw new Error('storage disabled'); };
    assert.deepEqual(loadSplitColumns('alpha', ['rpm']), ['rpm']);
    assert.doesNotThrow(() => saveSplitColumns('alpha', ['rpm']));
  } finally {
    delete globalThis.window;
  }
});
