import test from "node:test";
import assert from "node:assert/strict";
import { MOODS, MOOD_COLORS, moodColor, remoteHostingPill } from "../src/ui/consistency.js";

test("one mood palette covers every mood", () => {
  assert.deepEqual(Object.keys(MOOD_COLORS).sort(),
    ["chill", "discovery", "energetic", "focus", "melancholy", "neutral"]);
  for (const mood of MOODS) assert.equal(MOOD_COLORS[mood.id], mood.color);
});

test("mood buttons and charts share the same colour", () => {
  assert.equal(moodColor("chill"), MOOD_COLORS.chill);
  assert.equal(moodColor("CHILL"), MOOD_COLORS.chill);
  assert.equal(moodColor("unknown"), MOOD_COLORS.neutral);
  assert.equal(moodColor(null), MOOD_COLORS.neutral);
});

test("remote pill is hidden while hosting is off", () => {
  assert.equal(remoteHostingPill(null), null);
  assert.equal(remoteHostingPill({ state: "off" }), null);
});

test("remote pill reports live, pending and failing hosting", () => {
  const live = remoteHostingPill({ state: "available" });
  assert.equal(live.tone, "ok");
  assert.equal(live.label, "Remote");
  for (const state of ["starting_gateway", "starting_tunnel", "verifying_endpoint", "reconnecting", "stopping"]) {
    assert.equal(remoteHostingPill({ state }).tone, "warn", state);
  }
  assert.equal(remoteHostingPill({ state: "error" }).tone, "err");
  assert.equal(remoteHostingPill({ state: "discovery_error" }).tone, "err");
});

test("remote pill tooltip names the state", () => {
  assert.match(remoteHostingPill({ state: "available" }).title, /available/i);
  assert.match(remoteHostingPill({ state: "reconnecting" }).title, /reconnecting/i);
});
