// Split a transcript line into plain-text pieces and word pieces, using the
// [start, end, wordIndex] spans computed by the desktop app (tokenising lives
// only there). wordIndex -1 marks a name: rendered as plain text.
export function splitBySpans(text, spans) {
  const pieces = [];
  let pos = 0;
  for (const [start, end, index] of spans) {
    if (start < pos || end > text.length || start >= end) continue; // defensive: skip bad spans
    if (start > pos) pieces.push({ text: text.slice(pos, start), index: null });
    pieces.push({ text: text.slice(start, end), index: index >= 0 ? index : null });
    pos = end;
  }
  if (pos < text.length) pieces.push({ text: text.slice(pos), index: null });
  return pieces;
}

// Whole percent, but never "100%" unless every word is known: 0.9567 -> "96%", 0.999 -> "99%".
export function formatCoverage(fraction) {
  if (fraction >= 1) return "100%";
  return `${Math.min(99, Math.round(Math.max(0, fraction) * 100))}%`;
}
