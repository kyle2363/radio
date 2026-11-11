import test from 'node:test';
import assert from 'node:assert/strict';
import { createStation, setMood, nextTrack, toggleLike, toggleVibeLock, newMix } from '../model.mjs';

test('station starts with a deterministic mood queue and first track', () => {
  const station = createStation('chill');
  assert.equal(station.mood, 'chill');
  assert.equal(station.queue[0].id, 'glass-tide');
  assert.equal(station.currentId, 'glass-tide');
});

test('mood selection replaces queue predictably and resets current track', () => {
  const station = setMood(createStation('chill'), 'focus');
  assert.equal(station.mood, 'focus');
  assert.equal(station.currentId, 'soft-machines');
});

test('next skips the current track and wraps without random ordering', () => {
  const station = nextTrack(createStation('chill'));
  assert.equal(station.currentId, 'slow-orbit');
});

test('likes are tracked by song and vibe lock toggles independently', () => {
  const liked = toggleLike(createStation('chill'), 'glass-tide');
  assert.deepEqual(liked.likedIds, ['glass-tide']);
  assert.equal(toggleLike(liked, 'glass-tide').likedIds.length, 0);
  assert.equal(toggleVibeLock(liked).vibeLocked, true);
});

test('new mix deterministically rotates recommendations while preserving mood', () => {
  const a = newMix(createStation('chill'));
  const b = newMix(a);
  assert.equal(a.mood, 'chill');
  assert.notEqual(a.currentId, b.currentId);
});

test('vibe lock holds the current mood and queue against mood changes', () => {
  const locked = toggleVibeLock(createStation('chill'));
  assert.equal(setMood(locked, 'energy'), locked);
});

test('all desktop mood options build a deterministic queue', () => {
  for (const mood of ['neutral', 'chill', 'energetic', 'focus', 'melancholy', 'discovery']) {
    const station = setMood(createStation(), mood);
    assert.equal(station.mood, mood);
    assert.ok(station.queue.length > 0);
  }
});
