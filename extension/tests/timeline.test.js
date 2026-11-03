import assert from "node:assert/strict";
import { test } from "node:test";

import { findSegmentIndex, formatTime } from "../src/lib/timeline.js";

const SEGS = [
  { start_ms: 0, end_ms: 1000, text: "a" },
  { start_ms: 2000, end_ms: 3000, text: "b" },
  { start_ms: 3000, end_ms: 4500, text: "c" },
];

test("finds the segment playing at a time", () => {
  assert.equal(findSegmentIndex(SEGS, 0), 0);
  assert.equal(findSegmentIndex(SEGS, 999), 0);
  assert.equal(findSegmentIndex(SEGS, 2000), 1);
  assert.equal(findSegmentIndex(SEGS, 3000), 2, "boundary belongs to the later segment");
  assert.equal(findSegmentIndex(SEGS, 4499), 2);
});

test("returns -1 in gaps and outside", () => {
  assert.equal(findSegmentIndex(SEGS, 1500), -1);
  assert.equal(findSegmentIndex(SEGS, 4500), -1);
  assert.equal(findSegmentIndex(SEGS, -1), -1);
  assert.equal(findSegmentIndex([], 10), -1);
});

test("formats times", () => {
  assert.equal(formatTime(0), "0:00");
  assert.equal(formatTime(75_000), "1:15");
  assert.equal(formatTime(3_725_000), "1:02:05");
  assert.equal(formatTime(-5), "0:00");
});
