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
const { numericError, parseFiniteNumber } = await import(numbersUrl);
const { buildFilterSpec, DEFAULT_FILTER_UI } = await import(await moduleUrl(
  '../src/constants/filters.ts', { '../utils/numericField': numbersUrl },
));

test('cleared and partial numeric drafts never become zero or a finite number', () => {
  for (const draft of ['', '  ', '-', '+', '.', '-.', '1e', '1e-', 'NaN', 'Infinity', '-Infinity', '1e999', '0x20', '1,23'])
    assert.equal(parseFiniteNumber(draft), null, draft);
  assert.equal(numericError('', { allowEmpty: true }), '');
  assert.notEqual(numericError(''), '');
});

test('finite decimal and engineering notation retain the unrounded numeric value', () => {
  for (const [draft, expected] of [['0', 0], ['-0', -0], ['.5', .5], ['1.', 1], [' -2.5e-3 ', -.0025], ['+7E3', 7000], ['12.3456789012345', 12.3456789012345]])
    assert.equal(parseFiniteNumber(draft), expected);
});

test('inclusive/exclusive boundaries and integer order distinguish allowed zero values', () => {
  assert.equal(numericError('0', { min: 0 }), '');
  assert.notEqual(numericError('0', { min: 0, exclusiveMin: true }), '');
  assert.equal(numericError('100', { max: 100 }), '');
  assert.notEqual(numericError('100', { max: 100, exclusiveMax: true }), '');
  assert.notEqual(numericError('2.5', { integer: true }), '');
  assert.equal(numericError('2e0', { integer: true, min: 1, max: 10 }), '');
});

test('an incomplete filter cannot become a default or non-finite processing request', () => {
  for (const order of ['', ' ', '0', '11', '2.5', 'Infinity'])
    assert.equal(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'lowpass', f1: '10', order }), null);
  for (const draft of ['', '-', '1e', 'Infinity', '1e999']) {
    assert.equal(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'moving_avg', winS: draft }), null);
    assert.equal(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'lowpass', f1: draft }), null);
    assert.equal(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'despike', absFloor: draft }), null);
  }
});

test('valid filter values retain their scientific specification and unit conversion', () => {
  assert.deepEqual(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'bandpass', f1: '2.5', f2: '12.5' }), { kind: 'bandpass', order: 4, f1: 2.5, f2: 12.5 });
  assert.deepEqual(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'moving_avg', winS: '.025' }), { kind: 'moving_avg', windowS: .025 });
  assert.deepEqual(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'despike' }), { kind: 'despike', windowS: .025, maxSpikeS: .01, threshold: 3.5, absFloor: 0, replacement: 'linear' });
  assert.equal(buildFilterSpec({ ...DEFAULT_FILTER_UI, kind: 'despike', despikeWindowMs: '20' }), null);
});
