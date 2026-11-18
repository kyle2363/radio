import test from "node:test";
import assert from "node:assert/strict";
import { historySummary, pairOutcome, settledLabel, songLabel, validFeelName, ENERGY_BUCKETS } from "../src/components/feelsHelpers.js";

test("energy buckets match the phone", () => {
  assert.deepEqual(ENERGY_BUCKETS.map((b) => b.value), [0.875, 0.625, 0.375, 0.125]);
});

test("history summary lists current members only", () => {
  const march = Date.UTC(2026, 2, 15) / 1000;
  const summary = historySummary([
    { feel: { name: "Hype" }, member: true, since: march },
    { feel: { name: "Focus" }, member: false, since: march },
    { feel: { name: "Drive" }, member: true, since: null },
  ], "en-US");
  assert.equal(summary, "Hype since March 2026");
});

test("pair choices map to compare outcomes", () => {
  assert.equal(pairOutcome("left"), "a");
  assert.equal(pairOutcome("right"), "b");
  assert.equal(pairOutcome("tie"), "tie");
  assert.equal(pairOutcome("unfitRight"), "doesnt_fit_b");
  assert.equal(pairOutcome("nope"), null);
});

test("labels and names", () => {
  assert.equal(songLabel({ artist: "", title: "" }), "Unknown title");
  assert.equal(songLabel({ artist: "A", title: "B" }), "A — B");
  assert.match(settledLabel(82.4), /82% settled — more rounds/);
  assert.equal(validFeelName("  "), false);
  assert.equal(validFeelName("x".repeat(41)), false);
  assert.equal(validFeelName("Gym"), true);
});
