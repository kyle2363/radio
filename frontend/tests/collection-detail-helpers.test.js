import test from "node:test";
import assert from "node:assert/strict";
import {
  COMPLETION_PAGE_SIZE,
  completionPercent,
  songSearchParams,
} from "../src/components/collectionDetailHelpers.js";

test("completion percentage is already expressed on a 0–100 scale", () => {
  assert.equal(completionPercent({ avg_completion: 73.4 }), 73);
  assert.equal(completionPercent({ avg_completion: null }), 0);
});

test("song search uses server query and bounded pages", () => {
  assert.deepEqual(songSearchParams("  blue train ", 2), {
    page: "2",
    page_size: String(COMPLETION_PAGE_SIZE),
    sort_by: "title",
    summary: "true",
    search: "blue train",
  });
  assert.equal("search" in songSearchParams("  ", 1), false);
});
