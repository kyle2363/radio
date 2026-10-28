import test from 'node:test';
import assert from 'node:assert/strict';
import { buildLyricsLines } from '../src/content/lyrics.js';

test('missing lyrics stay renderable while timing is loading', () => {
  const lines = buildLyricsLines(null, null);
  assert.equal(lines.length, 0);
  assert.deepEqual(lines.map(line => line.text), []);
  assert.deepEqual(buildLyricsLines(undefined, 7), []);
});

test('plain and timed lyrics preserve line and timing information', () => {
  assert.deepEqual(buildLyricsLines(null, 'first\n\nlast').map(line => line.text), ['first', '', 'last']);
  const timed = [{ text: 'first', start_ms: 10, end_ms: 20, words: [] }];
  assert.deepEqual(buildLyricsLines(timed, null), timed);
});
