import test from 'node:test';
import assert from 'node:assert/strict';
import { createFolderGesture, applyFolderRange } from '../src/ui/folderSelection.js';
import { createFilterSaver } from '../src/ui/filterSaver.js';

test('mousedown, mouseup, click toggles a folder exactly once', () => {
  let folders = [];
  const gesture = createFolderGesture(range => {
    folders = applyFolderRange(folders, ['A', 'B', 'C'], range);
  });
  gesture.begin(0, true); gesture.release();
  if (gesture.shouldToggle(1)) folders = folders.includes('A') ? [] : ['A'];
  assert.deepEqual(folders, ['A']);
  gesture.begin(0, false); gesture.release();
  assert.equal(gesture.shouldToggle(1), false);
  assert.deepEqual(folders, []);
});
test('drag range commits once and ignores the click after release', () => {
  const ranges = [];
  const gesture = createFolderGesture(range => ranges.push(range));
  gesture.begin(2, true); gesture.enter(0); gesture.release();
  assert.deepEqual(ranges, [{ fromIdx: 2, toIdx: 0, mode: true }]);
  assert.equal(gesture.shouldToggle(1), false);
  assert.deepEqual(applyFolderRange(['D'], ['A','B','C','D'], ranges[0]), ['A','B','C','D']);
});
test('keyboard and touch click without a mouse gesture remain usable', () => {
  const gesture = createFolderGesture(() => {});
  assert.equal(gesture.shouldToggle(0), true);
  assert.equal(gesture.shouldToggle(1), true);
  gesture.begin(0, true); gesture.release();
  assert.equal(gesture.shouldToggle(0), true);
});
test('a new gesture clears a missing synthetic click; range deselection preserves other folders', () => {
  let calls = 0;
  const gesture = createFolderGesture(() => calls++);
  gesture.begin(0, true); gesture.enter(2); gesture.release();
  gesture.begin(1, false); gesture.release();
  assert.equal(calls, 2);
  assert.equal(gesture.shouldToggle(1), false);
  assert.deepEqual(applyFolderRange(['A','B','D'], ['A','B','C','D'], {fromIdx:0,toIdx:1,mode:false}), ['D']);
});

test('filter saves serialize and a stale acknowledgement cannot replace newer selection', async () => {
  const writes = [], acknowledgements = [];
  let resolveFirst;
  const saver = createFilterSaver({ save: value => {
    writes.push(value);
    return writes.length === 1 ? new Promise(resolve => { resolveFirst = resolve; }) : Promise.resolve();
  }, onSaved: value => acknowledgements.push(value) });
  saver.submit('A'); const first = saver.flush();
  saver.submit('B'); saver.flush();
  assert.deepEqual(writes, ['A']);
  resolveFirst(); await first;
  assert.deepEqual(writes, ['A','B']);
  assert.deepEqual(acknowledgements, ['B']);
  saver.dispose();
});
test('closing the panel flushes the pending selection without calling UI callbacks', async () => {
  const writes = [];
  const saver = createFilterSaver({ save: async value => writes.push(value),
    onSaved: () => assert.fail('unmounted UI callback') });
  saver.submit('A'); await saver.dispose();
  assert.deepEqual(writes, ['A']);
});
