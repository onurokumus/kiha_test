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
const numbersUrl = await moduleUrl('../src/utils/numericField.ts');
const filtersUrl = await moduleUrl('../src/constants/filters.ts', { '../utils/numericField': numbersUrl });
const apiUrl = `data:text/javascript;base64,${Buffer.from('export const API_BASE = "/api"; export const getJson = async (path) => ({path});').toString('base64')}`;
const { DEFAULT_FILTER_UI, buildFilterSpec } = await import(filtersUrl);
const { describePreprocessingFilter, preprocessingFilterError, preprocessingFilterUi, preprocessingNameError,
  serializePreprocessingFilter, suggestPreprocessingName, matchesPreprocessingRequest,
  createPreprocessedTest, fetchPreprocessing } = await import(await moduleUrl('../src/services/preprocessing.ts', {
  '../constants/filters': filtersUrl, './api': apiUrl,
}));
const ui = (kind, patch = {}) => ({ ...DEFAULT_FILTER_UI, kind, ...patch });

test('filtered copy names preserve the source identity and avoid case-insensitive collisions', () => {
  assert.equal(suggestPreprocessingName('flight', ['flight_filtered', 'FLIGHT_FILTERED_2']), 'flight_filtered_3');
  assert.equal(suggestPreprocessingName('legacy file', []), 'legacy_file_filtered');
  assert.ok(suggestPreprocessingName('x'.repeat(200), []).length <= 200);
  for (const value of ['', '..', 'bad/name', 'name.', 'CON', 'LPT1.csv', 'x'.repeat(201)])
    assert.notEqual(preprocessingNameError(value, []), '');
  assert.notEqual(preprocessingNameError('Flight', ['flight']), '');
  assert.equal(preprocessingNameError('flight_filtered', ['flight']), '');
});

test('recipe serialization excludes plot state and stale settings from previous filter kinds', () => {
  assert.deepEqual(serializePreprocessingFilter({ kind: 'detrend', order: 8, f1: 70, windowS: .1 }), { kind: 'detrend' });
  assert.deepEqual(serializePreprocessingFilter({ kind: 'moving_avg', order: 8, f1: 70, windowS: .025 }), { kind: 'moving_avg', window_s: .025 });
  assert.deepEqual(serializePreprocessingFilter({ kind: 'lowpass', order: 4, f1: 8, f2: 12 }), { kind: 'lowpass', order: 4, f1: 8 });
});

test('all filter kinds survive persisted recipe conversion without changing units', () => {
  const filters = [
    { kind: 'lowpass', order: 4, f1: 8 }, { kind: 'highpass', order: 2, f1: 1 },
    { kind: 'bandpass', order: 3, f1: 2, f2: 8 }, { kind: 'bandstop', order: 5, f1: 3, f2: 9 },
    { kind: 'detrend' }, { kind: 'moving_avg', window_s: .025 },
    { kind: 'despike', window_s: .051, max_spike_s: .01, threshold: 3.5, abs_floor: 2, replacement: 'median' },
  ];
  for (const filter of filters) assert.deepEqual(serializePreprocessingFilter(buildFilterSpec(preprocessingFilterUi(filter))), filter);
  assert.equal(preprocessingFilterUi(filters.at(-1)).despikeWindowMs, '51');
  assert.equal(preprocessingFilterUi(filters.at(-1)).maxSpikeMs, '10');
});

test('native Nyquist checks reject both band edges and the exact limit without clamping', () => {
  for (const kind of ['lowpass', 'highpass', 'bandpass', 'bandstop']) {
    const valid = ui(kind, { f1: '8', f2: '49.999' });
    assert.equal(preprocessingFilterError(valid, 100, 1200), '');
    const atLimit = ui(kind, kind.startsWith('band') ? { f1: '8', f2: '50' } : { f1: '50' });
    assert.match(preprocessingFilterError(atLimit, 100, 1200), /Nyquist/);
    assert.equal(atLimit[kind.startsWith('band') ? 'f2' : 'f1'], '50');
  }
  assert.match(preprocessingFilterError(ui('lowpass', { f1: '8' }), NaN, 1200), /sample rate/);
});

test('empty settings do not become zero and disabled parameters need no valid sample rate', () => {
  assert.equal(preprocessingFilterError(ui(''), NaN, 0), '');
  assert.match(preprocessingFilterError(ui('lowpass'), 100, 1200), /Complete/);
  assert.match(preprocessingFilterError(ui('bandpass', { f1: '10', f2: '8' }), 100, 1200), /Complete/);
  assert.match(preprocessingFilterError(ui('despike', { despikeWindowMs: '20', maxSpikeMs: '10' }), 1000, 1200), /Complete/);
});

