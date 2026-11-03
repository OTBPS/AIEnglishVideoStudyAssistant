// Pure helpers for transcript timelines (unit-tested with `node --test`).

// Index of the segment playing at `ms`, or -1 in a gap / outside the transcript.
// `segments` must be sorted by start_ms and non-overlapping (the backend guarantees it).
export function findSegmentIndex(segments, ms) {
  let lo = 0;
  let hi = segments.length - 1;
  let found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segments[mid].start_ms <= ms) {
      found = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return found >= 0 && ms < segments[found].end_ms ? found : -1;
}

// 75_000 -> "1:15", 3_725_000 -> "1:02:05"
export function formatTime(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}
