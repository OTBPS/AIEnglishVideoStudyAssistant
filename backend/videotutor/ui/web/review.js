"use strict";
// Review page. Two modes:
//   /ui/review.html                         the daily queue of due cards
//   /ui/review.html?platform=youtube&id=X   post-watch: comprehension + this video's words

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const video = params.get("platform") && params.get("id")
  ? { platform: params.get("platform"), external_id: params.get("id") }
  : null;
const context = video ? "postwatch" : "review";

const KIND_LABEL = {
  recognize: "选出这个词在句子里的意思",
  spell: "根据中文意思，填出空缺的英文单词",
  recall: "写出这个词在句子里的意思（中文或英文都可以）",
  use: "用这个词写一个你自己的英文句子",
};
const GRADE_LABEL = { good: "✓ 正确", easy: "✓ 正确", hard: "△ 基本正确", again: "✗ 还不对" };

const stats = { answered: 0, right: 0, graduated: [] };
const exclude = new Set();

// ------------------------------------------------------------ helpers
function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v; // never innerHTML: AI and transcript text
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) el.append(c);
  return el;
}

async function api(path, method = "GET", body) {
  const resp = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await resp.json().catch(() => null);
  if (!resp.ok) throw new Error(data?.detail || `请求失败（${resp.status}）`);
  return data;
}

function highlight(sentence, word) {
  if (!word) return [sentence];
  const i = sentence.toLowerCase().indexOf(word.toLowerCase().slice(0, Math.max(3, word.length - 2)));
  if (i < 0) return [sentence];
  let j = i;
  while (j < sentence.length && /[A-Za-z'’]/.test(sentence[j])) j++;
  return [sentence.slice(0, i), h("mark", { text: sentence.slice(i, j) }), sentence.slice(j)];
}

// ------------------------------------------------------------ comprehension (post-watch)
async function loadComprehension() {
  const pw = await api(`/api/videos/${video.platform}/${video.external_id}/prewatch`);
  if (!pw.guide) return;
  $("comprehension").hidden = false;
  $("questions").replaceChildren(
    ...pw.guide.questions.map((q, i) => {
      const box = h("textarea", { placeholder: "你的回答" });
      const out = h("div");
      const btn = h("button", {
        text: "提交",
        onclick: async () => {
          btn.disabled = true;
          out.replaceChildren(h("p", { class: "note", text: "AI 正在批改…" }));
          try {
            const r = await api(
              `/api/videos/${video.platform}/${video.external_id}/comprehension/${i}`, "POST", { answer: box.value },
            );
            out.replaceChildren(h("div", { class: `result ${r.grade}`, text: `${GRADE_LABEL[r.grade]}　${r.feedback_zh}\n参考答案：${r.reference_answer}` }));
          } catch (err) {
            out.replaceChildren(h("p", { class: "result again", text: err.message }));
            btn.disabled = false;
          }
        },
      });
      return h("li", {}, h("div", { class: "q", text: q.question }), h("div", { class: "hint", text: q.hint_zh }), box, h("div", { class: "row" }, btn), out);
    }),
  );
}

// ------------------------------------------------------------ word exercises
async function nextExercise() {
  const box = $("exercise");
  box.replaceChildren(h("p", { class: "note", text: "加载中…" }));
  let res;
  try {
    res = await api("/api/review/next", "POST", { context, ...(video || {}), exclude: [...exclude] });
  } catch (err) {
    box.replaceChildren(h("p", { class: "result again", text: err.message }));
    return;
  }
  if (!res.exercise) return showDone();
  renderExercise(res.exercise);
}

function renderExercise(ex) {
  const started = performance.now();
  const box = $("exercise");
  const out = h("div");
  let submitted = false;

  const submit = async (answer, chosenBtn) => {
    if (submitted) return;
    submitted = true;
    out.replaceChildren(h("p", { class: "note", text: ex.kind === "recall" || ex.kind === "use" ? "AI 正在批改…" : "" }));
    let r;
    try {
      r = await api("/api/review/answer", "POST", { exercise_id: ex.id, answer, duration_ms: Math.round(performance.now() - started) });
    } catch (err) {
      out.replaceChildren(h("p", { class: "result again", text: err.message }), h("button", { text: "下一题", onclick: nextExercise }));
      return;
    }
    stats.answered += 1;
    // Post-watch: each word once. Daily review: a missed word comes back when due again.
    if (r.grade !== "again") stats.right += 1;
    if (r.grade !== "again" || context === "postwatch") exclude.add(r.lemma);
    if (r.graduated) stats.graduated.push(r.lemma);
    for (const b of box.querySelectorAll(".options button")) {
      if (b.textContent === r.correct_answer) b.classList.add("right");
      else if (b === chosenBtn) b.classList.add("wrong");
      b.disabled = true;
    }
    const lines = [GRADE_LABEL[r.grade]];
    if (ex.kind !== "recognize") lines.push(`正确答案：${r.correct_answer}`);
    if (r.feedback_zh) lines.push(r.feedback_zh);
    if (r.graduated) lines.push("🎓 这个词已经记得很牢，自动标记为「认识」。");
    const next = h("button", { text: "下一题", onclick: nextExercise });
    out.replaceChildren(h("div", { class: `result ${r.grade}`, text: lines.join("\n") }), h("div", { class: "row" }, next));
    next.focus();
  };

  const parts = [h("div", { class: "kind", text: KIND_LABEL[ex.kind] })];
  if (ex.word) parts.push(h("div", { class: "word", text: ex.word }));
  parts.push(h("p", { class: "sentence" }, ex.kind === "spell" ? ex.sentence : highlight(ex.sentence, ex.word)));
  if (ex.hint_zh) parts.push(h("p", { class: "hint-zh", text: `意思：${ex.hint_zh}` }));

  if (ex.kind === "recognize") {
    parts.push(h("div", { class: "options" }, ex.options.map((o) => {
      const b = h("button", { text: o });
      b.onclick = () => submit(o, b);
      return b;
    })));
  } else {
    const input = ex.kind === "spell"
      ? h("input", { class: "answer-input", type: "text", autocomplete: "off", spellcheck: "false" })
      : h("textarea", { placeholder: ex.kind === "use" ? "Write your sentence here…" : "写出意思" });
    const btn = h("button", { text: "提交", onclick: () => submit(input.value) });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (ex.kind === "spell" || e.ctrlKey)) {
        e.preventDefault();
        btn.click();
      }
    });
    parts.push(input, h("div", { class: "row" }, btn, h("span", { class: "note", text: ex.kind === "spell" ? "回车提交" : "Ctrl+回车提交" })));
    setTimeout(() => input.focus(), 0);
  }
  parts.push(out);
  box.replaceChildren(...parts);
}

