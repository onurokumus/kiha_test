import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/scatterTicks.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { createScatterTicks, formatScatterTick } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
);

function valid(low, high, pixels, axis = 'x') {
  const result = createScatterTicks(low, high, pixels, axis);
  assert.ok(result.ticks.length >= 2 && result.ticks.length <= 12, JSON.stringify(result));
  assert.ok(Number.isFinite(result.step) && result.step > 0);
  assert.ok(result.ticks.every((value, i) => Number.isFinite(value) && value >= low && value <= high &&
    (i === 0 || value > result.ticks[i - 1])), JSON.stringify(result));
  assert.equal(new Set(result.ticks.map(result.format)).size, result.ticks.length);
  assert.ok(result.ticks.every(value => !/NaN|Infinity|^-0$/.test(result.format(value))));
  assert.ok(result.minorTicks.every(value => value > low && value < high && !result.ticks.includes(value)));
  return result;
}

test('engineering ticks replace arbitrary endpoints without changing the domain', () => {
  const result = valid(1494.097, 4909.992, 450);
  assert.deepEqual(result.ticks, [2000, 3000, 4000]);
  assert.equal(result.step, 1000);
  assert.deepEqual(result.ticks.map(result.format), ['2,000', '3,000', '4,000']);
  assert.deepEqual(result.minorTicks, [2500, 3500]);
});

test('axis pixel length controls density, with tighter vertical spacing and a hard cap', () => {
  const narrow = valid(0, 100, 240);
  const wide = valid(0, 100, 900);
  const vertical = valid(0, 100, 240, 'y');
  assert.ok(narrow.ticks.length < wide.ticks.length);
  assert.ok(vertical.step < narrow.step);
  assert.ok(valid(0, 100, 1e9).ticks.length <= 12);
});

test('small pans keep tick spacing and shared tick labels anchored to round values', () => {
  const first = valid(-1.13, 3.87, 450);
  const shifted = valid(-1.11, 3.89, 450);
  assert.equal(first.step, shifted.step);
  assert.deepEqual(first.ticks, shifted.ticks);
  assert.deepEqual(first.ticks, [-1, 0, 1, 2, 3]);
});

test('narrow plots refine a single interior tick instead of exposing long raw endpoints', () => {
  for (const width of [240, 280, 320]) {
    const result = valid(6.126080130241357, 93.45725309209197, width);
    assert.equal(result.step, 25);
    assert.deepEqual(result.ticks, [25, 50, 75]);
    assert.deepEqual(result.ticks.map(result.format), ['25', '50', '75']);
  }
});

test('quarter-decade precision avoids duplicate labels and binary floating-point noise', () => {
  const quarter = valid(0, 1, 360);
  assert.equal(quarter.step, .25);
  assert.deepEqual(quarter.ticks.map(quarter.format), ['0', '0.25', '0.5', '0.75', '1']);
  const decimal = valid(-.3, .3, 540);
  assert.deepEqual(decimal.ticks, [-.3, -.2, -.1, 0, .1, .2, .3]);
  assert.equal(formatScatterTick(-0, .1), '0');
  assert.equal(formatScatterTick(.00012, .00001), '0.00012');
  assert.equal(formatScatterTick(1234.00012, .00001), '1,234.00012');
});

test('tiny and large magnitudes use readable distinct scientific labels', () => {
  for (const [low, high] of [[1e-9, 5e-9], [-5e-12, 5e-12], [1e9, 5e9]]) {
    const result = valid(low, high, 450);
    assert.ok(result.ticks.filter(value => value !== 0).every(value => /e-?\d+$/.test(result.format(value))));
  }
  assert.equal(formatScatterTick(1.25e-6, 2.5e-7), '1.25e-6');
  assert.equal(formatScatterTick(1.25e8, 2.5e7), '1.25e8');
});

test('zooming into large offsets preserves differences that fixed significant digits would erase', () => {
  for (const [low, high] of [[1e9, 1e9 + .01], [1e15, 1e15 + 1], [1e308, 1.0000000000000002e308]]) {
    valid(low, high, 450);
  }
});

test('opposite extremes, near-maximum values and subnormal ranges remain finite and bounded', () => {
  for (const pixels of [0, 1, 90, 450, 100000]) {
    valid(-Number.MAX_VALUE, Number.MAX_VALUE, pixels);
    valid(0, Number.MIN_VALUE, pixels);
    valid(-Number.MIN_VALUE, Number.MIN_VALUE, pixels);
    valid(1e-320, 2e-320, pixels);
  }
});

test('degenerate, reversed and invalid inputs have predictable harmless output', () => {
  assert.deepEqual(createScatterTicks(5, 5, 450).ticks, [5]);
  assert.deepEqual(createScatterTicks(-0, 0, 450).ticks, [0]);
  assert.deepEqual(createScatterTicks(10, 0, 450).ticks, createScatterTicks(0, 10, 450).ticks);
  for (const invalid of [NaN, Infinity, -Infinity]) {
    assert.deepEqual(createScatterTicks(invalid, 1, 450).ticks, []);
    assert.deepEqual(createScatterTicks(0, invalid, 450).minorTicks, []);
    assert.equal(formatScatterTick(invalid, 1), '');
  }
  valid(0, 10, NaN);
  valid(0, 10, -100);
  const short = valid(1.001, 1.049, 1);
  assert.deepEqual(short.ticks.map(short.format), ['1.02', '1.04']);
  const adjacent = valid(1e308, 1.0000000000000002e308, 450);
  assert.deepEqual(adjacent.ticks.map(adjacent.format), ['1e308', '1.0000000000000002e308']);
});
