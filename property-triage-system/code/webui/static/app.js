"use strict";

/* ----------------------------- Tabs ----------------------------- */
const tabs = document.querySelectorAll(".tab");
const panels = document.querySelectorAll(".panel");
tabs.forEach((t) => {
  t.addEventListener("click", () => {
    tabs.forEach((x) => x.classList.remove("active"));
    panels.forEach((p) => p.classList.remove("active"));
    t.classList.add("active");
    document.getElementById("tab-" + t.dataset.tab).classList.add("active");
    const pt = document.getElementById("page-title");
    if (pt) pt.textContent = t.dataset.title || t.textContent.trim();
    if (t.dataset.tab === "dashboard") loadDashboard();
  });
});

/* --------------------------- Helpers ---------------------------- */
function esc(s) {
  return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
// Safe mini-markdown: esc() runs FIRST so any raw HTML in the source is inert,
// then we add headings / bold / lists. Used for both chat replies and the
// listing brief — so no un-escaped server HTML is ever injected (no XSS).
function mdLite(t) {
  let h = esc(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const lines = h.split("\n");
  const out = [];
  let inList = false;
  const closeList = () => { if (inList) { out.push("</ul>"); inList = false; } };
  for (const ln of lines) {
    const head = ln.match(/^(#{1,6})\s+(.*)/);
    const item = ln.match(/^\s*[-*]\s+(.*)/);
    if (head) {
      closeList();
      const lvl = Math.min(head[1].length + 1, 4); // # → h2, ## → h3 …
      out.push(`<h${lvl}>${head[2]}</h${lvl}>`);
    } else if (item) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push("<li>" + item[1] + "</li>");
    } else {
      closeList();
      if (ln.trim()) out.push("<p>" + ln + "</p>");
    }
  }
  closeList();
  return out.join("");
}
function tableHTML(rows, cols) {
  if (!rows || !rows.length) return "";
  const head = "<tr>" + cols.map((c) => `<th>${c.label}</th>`).join("") + "</tr>";
  const body = rows
    .map((r) => "<tr>" + cols.map((c) => {
      // c.fmt returns a fixed, developer-defined string (e.g. ✓/✗) → trusted;
      // the default path escapes the raw cell value.
      const cell = c.fmt ? c.fmt(r[c.key]) : esc(String(r[c.key] ?? "—"));
      return `<td>${cell}</td>`;
    }).join("") + "</tr>")
    .join("");
  return `<div class="table-wrap"><table>${head}${body}</table></div>`;
}
// Guardrail pass/fail → glyph (null/undefined → em-dash for "n/a")
const passFmt = (v) => (v === true ? "✓" : v === false ? "✗" : "—");

/* ---------------------------- Chat ------------------------------ */
const chatWindow = document.getElementById("chat-window");
const chatForm = document.getElementById("chat-form");
const chatText = document.getElementById("chat-text");
const chatSend = document.getElementById("chat-send");
const chatClear = document.getElementById("chat-clear");
const suggestionsEl = document.getElementById("suggestions");
const SUGGESTIONS = [
  "Which listings need renovation?",
  "What properties do you have in Ramat Gan?",
  "What should I check when viewing an apartment?",
  "Explain the steps of buying a property.",
];
let messages = [];

function bubbleEl(role, html) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  const avatar = role === "user" ? "🧑" : "🏠";
  wrap.innerHTML = `<div class="avatar">${avatar}</div><div class="bubble" dir="auto">${html}</div>`;
  return wrap;
}
function scrollChat() { chatWindow.scrollTop = chatWindow.scrollHeight; }

function renderSuggestions() {
  suggestionsEl.innerHTML = "";
  if (messages.length) return;
  SUGGESTIONS.forEach((s) => {
    const c = document.createElement("button");
    c.type = "button"; c.className = "chip"; c.textContent = s;
    c.addEventListener("click", () => sendMessage(s));
    suggestionsEl.appendChild(c);
  });
}

function greet() {
  chatWindow.appendChild(
    bubbleEl(
      "assistant",
      mdLite(
        "Hi! I'm your real-estate assistant. I can answer questions about the listings in the system, explain the buying/renting process, and help with what to check when viewing a property.\n\nPick a question below or type your own."
      )
    )
  );
}

let chatBusy = false;

function setChatBusy(busy) {
  chatBusy = busy;
  chatText.disabled = busy;
  chatSend.disabled = busy;
}

async function sendMessage(text) {
  text = (text || "").trim();
  if (!text || chatBusy) return; // ignore re-entrancy while a reply is streaming
  setChatBusy(true);

  messages.push({ role: "user", content: text });
  chatWindow.appendChild(bubbleEl("user", esc(text)));
  suggestionsEl.innerHTML = "";
  chatText.value = "";
  scrollChat();

  const assistantWrap = bubbleEl("assistant", "<span class='typing'>…</span>");
  chatWindow.appendChild(assistantWrap);
  const bubble = assistantWrap.querySelector(".bubble");
  scrollChat();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ history: messages }),
    });
    if (!resp.ok || !resp.body) throw new Error("HTTP " + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let acc = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      acc += decoder.decode(value, { stream: true });
      bubble.innerHTML = mdLite(acc);
      scrollChat();
    }
    acc += decoder.decode(); // flush any trailing multi-byte char (Hebrew)
    if (acc.trim()) {
      bubble.innerHTML = mdLite(acc);
      messages.push({ role: "assistant", content: acc }); // persist only a real reply
    } else {
      bubble.innerHTML = "<em>No response — please try again.</em>";
      messages.pop(); // drop the user turn so history stays clean
    }
  } catch (e) {
    bubble.innerHTML = "<em>⚠️ Couldn't reach the assistant — please try again.</em>";
    messages.pop(); // failed turn is NOT added to history (no polluted context)
  } finally {
    setChatBusy(false);
    chatText.focus();
  }
}

