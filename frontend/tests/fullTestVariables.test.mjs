import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

async function moduleUrl(path, replacements = {}) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [from, to] of Object.entries(replacements)) source = source.replace(from, to);
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
  });
  return 'data:text/javascript;base64,' + Buffer.from(outputText).toString('base64');
}
const themeUrl = await moduleUrl('../src/constants/uplotTheme.ts');
const { themeSeriesColor } = await import(themeUrl);
const { createFullTestColorResolver, fullTestVariableColor } = await import(
  await moduleUrl('../src/utils/fullTestVariables.ts', { '../constants/uplotTheme': themeUrl }));
const catalog = Array.from({ length: 512 }, (_, index) => ({ key: `signal_${index}` }));
const keys = indices => indices.map(index => catalog[index].key);
const resolve = createFullTestColorResolver(catalog);

// Independent matrix-form reference for visible sRGB separation in both themes.
const multiply = (matrix, values) => matrix.map(row => row.reduce((sum, value, i) => sum + value * values[i], 0));
function lab(hex) {
  const linear = hex.slice(1).match(/../g).map(channel => parseInt(channel, 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  const cone = multiply([
    [.4122214708, .5363325363, .0514459929],
    [.2119034982, .6806995451, .1073969566],
    [.0883024619, .2817188376, .6299787005],
  ], linear).map(Math.cbrt);
  return multiply([
    [.2104542553, .7936177850, -.0040720468],
    [1.9779984951, -2.4285922050, .4505937099],
    [.0259040371, .7827717662, -.8086757660],
  ], cone);
}
function assertSeparated(colors, minimum = .12) {
  for (const dark of [false, true]) {
    const coordinates = colors.map(color => lab(themeSeriesColor(color, dark)));
    coordinates.forEach((a, i) => coordinates.slice(i + 1).forEach((b, j) => {
      const distance = Math.hypot(...a.map((value, channel) => value - b[channel]));
      assert.ok(distance >= minimum - 1e-9,
        `${dark ? 'dark' : 'light'}: ${colors[i]} / ${colors[i + j + 1]} distance ${distance}`);
    }));
  }
}

test('single-variable colors and already distinct comparisons keep their source identity', () => {
  for (const config of catalog) assert.equal(resolve([config.key])[0], fullTestVariableColor(config.key, catalog));
  assert.deepEqual(resolve(keys([0, 1, 2])), ['#d55e00', '#21834a', '#9333b8']);
});

test('exact and near source collisions become separated in both themes', () => {
  assert.equal(fullTestVariableColor('signal_6', catalog), fullTestVariableColor('signal_287', catalog));
  for (const pair of [[6, 287], [6, 13], [0, 5], [2, 3], [0, 4]]) {
    const columns = keys(pair);
    const colors = resolve(columns);
    assert.equal(colors[0], fullTestVariableColor(columns[0], catalog));
    assertSeparated(colors);
    assertSeparated(resolve([...columns].reverse()));
  }
});

test('any pair from a 512-column source remains separated after changing the primary', () => {
  for (let i = 0; i < catalog.length; i++) {
    for (let j = i + 1; j < catalog.length; j++) assertSeparated(resolve(keys([i, j])));
  }
});

test('six-variable comparisons have distinct light and dark colors for adversarial selections', () => {
  for (const indices of [[0, 1, 2, 3, 4, 5], [6, 287, 13, 294, 20, 301], [0, 5, 4, 6, 13, 20]]) {
    assertSeparated(resolve(keys(indices)), .09);
  }
  let seed = 20260930;
  for (let iteration = 0; iteration < 1000; iteration++) {
    const selected = new Set();
    while (selected.size < 6) {
      seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
      selected.add(seed >>> 23);
    }
    assertSeparated(resolve(keys([...selected])), .09);
  }
});

test('adding variables keeps every existing trace color and previews match the committed choice', () => {
  const columns = keys([6, 287, 13, 0, 5, 1]);
  for (let count = 1; count < columns.length; count++) {
    const before = resolve(columns.slice(0, count));
    const preview = resolve(columns.slice(0, count + 1));
    assert.deepEqual(preview.slice(0, count), before);
    assert.deepEqual(createFullTestColorResolver(catalog)(columns.slice(0, count + 1)), preview);
  }
});

test('removal, re-addition and reopening are deterministic and do not affect other plots', () => {
  const columns = keys([6, 287, 13, 0, 5, 1]);
  const before = resolve(columns);
  resolve(columns.filter((_, index) => index !== 2));
  resolve(keys([0, 1, 2]));
  assert.deepEqual(resolve(columns), before);
  assert.deepEqual(createFullTestColorResolver(catalog)(JSON.parse(JSON.stringify(columns))), before);
  assertSeparated(resolve([...columns].reverse()), .09);
});

test('duplicate identities share a color while unknown distinct variables still avoid collisions', () => {
  const colors = resolve(['missing_a', 'missing_a', 'missing_b', 'signal_0']);
  assert.equal(colors[0], colors[1]);
  assertSeparated([colors[0], colors[2], colors[3]]);
  assert.deepEqual(resolve([]), []);
});