test('recording length checks match native Butterworth sections and Python window rounding', () => {
  assert.match(preprocessingFilterError(ui('bandpass', { order: '4', f1: '5', f2: '10' }), 100, 27), /too short/);
  assert.equal(preprocessingFilterError(ui('bandpass', { order: '4', f1: '5', f2: '10' }), 100, 28), '');
  assert.equal(preprocessingFilterError(ui('lowpass', { order: '4', f1: '5' }), 100, 16), '');
  assert.match(preprocessingFilterError(ui('moving_avg', { winS: '.16' }), 100, 16), /shorter/);
  // Python rounds 2.5 samples to 2, so this is a valid 2-sample window in 3 rows.
  assert.equal(preprocessingFilterError(ui('moving_avg', { winS: '.025' }), 100, 3), '');
});

test('despike uses floor for event duration and enforces its native sample budget', () => {
  assert.equal(preprocessingFilterError(ui('despike', { despikeWindowMs: '51', maxSpikeMs: '24' }), 100, 1200), '');
  assert.match(preprocessingFilterError(ui('despike', { despikeWindowMs: '100002', maxSpikeMs: '10' }), 1000, 500000), /100,001/);
  assert.match(preprocessingFilterError(ui('despike', { despikeWindowMs: '51', maxSpikeMs: '10' }), 1000, 50), /shorter/);
});

test('saved recipe descriptions retain settings that affect analysis', () => {
  assert.match(describePreprocessingFilter({ kind: 'bandstop', f1: 48, f2: 52, order: 4 }), /48–52 Hz.*order 4/);
  const label = describePreprocessingFilter({ kind: 'despike', window_s: .05, max_spike_s: .01, threshold: 4, abs_floor: 2, replacement: 'median' });
  for (const fragment of ['50 ms', '10 ms', '4 MAD', 'min jump 2', 'local median']) assert.ok(label.includes(fragment));
});

test('lost-response recovery requires the same source revision, parameters and settings', () => {
  const request = { name: 'filtered', source_id: 'source-id', source_revision: 'revision-1',
    filters: [{ column: 'thrust', filter: { kind: 'lowpass', order: 4, f1: 8 } }] };
  const recipe = { source: { id: 'source-id', revision: 'revision-1', name: 'source' },
    filters: [{ column: 'thrust', filter: { kind: 'lowpass', order: 4, f1: 8, f2: null, window_s: null, abs_floor: 0 } }] };
  assert.equal(matchesPreprocessingRequest(recipe, request), true);
  assert.equal(matchesPreprocessingRequest({ ...recipe, source: { ...recipe.source, id: 'different-source' } }, request), false);
  assert.equal(matchesPreprocessingRequest({ ...recipe, source: { ...recipe.source, revision: 'revision-2' } }, request), false);
  assert.equal(matchesPreprocessingRequest(recipe, { ...request, filters: [{ column: 'thrust', filter: { kind: 'lowpass', order: 4, f1: 9 } }] }), false);
  assert.equal(matchesPreprocessingRequest(recipe, { ...request, filters: [] }), false);
  assert.equal(matchesPreprocessingRequest(null, request), false);
});

test('submission transmits guarded native-record recipe and surfaces server failures', async () => {
  const originalFetch = globalThis.fetch;
  const request = { name: 'filtered', source_id: 'source-id', source_revision: 'revision', filters: [{ column: 'thrust', filter: { kind: 'detrend' } }] };
  try {
    globalThis.fetch = async (url, init) => {
      assert.equal(url, '/api/tests/source%20test/preprocess');
      assert.equal(init.method, 'POST');
      assert.deepEqual(JSON.parse(init.body), request);
      return { ok: true, json: async () => ({ name: 'filtered', status: 'rebuilding' }) };
    };
    assert.deepEqual(await createPreprocessedTest('source test', request), { name: 'filtered', status: 'rebuilding' });
    globalThis.fetch = async () => ({ ok: false, status: 409, statusText: 'Conflict', json: async () => ({ detail: 'Source revision changed.' }) });
    await assert.rejects(() => createPreprocessedTest('source test', request), /Source revision changed/);
    assert.deepEqual(await fetchPreprocessing('source test'), { path: '/tests/source%20test/preprocess' });
  } finally { globalThis.fetch = originalFetch; }
});
