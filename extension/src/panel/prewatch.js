// The "课前" card: how hard this video is, key words, questions to watch for.
import { formatCoverage } from "../lib/spans.js";
import { h } from "./dom.js";

const DIFFICULTY = {
  easy: ["轻松", "ok"],
  suitable: ["适合", "ok"],
  challenging: ["偏难", "warn"],
  hard: ["太难", "err"],
};

export function renderSummary(el, vocab) {
  if (!vocab) {
    el.replaceChildren();
    return;
  }
  const [label, cls] = DIFFICULTY[vocab.difficulty] || ["", ""];
  const unknown = vocab.words.filter((w) => !w.known).length;
  el.replaceChildren(
    h("span", { class: `badge ${cls}`, text: label }),
    h("span", { text: ` 词汇覆盖率 ${formatCoverage(vocab.coverage)} · 生词 ${unknown} 个` }),
  );
  el.title = `按你的词汇量（约 ${vocab.level} 词）和已标记的词计算；95% 以上能较好听懂，98% 以上比较轻松。`;
}

function wordCard(item, handlers) {
  const { word, gloss, example } = item;
  return h(
    "li",
    { class: "pw-word" },
    h(
      "div",
      { class: "pw-head" },
      h("strong", { text: word.lemma }),
      gloss && h("span", { class: "pos", text: ` ${gloss.part_of_speech}` }),
      gloss && h("span", { class: "zh", text: ` ${gloss.definition_zh}` }),
    ),
    gloss && h("div", { class: "en", text: gloss.definition_en }),
    example && h("div", { class: "example", text: `“${example}”` }),
    h(
      "div",
      { class: "pw-actions" },
      h("button", { class: "small", text: "认识", onclick: () => handlers.mark(word.lemma, "known") }),
      h("button", { class: "small", text: "收藏学习", onclick: () => handlers.mark(word.lemma, "learning") }),
    ),
  );
}

export function renderPrewatch(el, state, handlers) {
  if (!state) {
    el.replaceChildren();
    return;
  }
  const parts = [];
  const guide = state.guide;
  if (guide) {
    parts.push(
      h("p", { class: "predict", text: `🤔 ${guide.prediction_prompt}` }),
      h(
        "ol",
        { class: "questions" },
        guide.questions.map((q) =>
          h("li", {}, h("div", { text: q.question }), h("div", { class: "hint", text: q.hint_zh })),
        ),
      ),
    );
    if (guide.based_on_partial_transcript) {
      parts.push(h("p", { class: "muted", text: "视频较长，问题只根据前半部分生成。" }));
    }
  }
  const words = state.words.filter((w) => !w.word.known && !w.word.status);
  if (words.length) {
    parts.push(h("div", { class: "pw-subtitle", text: "先认识这几个词" }));
    parts.push(h("ul", { class: "pw-words" }, words.map((w) => wordCard(w, handlers))));
  }

  const status = {
    not_configured:
      !guide &&
      h("p", { class: "muted", text: "在 VideoTutor 主页填写 AI 密钥后，这里会显示生词释义和「带着问题看」的问题。" }),
    running: h("p", { class: "muted", text: "正在生成课前预习，大约需要十几秒…" }),
    failed: h(
      "p",
      { class: "err" },
      `${state.error || "生成失败"} `,
      h("button", { class: "small", text: "重试", onclick: handlers.generate }),
    ),
    idle: !guide && h("button", { text: "生成课前预习", onclick: handlers.generate }),
  }[state.status];

  el.replaceChildren(h("h2", { text: "课前" }), status || "", ...parts);
}
