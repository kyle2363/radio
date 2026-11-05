import test from "node:test";
import assert from "node:assert/strict";
import { buildMetadataPayload, metadataSaveMessage } from "../src/components/songListHelpers.js";

test("metadata payload preserves intentional empty strings", () => {
  assert.deepEqual(buildMetadataPayload({ artist: "", title: " New ", album: "", bpm: "", lyrics: "", caption: "" }), {
    artist: "", title: "New", album: "", lyrics: "", caption: "",
  });
});

test("metadata save reports disk write errors", () => {
  assert.match(metadataSaveMessage({ id3: { artist: "error" } }), /artist/);
  assert.match(metadataSaveMessage({ acestep: { status: "invalid_format" } }), /invalid_format/);
  assert.match(metadataSaveMessage({ acestep: { status: "ok", fields_error: ["caption"] } }), /caption/);
  assert.match(metadataSaveMessage({ error: "Song not found" }), /Song not found/);
  assert.equal(metadataSaveMessage({}), null);
});