function showDone() {
  const msg = stats.answered
    ? `完成！本次练习 ${stats.answered} 题，答对 ${stats.right} 题。`
    : context === "postwatch"
      ? "这个视频还没有收藏的单词。在侧边栏点生词，选「收藏学习」，课后就能在这里练习。"
      : "今天没有需要复习的单词了 🎉";
  const parts = [h("p", { class: "done", text: msg })];
  if (stats.graduated.length) parts.push(h("p", { text: `🎓 毕业的单词：${stats.graduated.join("、")}` }));
  $("exercise").replaceChildren(...parts);
  refreshStatus();
}

async function refreshStatus() {
  try {
    const s = await api("/api/review/summary");
    $("status").textContent = `待复习 ${s.due} 个 · 共 ${s.total} 个单词在学 · 今天已练习 ${s.reviewed_today} 次`
      + (s.waiting_for_gloss ? ` · ${s.waiting_for_gloss} 个词还在等待 AI 释义` : "");
  } catch (err) {
    $("status").textContent = err.message;
  }
}

async function init() {
  if (video) {
    $("page-title").textContent = "课后测验";
    $("practice-title").textContent = "本视频的单词";
    try {
      await loadComprehension();
    } catch (err) {
      console.error(err);
    }
  }
  await refreshStatus();
  await nextExercise();
}

init();