chatForm.addEventListener("submit", (e) => { e.preventDefault(); sendMessage(chatText.value); });
if (chatClear) {
  chatClear.addEventListener("click", () => {
    if (chatBusy) return; // don't wipe the conversation mid-stream
    messages = [];
    chatWindow.innerHTML = "";
    greet();
    renderSuggestions();
  });
}
greet();
renderSuggestions();

/* --------------------------- Submit ----------------------------- */
const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const previews = document.getElementById("previews");
const submitForm = document.getElementById("submit-form");
const resultEl = document.getElementById("result");
let selectedFiles = [];
let previewUrls = []; // object URLs currently shown — revoked before re-render to avoid leaks

dropzone.addEventListener("click", () => fileInput.click());
dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
dropzone.addEventListener("drop", (e) => {
  e.preventDefault(); dropzone.classList.remove("drag");
  addFiles(e.dataTransfer.files);
});
fileInput.addEventListener("change", () => addFiles(fileInput.files));

function addFiles(fileList) {
  for (const f of fileList) {
    if (!f.type.startsWith("image/")) continue;
    // de-dupe: same name + size + lastModified ⇒ already picked
    if (selectedFiles.some((g) => g.name === f.name && g.size === f.size && g.lastModified === f.lastModified)) continue;
    selectedFiles.push(f);
  }
  renderPreviews();
}
function renderPreviews() {
  previewUrls.forEach(URL.revokeObjectURL); // release the previous batch first
  previewUrls = [];
  previews.innerHTML = "";
  selectedFiles.forEach((f) => {
    const url = URL.createObjectURL(f);
    previewUrls.push(url);
    const div = document.createElement("div");
    div.className = "preview";
    div.innerHTML = `<img src="${url}" alt=""><span>${esc(f.name)}</span>`;
    previews.appendChild(div);
  });
}
function resetSubmitForm() {
  previewUrls.forEach(URL.revokeObjectURL);
  previewUrls = [];
  selectedFiles = [];
  previews.innerHTML = "";
  fileInput.value = "";
  document.getElementById("desc").value = "";
  document.getElementById("agent").value = "";
}

submitForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const description = document.getElementById("desc").value.trim();
  const agent = document.getElementById("agent").value.trim();
  if (!description) { resultEl.innerHTML = `<div class="card"><span class="pill pill-bad">Please enter a property description.</span></div>`; return; }
  const btn = document.getElementById("submit-btn");
  btn.disabled = true; btn.textContent = "Processing…";
  try {
    const resp = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, agent_name: agent, images: selectedFiles.map((f) => f.name) }),
    });
    const data = await resp.json();
    renderResult(data);
    // On a successful (non-rejected) submission, clear the form + previews so the
    // next listing starts clean and the old images aren't re-sent.
    if (!data.error && data.status !== "rejected") resetSubmitForm();
  } catch (e) {
    resultEl.innerHTML = `<div class="card"><span class="pill pill-bad">Request failed.</span></div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Submit listing";
  }
});

function renderResult(d) {
  if (d.error) { resultEl.innerHTML = `<div class="card"><span class="pill pill-bad">${esc(d.error)}</span></div>`; return; }
  const status = d.status || "ok";

  // Rejected by the guardrail: show the reason and STOP — never display the
  // blocked brief or any tables for content the pipeline refused to publish.
  if (status === "rejected") {
    resultEl.innerHTML = `<div class="card">
      <div class="status-line"><span>Submission rejected</span> <span class="pill pill-bad">blocked by guardrail</span></div>
      <p class="panel-intro">${esc(d.reason || "This submission was not accepted.")}</p>
    </div>`;
    return;
  }

  let pill = `<span class="pill pill-ok">routed → ${esc(d.routing || "—")}</span>`;
  if (status === "review") pill = `<span class="pill pill-review">held for review</span>`;

  const guard = d.guardrail || {};
  const imgs = tableHTML(d.images, [
    { key: "room_type", label: "Room" }, { key: "condition_score", label: "Condition" }, { key: "confidence", label: "Confidence" },
  ]);
  const sims = tableHTML(d.similar_listings, [
    { key: "id", label: "ID" }, { key: "text", label: "Listing" }, { key: "score", label: "Score" },
  ]);

  resultEl.innerHTML = `
    <div class="card">
      <div class="status-line"><span>Listing processed</span> ${pill}</div>
      ${d.brief_markdown ? `<div class="brief">${mdLite(d.brief_markdown)}</div>` : ""}
      ${imgs ? `<h3 class="chart-title" style="margin-top:18px">Image analysis</h3>${imgs}` : ""}
      ${sims ? `<h3 class="chart-title" style="margin-top:18px">Similar listings</h3>${sims}` : ""}
      <p class="panel-intro" style="margin-top:14px">Guardrails — input: ${passFmt(guard.input_pass)} · output: ${passFmt(guard.output_pass)} · exec: ${d.exec_ms ?? "?"} ms</p>
    </div>`;
}

/* -------------------------- Dashboard --------------------------- */
// chart colors are read from the active theme (CSS vars) inside loadDashboard()
const charts = {};
function makeChart(id, cfg) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), cfg);
}

async function loadDashboard() {
  let d;
  try { d = await (await fetch("/api/dashboard")).json(); }
  catch (e) { return; }

  const _c = getComputedStyle(document.documentElement);
  const _v = (n, f) => (_c.getPropertyValue(n).trim() || f);
  const CHARCOAL = _v("--ink", "#211a12"), CLAY = _v("--taupe", "#a98c63"),
        AMBER = _v("--sand", "#b8a37e"), GREEN = _v("--sage", "#8f9e84"), RED = _v("--bad", "#a8584a"),
        ACCENT = _v("--accent", "#936a35");

  document.getElementById("dash-note").textContent = d.is_sample ? "Showing sample data (no real submissions yet)." : "";

  const m = d.metrics;
  document.getElementById("metrics").innerHTML = [
    ["Listings processed", m.total],
    ["Guardrail rejection rate", m.rejection_rate + "%"],
    ["Avg. condition score", m.avg_condition ?? "—"],
    ["Avg. exec time", m.avg_exec_ms != null ? m.avg_exec_ms + " ms" : "—"],
  ].map(([l, v]) => `<div class="metric"><div class="m-label">${l}</div><div class="m-value">${v}</div></div>`).join("");

  const baseOpts = { responsive: true, plugins: { legend: { display: false } } };

  makeChart("chart-routing", {
    type: "bar",
    data: { labels: Object.keys(d.routing), datasets: [{ data: Object.values(d.routing), backgroundColor: ACCENT, borderRadius: 6 }] },
    options: baseOpts,
  });
  makeChart("chart-exec", {
    type: "line",
    data: { labels: d.exec_series.map((_, i) => i + 1), datasets: [{ data: d.exec_series.map((e) => e.exec_ms), borderColor: CLAY, backgroundColor: CLAY + "22", fill: true, tension: .3, pointRadius: 3 }] },
    options: baseOpts,
  });
  makeChart("chart-cond", {
    type: "bar",
    data: { labels: d.cond_series.map((_, i) => i + 1), datasets: [{ data: d.cond_series.map((e) => e.avg_condition), backgroundColor: AMBER, borderRadius: 6 }] },
    options: { ...baseOpts, scales: { y: { min: 0, max: 5 } } },
  });
  makeChart("chart-outcomes", {
    type: "doughnut",
    data: { labels: Object.keys(d.outcomes), datasets: [{ data: Object.values(d.outcomes), backgroundColor: [GREEN, AMBER, RED, ACCENT] }] },
    options: { responsive: true, plugins: { legend: { position: "bottom" } } },
  });

  document.getElementById("recent-table").innerHTML = tableHTML(d.recent, [
    { key: "ts", label: "Time" }, { key: "agent", label: "Agent" }, { key: "property_type", label: "Type" },
    { key: "location", label: "Location" }, { key: "routing", label: "Team" }, { key: "status", label: "Status" },
    { key: "input_pass", label: "Input", fmt: passFmt }, { key: "output_pass", label: "Output", fmt: passFmt },
    { key: "avg_condition", label: "Avg cond." }, { key: "exec_ms", label: "ms" },
  ]);
}

/* ----------------------------- Theme palette switcher ----------------------------- */
(function () {
  const sw = document.querySelector(".theme-switch");
  const btn = document.getElementById("theme-btn");
  const menu = document.getElementById("theme-menu");
  if (!btn || !menu) return;
  const saved = localStorage.getItem("pt-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  const markActive = (t) =>
    menu.querySelectorAll(".theme-opt").forEach((o) => o.classList.toggle("active", (o.dataset.theme || "") === (t || "")));
  markActive(saved);
  btn.addEventListener("click", (e) => { e.stopPropagation(); menu.classList.toggle("hidden"); });
  menu.querySelectorAll(".theme-opt").forEach((o) =>
    o.addEventListener("click", () => {
      const t = o.dataset.theme;
      if (t) document.documentElement.dataset.theme = t;
      else delete document.documentElement.dataset.theme;
      localStorage.setItem("pt-theme", t);
      markActive(t);
      menu.classList.add("hidden");
      if (document.getElementById("tab-dashboard").classList.contains("active")) loadDashboard();
    })
  );
  document.addEventListener("click", (e) => { if (sw && !sw.contains(e.target)) menu.classList.add("hidden"); });
})();
