import assert from "node:assert/strict";
import { test } from "node:test";

import { formatCoverage, splitBySpans } from "../src/lib/spans.js";

test("splits text into word and gap pieces", () => {
  const text = "As Robinson said, kids learn.";
  const spans = [
    [0, 2, 0],
    [3, 11, -1],
    [12, 16, 1],
    [18, 22, 2],
    [23, 28, 3],
  ];
  assert.deepEqual(splitBySpans(text, spans), [
    { text: "As", index: 0 },
    { text: " ", index: null },
    { text: "Robinson", index: null },
    { text: " ", index: null },
    { text: "said", index: 1 },
    { text: ", ", index: null },
    { text: "kids", index: 2 },
    { text: " ", index: null },
    { text: "learn", index: 3 },
    { text: ".", index: null },
  ]);
});

test("ignores malformed spans and keeps all the text", () => {
  const pieces = splitBySpans("hello world", [[0, 5, 0], [3, 8, 1], [6, 99, 2]]);
  assert.equal(pieces.map((p) => p.text).join(""), "hello world");
  assert.deepEqual(pieces[0], { text: "hello", index: 0 });
  assert.deepEqual(splitBySpans("", []), []);
});

test("formats coverage", () => {
  assert.equal(formatCoverage(1), "100%");
  assert.equal(formatCoverage(0.9567), "96%");
  assert.equal(formatCoverage(0.999), "99%");
  assert.equal(formatCoverage(0), "0%");
});
