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
const { filterForSampleRates } = await import(await moduleUrl('../src/constants/filters.ts', {
  '../utils/numericField': numbersUrl,
}));

test('single cutoff blocks exact Nyquist and above without changing the valid specification', () => {
  for (const kind of ['lowpass', 'highpass']) {
    const valid = { kind, order: 4, f1: 49.9999 };
    assert.equal(filterForSampleRates(valid, [100]).spec, valid);
    for (const cutoff of [50, 75]) {
      const spec = { kind, order: 4, f1: cutoff };
      assert.deepEqual(filterForSampleRates(spec, [100]), { fs: 100, spec: null });
      assert.equal(spec.f1, cutoff);
    }
  }
});

test('mixed source rates use the lowest known sample rate for either band edge', () => {
  for (const kind of ['bandpass', 'bandstop']) {
    assert.equal(filterForSampleRates({ kind, f1: 10, f2: 50 }, [1000, undefined, 100, null]).spec, null);
    assert.equal(filterForSampleRates({ kind, f1: 50, f2: 60 }, [1000, 100]).spec, null);
    const valid = { kind, f1: 10, f2: 49 };
    assert.deepEqual(filterForSampleRates(valid, [1000, 100]), { fs: 100, spec: valid });
    assert.equal(filterForSampleRates({ kind, f1: 10, f2: 50 }, [1000]).spec.f2, 50);
  }
});

test('unknown or invalid sample-rate metadata does not invent a Nyquist limit', () => {
  const spec = { kind: 'lowpass', order: 4, f1: 500 };
  assert.deepEqual(filterForSampleRates(spec, [undefined, null, 0, -1, NaN, Infinity]), { fs: null, spec });
  assert.deepEqual(filterForSampleRates(spec, [undefined, 2000]), { fs: 2000, spec });
});

test('non-frequency filters and disabled filters retain their original meaning', () => {
  for (const spec of [null, { kind: 'moving_avg', windowS: 10 }, { kind: 'detrend' }, { kind: 'despike', windowS: .025 }])
    assert.deepEqual(filterForSampleRates(spec, [100]), { fs: 100, spec });
});
