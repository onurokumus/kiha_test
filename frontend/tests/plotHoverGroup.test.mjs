import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/utils/plotHoverGroup.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext },
});
const { createPlotHoverGroup, parsePlotHoverMode } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);

function fixture() {
  let next = 0;
  const frames = new Map();
  globalThis.requestAnimationFrame = callback => { frames.set(++next, callback); return next; };
  globalThis.cancelAnimationFrame = id => frames.delete(id);
  const group = createPlotHoverGroup();
  const flush = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); };
  function member(xAxis, yAxis, width = 100, xRange = [0, 10], yRange = [0, 10]) {
    const result = { xAxis, yAxis, renders: 0, hidden: 0,
      render() { this.renders++; }, hide() { this.hidden++; },
      plot: { root: { isConnected: true }, cursor: { left: 50, top: 20 },
        over: { getBoundingClientRect: () => ({ width, height: 100 }) },
        posToVal(position, axis) { const [lo, hi] = axis === 'x' ? xRange : yRange;
          return lo + position / (axis === 'x' ? width : 100) * (hi - lo); },
        valToPos(value, axis) { const [lo, hi] = axis === 'x' ? xRange : yRange;
          return (value - lo) / (hi - lo) * (axis === 'x' ? width : 100); },
        setCursor(cursor, fire) { assert.equal(fire, false); this.cursor = cursor; },
        setScale() { assert.fail('Inspection must not alter scales'); },
        setData() { assert.fail('Inspection must not replace scientific data'); },
      } };
    result.dispose = group.register(result);
    return result;
  }
  return { group, member, flush, frames };
}

test('linked time/frequency readouts share an actual X value across different widths and zoom ranges', () => {
  const { group, member, flush } = fixture();
  const source = member('time'), target = member('time', undefined, 200, [0, 20]);
  group.move(source); flush();
  assert.equal(source.renders, 1); assert.equal(target.renders, 1);
  assert.equal(target.plot.posToVal(target.plot.cursor.left, 'x'), 5);
  assert.equal(target.plot.cursor.top, 20);
});

test('XY shares only matching axes, retaining relative position for variables with unlike units', () => {
  const { group, member, flush } = fixture();
  const source = member('rpm', 'thrust'), mixed = member('rpm', 'torque', 200, [0, 20], [100, 200]);
  const different = member('voltage', 'thrust', 200, [100, 200], [0, 20]);
  group.move(source); flush();
  assert.equal(mixed.plot.posToVal(mixed.plot.cursor.left, 'x'), 5);
  assert.equal(mixed.plot.cursor.top, 20);
  assert.equal(different.plot.cursor.left, 100);
  assert.equal(different.plot.posToVal(different.plot.cursor.top, 'y'), 2);
});

test('a crop excluding the shared coordinate clears the sample cursor but still renders an unavailable readout', () => {
  const { group, member, flush } = fixture();
  const source = member('Hz'), target = member('Hz', undefined, 100, [0, 1]);
  group.move(source); flush();
  assert.deepEqual(target.plot.cursor, { left: -1, top: -1 });
  assert.equal(target.renders, 1);
});

test('pointer moves coalesce to the latest source and disabling cancels all pending readouts', () => {
  const { group, member, flush, frames } = fixture();
  const first = member('time'), last = member('time');
  for (let i = 0; i < 20; i++) group.move(first);
  last.plot.cursor.left = 70; group.move(last);
  assert.equal(frames.size, 1); flush();
  assert.equal(first.plot.cursor.left, 70); assert.equal(first.renders, 1);
  group.move(first); group.setMode('none'); flush();
  assert.equal(first.renders, 1); assert.deepEqual(first.plot.cursor, { left: -1, top: -1 });
  group.move(first); assert.equal(frames.size, 0);
  group.setMode('all'); first.plot.cursor = { left: 40, top: 20 }; group.move(first); flush();
  assert.equal(last.renders, 2);
});

test('destroying a member clears peers and cancels stale callbacks; disconnected plots never render', () => {
  const { group, member, flush } = fixture();
  const first = member('time'), peer = member('time');
  group.move(first); first.dispose(); flush();
  assert.equal(first.renders, 0); assert.ok(peer.hidden > 0);
  peer.plot.root.isConnected = false; group.move(peer); flush();
  assert.equal(peer.renders, 0);
});

test('current mode renders only the hovered member and moves ownership without a stale peer box', () => {
  const { group, member, flush } = fixture();
  const first = member('time'), next = member('time');
  group.setMode('current');
  first.plot.cursor = { left: 50, top: 20 };
  group.move(first); flush();
  assert.equal(first.renders, 1); assert.equal(next.renders, 0);
  const firstHidden = first.hidden;
  next.plot.cursor = { left: 50, top: 20 };
  group.move(next); flush();
  assert.equal(next.renders, 1); assert.equal(first.renders, 1);
  assert.ok(first.hidden > firstHidden);
  group.setMode('all'); next.plot.cursor = { left: 60, top: 20 };
  group.move(next); flush();
  assert.equal(first.renders, 2); assert.equal(next.renders, 2);
});

test('changing modes cancels the pending source before showing only the new current plot', () => {
  const { group, member, flush } = fixture();
  const first = member('time'), next = member('time');
  group.move(first); group.setMode('current'); flush();
  assert.equal(first.renders, 0); assert.equal(next.renders, 0);
  next.plot.cursor = { left: 50, top: 20 }; group.move(next); flush();
  assert.equal(first.renders, 0); assert.equal(next.renders, 1);
});

test('three-way preference preserves legacy on/off choices and safely defaults corrupt or missing storage', () => {
  for (const mode of ['current', 'all', 'none']) assert.equal(parsePlotHoverMode(mode), mode);
  assert.equal(parsePlotHoverMode('false'), 'none');
  for (const value of ['true', null, '', 'bad-mode', '{broken']) assert.equal(parsePlotHoverMode(value), 'all');
});
