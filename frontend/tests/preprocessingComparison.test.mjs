import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

async function moduleUrl(path, imports = {}) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, url] of Object.entries(imports)) source = source.replace(`'${specifier}'`, `'${url}'`);
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  });
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`;
}
const apiUrl = `data:text/javascript;base64,${Buffer.from('export const getJson = (path, signal) => globalThis.__comparisonGet(path, signal);').toString('base64')}`;
const rangeUrl = await moduleUrl('../src/utils/timePlotRanges.ts');
const { validatePreprocessingComparison, fetchPreprocessingComparison, constrainComparisonRange, comparisonCursorValues } =
  await import(await moduleUrl('../src/services/preprocessingComparison.ts', { './api': apiUrl, '../utils/timePlotRanges': rangeUrl }));
const source = { id: 'fixture-id', revision: 'revision-one', name: 'A&B flight' };
const fixture = (patch = {}) => ({ source, column: 'Torque, raw [N·m]', mode: 'raw', level: 1, n_raw: 3, i0: 0, i1: 3,
  t: [0, 1, 2], original: [10, null, 20], filtered: [9, 15, 20.00000001], range: { start: 0, end: 3 },
  summary: { finite_pairs: 2, missing_pairs: 1, rms_difference: .707, max_abs_difference: 1 }, gaps: [], warnings: [], ...patch });

test('comparison query protects exact arbitrary names, source revision, time window and abort signal', async () => {
  const controller = new AbortController();
  let captured;
  globalThis.__comparisonGet = async (path, signal) => { captured = { path, signal }; return fixture(); };
  try {
    const result = await fetchPreprocessingComparison(source.name, source, fixture().column, [.125, 1.875], 901.5, controller.signal);
    const url = new URL(captured.path, 'http://fixture');
    assert.equal(url.pathname, '/tests/A%26B%20flight/preprocess/compare');
    assert.equal(url.searchParams.get('column'), fixture().column);
    assert.equal(url.searchParams.get('source_id'), source.id);
    assert.equal(url.searchParams.get('source_revision'), source.revision);
    assert.equal(url.searchParams.get('t0'), '0.125');
    assert.equal(url.searchParams.get('t1'), '1.875');
    assert.equal(url.searchParams.get('px'), '902');
    assert.equal(captured.signal, controller.signal);
    assert.deepEqual(result, fixture());
    await fetchPreprocessingComparison(source.name, source, fixture().column, null, 90000);
    const full = new URL(captured.path, 'http://fixture');
    assert.equal(full.searchParams.has('t0'), false);
    assert.equal(full.searchParams.get('px'), '4000');
  } finally { delete globalThis.__comparisonGet; }
});

test('guarded comparison rejects stale identity/revision, mismatched grids and nonfinite data', () => {
  for (const patch of [
    { source: { ...source, revision: 'other-revision' } }, { source: { ...source, id: 'other-id' } },
    { source: { ...source, name: 'renamed' } }, { column: 'other-column' },
    { filtered: [1, 2] }, { original: [1, Infinity, 3] }, { t: [0, null, 2] }, { t: [0, 0, 2] },
    { range: { start: 2, end: 2 } }, { mode: 'envelope', original: { min: [1, 2, 3], max: [4] } },
    { level: 16 }, { n_raw: 4 }, { i1: 4 }, { i0: -1 }, { n_raw: Infinity },
    { summary: null }, { summary: { finite_pairs: 2, missing_pairs: 0, rms_difference: 1, max_abs_difference: 2 } },
    { summary: { finite_pairs: 2, missing_pairs: 1, rms_difference: NaN, max_abs_difference: 2 } },
  ]) assert.throws(() => validatePreprocessingComparison(fixture(patch), source, fixture().column), /does not match/);
  assert.equal(validatePreprocessingComparison(fixture(), source, fixture().column).original[1], null);
});

test('native cursor differences preserve tiny changes and missing samples are never treated as zero', () => {
  assert.equal(comparisonCursorValues(fixture(), 0).difference, -1);
  assert.equal(comparisonCursorValues(fixture(), 1).difference, null);
  assert.deepEqual(comparisonCursorValues(fixture(), 1).original, [null]);
  assert.equal(comparisonCursorValues(fixture(), 2).difference, 20.00000001 - 20);
  assert.equal(comparisonCursorValues(fixture(), null), null);
  assert.equal(comparisonCursorValues(fixture(), 3), null);
  const overflow = fixture({ original: [-1e308, null, 2], filtered: [1e308, null, 2],
    summary: { finite_pairs: 2, missing_pairs: 1, rms_difference: null, max_abs_difference: null } });
  validatePreprocessingComparison(overflow, source, overflow.column);
  assert.equal(comparisonCursorValues(overflow, 0).difference, null);
});

test('independent envelope extrema produce ranges, never fabricated paired differences', () => {
  const data = fixture({ mode: 'envelope', level: 16, summary: null,
    original: { min: [1, null, 3], max: [7, null, 9] },
    filtered: { min: [2, null, 4], max: [6, null, 8] } });
  validatePreprocessingComparison(data, source, data.column);
  assert.deepEqual(comparisonCursorValues(data, 0), { time: 0, original: [1, 7], filtered: [2, 6], difference: null });
  assert.deepEqual(comparisonCursorValues(data, 1).filtered, [null, null]);
  assert.throws(() => validatePreprocessingComparison({ ...data,
    original: { min: [8, null, 3], max: [7, null, 9] } }, source, data.column), /does not match/);
  assert.throws(() => validatePreprocessingComparison({ ...data,
    original: { min: [1, null, 3], max: [7, 0, 9] } }, source, data.column), /does not match/);
  assert.throws(() => validatePreprocessingComparison({ ...data, summary: fixture().summary }, source, data.column), /does not match/);
});

test('comparison navigation keeps pan spans within source bounds and prevents degenerate scales', () => {
  assert.deepEqual(constrainComparisonRange([-2, 2], [0, 10]), [0, 4]);
  assert.deepEqual(constrainComparisonRange([8, 12], [0, 10]), [6, 10]);
  assert.deepEqual(constrainComparisonRange([-20, 20], [0, 10]), [0, 10]);
  assert.deepEqual(constrainComparisonRange([4, 6], [0, 10]), [4, 6]);
  for (const invalid of [[1, 1], [2, 1], [NaN, 3], [1e9, 1e9 + .01]])
    assert.equal(constrainComparisonRange(invalid, [0, 2e9]), null);
});
