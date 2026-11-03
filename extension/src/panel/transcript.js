// Transcript list with clickable words, and the popover that opens on a word.
import { splitBySpans } from "../lib/spans.js";
import { formatTime } from "../lib/timeline.js";
import { h } from "./dom.js";

export function wordClass(word) {
  if (!word) return "w";
  if (word.status === "learning") return "w learning";
  return word.known ? "w" : "w unknown";
}

export function renderTranscript(listEl, segments, vocab) {
  const words = vocab?.words || [];
  listEl.replaceChildren(
    ...segments.map((seg, i) => {
      const spans = vocab?.spans?.[i] || [];
      const textEl = h(
        "span",
        { class: "text" },
        splitBySpans(seg.text, spans).map((piece) =>
          piece.index === null
            ? piece.text
            : h("span", { class: wordClass(words[piece.index]), dataset: { word: String(piece.index) }, text: piece.text }),
        ),
      );
      return h("li", { dataset: { index: String(i) } }, h("time", { text: formatTime(seg.start_ms) }), textEl);
    }),
  );
}

const STATUS_LABEL = { known: "已标记认识", learning: "学习中", ignored: "已忽略" };

// Opens next to `anchor`. `actions.lookup()` resolves to a gloss or throws.
export function openWordPopover(pop, anchor, word, actions) {
  const glossBox = h("div", { class: "gloss" });
  const lookup = async () => {
    glossBox.replaceChildren(h("span", { class: "muted", text: "正在查询…" }));
    try {
      const g = await actions.lookup(word.lemma);
      glossBox.replaceChildren(
        h("div", {}, h("span", { class: "pos", text: g.part_of_speech }), " ", h("span", { class: "zh", text: g.definition_zh })),
        h("div", { class: "en", text: g.definition_en }),
      );
    } catch (err) {
      glossBox.replaceChildren(h("span", { class: "err", text: err.message }));
    }
  };
  const btn = (text, status) =>
    h("button", { class: "small", text, onclick: () => actions.mark(word.lemma, status) });

  pop.replaceChildren(
    h(
      "div",
      { class: "pop-head" },
      h("strong", { text: word.lemma }),
      h("span", { class: "muted", text: word.status ? ` ${STATUS_LABEL[word.status]}` : word.known ? " 按词汇量算作认识" : " 生词" }),
    ),
    glossBox,
    h(
      "div",
      { class: "pw-actions" },
      actions.canLookup && h("button", { class: "small", text: "查释义", onclick: lookup }),
      word.status !== "known" && btn("认识", "known"),
      word.status !== "learning" && btn("收藏学习", "learning"),
      word.status !== "ignored" && btn("忽略", "ignored"),
      word.status && btn("清除标记", null),
    ),
  );
  pop.hidden = false;
  const box = anchor.getBoundingClientRect();
  // Below the word, or above it when it would run off the bottom of the panel.
  const fitsBelow = box.bottom + 4 + pop.offsetHeight <= window.innerHeight;
  const top = window.scrollY + (fitsBelow ? box.bottom + 4 : Math.max(4, box.top - 4 - pop.offsetHeight));
  const left = Math.max(8, Math.min(box.left + window.scrollX, window.innerWidth - pop.offsetWidth - 8));
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
  if (actions.autoLookup) lookup();
}
