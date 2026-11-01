/* =====================================================================
   Sahyog Care: the PATIENT site's behaviour (plain JavaScript, no framework).
   Doctors and the front desk have their own pages (doctor.js, desk.js).

   Parts of this file:
    1 helpers         4 hero demo         7 chat on the page
    2 motion          5 carousel          8 records
    3 who is signed in 6 voice            9 doctors  ·  10 start-up
   ===================================================================== */
"use strict";

/* ---------- 1. helpers ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const icon = (name) => `<svg class="icon"><use href="#i-${name}"/></svg>`;

// Everything the page remembers while it is open lives in this one object.
const state = { thread: null, speak: true };

/** Call the backend with the signed-in person's token (auth.js builds the headers). */
async function api(path, { method = "GET", body, form } = {}) {
  const headers = { ...window.SahyogAuth.headers() };
  let payload;
  if (form) payload = form;
  else if (body) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* the body was not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function toast(message, bad = false) {
  const t = Object.assign(document.createElement("div"), { className: "toast" + (bad ? " bad" : ""), textContent: message });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

const fmtTime = (s) => new Date(s.replace(" ", "T")).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });

/* ---------- 2. motion: scroll reveals, count-up numbers, parallax, spotlight, nav ---------- */
function initMotion() {
  const nav = $("#nav");
  const heroDevice = $("#heroDevice");
  let ticking = false;
  addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {                 // batch work into one frame so scrolling stays smooth
      nav.classList.toggle("scrolled", scrollY > 8);
      if (!reduceMotion && scrollY < 900) heroDevice.style.setProperty("--py", `${scrollY * -0.1}px`);
      ticking = false;
    });
  }, { passive: true });

  // Fade things in as they scroll into view.
  const reveal = new IntersectionObserver((entries) => {
    entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); reveal.unobserve(e.target); } });
  }, { threshold: 0.12 });
  $$(".reveal").forEach((n) => reveal.observe(n));

  // Count numbers up from zero when they appear.
  const counter = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      counter.unobserve(e.target);
      const target = +e.target.dataset.count, suffix = e.target.dataset.suffix || "";
      if (reduceMotion) { e.target.textContent = target.toLocaleString() + suffix; return; }
      const start = performance.now(), duration = 1500;
      (function frame(now) {
        const t = Math.min(1, (now - start) / duration), eased = 1 - Math.pow(1 - t, 4);   // fast start, soft landing
        e.target.textContent = Math.round(target * eased).toLocaleString() + suffix;
        if (t < 1) requestAnimationFrame(frame);
      })(start);
    });
  }, { threshold: 0.6 });
  $$("[data-count]").forEach((n) => counter.observe(n));

  // The soft light on cards follows the pointer.
  document.addEventListener("pointermove", (e) => {
    const card = e.target.closest?.(".card");
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - r.left}px`);
    card.style.setProperty("--my", `${e.clientY - r.top}px`);
  });
}

/* ---------- 3. who is signed in ---------- */
// auth.js sends staff to their own portals. Here we only react to a patient signing in or out.
function initAuthEvents() {
  addEventListener("sahyog:auth", () => {
    state.thread = null;
    $("#messages").innerHTML = "";
    greet();
    refreshDocs();
    loadVisits();
  });
}

/* ---------- 4. the animated phone in the hero ---------- */
async function heroDemo() {
  const box = $("#heroChat");
  const script = [
    ["user", "My lower back has hurt for three weeks."],
    ["bot", "I suggest Orthopaedics. Here are your options."],
    ["opt", "Dr. Mehta · Baner<small>Today, 6:30 PM</small>"],
    ["user", "The first one, please."],
    ["bot", "Booked. A text message confirmation is on its way."],
  ];
  const add = ([kind, text]) => {
    const node = document.createElement("div");
    node.className = kind === "opt" ? "option" : `bubble ${kind}`;
    node.style.maxWidth = kind === "opt" ? "100%" : "";
    node.innerHTML = kind === "opt" ? `<b>${text}</b>` : esc(text);
    box.append(node);
  };
  if (reduceMotion) { script.forEach(add); return; }
  for (;;) {                                  // loop forever, restarting after a pause
    box.innerHTML = "";
    for (const line of script) { await sleep(1400); add(line); }
    await sleep(4200);
  }
}

/* ---------- 5. feature carousel (scroll-snap does the sliding; we add buttons, dots and autoplay) ---------- */
function initCarousel() {
  const track = $("#featureTrack"), slides = [...track.children], dots = $("#carDots");
  let index = 0, timer;
  slides.forEach((_, i) => {
    const d = Object.assign(document.createElement("button"), { className: "dot", ariaLabel: `Slide ${i + 1}` });
    d.onclick = () => { go(i); restart(); };
    dots.append(d);
  });
  const go = (i) => {
    index = (i + slides.length) % slides.length;
    const s = slides[index];
    track.scrollTo({ left: s.offsetLeft - (track.clientWidth - s.clientWidth) / 2, behavior: reduceMotion ? "auto" : "smooth" });
  };
  const paint = () => {                       // work out which slide is nearest the centre
    const mid = track.scrollLeft + track.clientWidth / 2;
    const nearest = slides.reduce((best, s, i) => Math.abs(s.offsetLeft + s.clientWidth / 2 - mid) < Math.abs(slides[best].offsetLeft + slides[best].clientWidth / 2 - mid) ? i : best, 0);
    index = nearest;
    $$(".dot", dots).forEach((d, i) => d.classList.toggle("on", i === nearest));
  };
  track.addEventListener("scroll", () => requestAnimationFrame(paint), { passive: true });
  $("#carPrev").onclick = () => { go(index - 1); restart(); };
  $("#carNext").onclick = () => { go(index + 1); restart(); };
  const restart = () => { clearInterval(timer); if (!reduceMotion) timer = setInterval(() => go(index + 1), 5500); };
  const box = $("#featureCarousel");
  box.addEventListener("pointerenter", () => clearInterval(timer));
  box.addEventListener("pointerleave", restart);
  box.addEventListener("focusin", () => clearInterval(timer));
  paint(); restart();
}

/* ---------- 6. voice: the shared engine lives in bot.js (window.SahyogVoice) ---------- */
function stopSpeaking() { window.SahyogVoice.stop(); }

/** Speak a reply sentence by sentence. The first sentence starts while the rest are still being generated. */
function speak(text, agent) {
  if (!state.speak) return;
  return window.SahyogVoice.speak(text, agent, {
    onUnavailable: () => { $("#voiceNote").textContent = "Voice is not available right now. Replies stay on screen."; },
  });
}

/* ---------- 7. the chat on the page (the round bot in the corner uses the same service) ---------- */
const messages = () => $("#messages");

function addBubble(role, html, extraClass = "") {
  const b = document.createElement("div");
  b.className = `bubble ${role} ${extraClass}`;
  b.innerHTML = html;
  messages().append(b);
  messages().scrollTop = messages().scrollHeight;
  return b;
}

function greet() {
  addBubble("bot", "Hello. Tell me what is wrong, or ask about a report. You can type, or talk to the round assistant in the corner.");
}

const agentFor = (res) => (res.outcome === "emergency" ? "emergency" : res.citations?.length ? "records" : "triage");

async function send(text) {
  if (!text) return;
  stopSpeaking();
  $("#msgInput").value = "";
  addBubble("user", esc(text));
  const typing = addBubble("bot", '<span class="typing"><i></i><i></i><i></i></span>');
  try {
    const res = await api("/api/chat", { method: "POST", body: { message: text, thread_id: state.thread } });
    typing.remove();
    state.thread = res.thread_id;
    renderReply(res);
  } catch (err) {
    typing.remove();
    if (err.status === 401) {
      const b = addBubble("bot", "Please sign in so I know who you are.<div style='margin-top:8px'><button class='btn small' type='button'>Sign in</button></div>");
      $(".btn", b).onclick = () => window.SahyogAuth.openModal();
    } else addBubble("bot", esc(err.message), "emergency");
  }
}

function renderReply(res) {
  const sources = (res.reply.match(/\(Source: ([^)]*)\)/) || [])[1];
  const clean = res.reply.replace(/\s*\(Source:[^)]*\)/, "");
  const chips = sources ? sources.split("; ").map((s) => `<span class="src">${esc(s)}</span>`).join("") : "";
  addBubble("bot", esc(clean) + (chips ? `<div>${chips}</div>` : ""), res.outcome === "emergency" ? "emergency" : "");
  if (res.options?.length) renderOptions(res.options, res.thread_id);
  if (/^Booked/.test(res.reply)) loadVisits();            // a new booking: show it under My visits
  speak(res.reply, agentFor(res));
}

function renderOptions(options, thread) {
  const row = document.createElement("div");
  row.className = "options";
  options.forEach((o, i) => {
    const card = document.createElement("div");
    card.className = "option";
    card.style.animationDelay = `${i * 110}ms`;
    card.innerHTML = `<b>${esc(o.name)}</b><small>${esc(o.clinic)} · ${esc(o.specialty)}<br>${esc(fmtTime(o.starts_at))}</small>`;
    const btn = Object.assign(document.createElement("button"), { className: "btn small", textContent: "Book this" });
    btn.onclick = async () => {
      $$(".option .btn", row).forEach((b) => (b.disabled = true));
      stopSpeaking();
      try {
        const res = await api("/api/chat/resume", { method: "POST", body: { thread_id: thread, slot_id: o.slot_id } });
        row.style.opacity = ".45";
        addBubble("user", `Book ${esc(o.name)}`);
        renderReply(res);
      } catch (err) { toast(err.message, true); $$(".option .btn", row).forEach((b) => (b.disabled = false)); }
    };
    card.append(btn);
    row.append(card);
  });
  messages().append(row);
  messages().scrollTop = messages().scrollHeight;
}

function initChat() {
  greet();
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#msgInput").value.trim()); });
  $("#speakToggle").addEventListener("change", (e) => { state.speak = e.target.checked; if (!state.speak) stopSpeaking(); });
  $$("#quickChips .chip").forEach((c) => c.addEventListener("click", () => {
    state.thread = null;                      // each example starts a fresh conversation
    send(c.dataset.q);
  }));
  $("#heroTalk").addEventListener("click", () => setTimeout(() => $("#msgInput").focus(), 700));
}

/* ---------- 7b. my visits: coming up, history, doctor's notes ---------- */
let visitData = null, visitTab = "upcoming";

async function loadVisits() {
  const list = $("#visitList");
  try {
    visitData = await api("/api/my/visits");
    drawVisits();
  } catch (err) {
    visitData = null;
    $("#visitNotes").innerHTML = "";
    list.innerHTML = err.status === 401
      ? '<div class="visit-empty">Sign in to see your visits and follow-ups.<div style="margin-top:12px"><button class="btn small" id="visitSignIn" type="button">Sign in</button></div></div>'
      : `<div class="visit-empty">${esc(err.message)}</div>`;
    $("#visitSignIn")?.addEventListener("click", () => window.SahyogAuth.openModal());
  }
}

function drawVisits() {
  const rows = visitData[visitTab];
  const day = (s) => new Date(s.replace(" ", "T")).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
  const hour = (s) => new Date(s.replace(" ", "T")).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  $("#visitList").innerHTML = rows.length ? rows.map((v, i) => `<div class="visit-row" style="animation-delay:${i * 60}ms">
      <div class="when">${esc(hour(v.starts_at))}<small>${esc(day(v.starts_at))}</small></div>
      <div><b>${esc(v.doctor_name)} ${v.kind === "followup" ? '<span class="pill info">Follow-up</span>' : ""}</b>
        <div class="sub">${esc(v.specialty)} · ${esc(v.clinic)}${v.reason ? ` · ${esc(v.reason)}` : ""}</div></div>
      ${visitTab === "upcoming" ? `<button class="btn small ghost" data-cancel="${v.id}">Cancel</button>` : ""}</div>`).join("")
    : `<div class="visit-empty">${visitTab === "upcoming" ? "Nothing booked. Tell the assistant what is wrong and it will find you a doctor." : "No past visits yet."}</div>`;
  $$("[data-cancel]").forEach((b) => b.onclick = () => cancelVisit(b));
  $("#visitNotes").innerHTML = visitTab === "past" && visitData.notes.length ? visitData.notes.map((n) => `<div class="note-card"><h3>${esc(n.doctor_name)} · ${esc((n.signed_at || "").slice(0, 10))}</h3>
      <div>${esc(n.assessment)}</div>${n.plan.length ? `<ul>${n.plan.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}</div>`).join("") : "";
}

/** Two taps, so a slip of the finger does not cancel a visit. */
function cancelVisit(button) {
  if (button.dataset.sure) {
    api(`/api/my/appointments/${button.dataset.cancel}/cancel`, { method: "POST" })
      .then(() => { toast("Visit cancelled."); loadVisits(); }).catch((err) => toast(err.message, true));
    return;
  }
  button.dataset.sure = "1"; button.textContent = "Tap again to cancel"; button.classList.remove("ghost"); button.classList.add("danger");
  setTimeout(() => { if (button.isConnected) { delete button.dataset.sure; button.textContent = "Cancel"; button.classList.add("ghost"); button.classList.remove("danger"); } }, 4000);
}

function initVisits() {
  $("#visitTabs").addEventListener("click", (e) => {
    const tab = e.target.closest(".tab"); if (!tab) return;
    $$("#visitTabs .tab").forEach((t) => t.classList.toggle("on", t === tab));
    visitTab = tab.dataset.v;
    if (visitData) drawVisits();
  });
  loadVisits();
}

/* ---------- 8. records ---------- */
async function refreshDocs() {
  const list = $("#docList");
  try {
    const docs = await api("/api/records/documents");
    list.innerHTML = docs.length ? docs.map((d) => `<li>${icon("file")}<span>${esc(d.filename)}</span>${statusPill(d.status, d.pages)}</li>`).join("")
      : '<li style="color:var(--muted)">No documents yet. Upload one above.</li>';
  } catch (err) {
    list.innerHTML = `<li style="color:var(--muted)">${err.status === 401 ? "Sign in to see and upload your reports." : esc(err.message)}</li>`;
  }
}
const statusPill = (status, pages) =>
  status === "ready" ? `<span class="pill ok">ready · ${pages} page${pages === 1 ? "" : "s"}</span>`
    : status.startsWith("failed") ? `<span class="pill bad" title="${esc(status)}">failed</span>` : `<span class="pill warn">${esc(status)}…</span>`;

async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  try {
    const doc = await api("/api/records/upload", { method: "POST", form });
    toast(`Uploaded ${doc.filename}. Reading it now.`);
    for (let i = 0; i < 20; i++) {             // poll the status until the background job finishes
      await refreshDocs();
      const s = await api(`/api/records/status/${doc.doc_id}`);
      if (s.status === "ready" || s.status.startsWith("failed")) break;
      await sleep(700);
    }
    await refreshDocs();
  } catch (err) { if (err.status === 401) window.SahyogAuth.openModal(); else toast(err.message, true); }
}

function initRecords() {
  const drop = $("#drop"), input = $("#fileInput");
  drop.onclick = () => input.click();
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") input.click(); };
  input.onchange = () => input.files[0] && uploadFile(input.files[0]);
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && uploadFile(e.dataTransfer.files[0]));
  $("#recAsk").onclick = askRecord;
  $("#recQ").addEventListener("keydown", (e) => e.key === "Enter" && askRecord());
  $$("[data-rq]").forEach((c) => c.addEventListener("click", () => { $("#recQ").value = c.dataset.rq; askRecord(); }));
  refreshDocs();
}

async function askRecord() {
  const out = $("#recAnswer");
  out.innerHTML = '<div class="answer"><span class="typing"><i></i><i></i><i></i></span></div>';
  try {
    const res = await api("/api/records/ask", { method: "POST", body: { question: $("#recQ").value } });
    const sources = (res.reply.match(/\(Source: ([^)]*)\)/) || [])[1];
    const clean = res.reply.replace(/\s*\(Source:[^)]*\)/, "");
    out.innerHTML = `<div class="answer ${res.outcome === "done" ? "" : "bad"}"><p>${esc(clean)}</p>` +
      (sources ? sources.split("; ").map((s) => `<span class="bubble src" style="display:inline-block;margin:8px 8px 0 0;padding:3px 10px;font-size:12px;animation:none">${esc(s)}</span>`).join("") : "") + "</div>";
    speak(res.reply, "records");
  } catch (err) {
    out.innerHTML = `<div class="answer bad"><p>${err.status === 401 ? "Please sign in to ask about your reports." : esc(err.message)}</p></div>`;
    if (err.status === 401) window.SahyogAuth.openModal();
  }
}

/* ---------- 9. doctors ---------- */
async function initDoctors() {
  const all = await api("/api/doctors");
  const specialties = ["All", ...new Set(all.map((d) => d.specialty))];
  const filters = $("#docFilters"), grid = $("#docGrid");
  filters.innerHTML = specialties.map((s, i) => `<button class="chip ${i === 0 ? "on" : ""}" data-s="${esc(s)}">${esc(s)}</button>`).join("");
  const draw = (specialty) => {
    const list = specialty === "All" ? all : all.filter((d) => d.specialty === specialty);
    grid.innerHTML = list.map((d, i) => {
      const initials = d.name.replace("Dr. ", "").split(" ").map((w) => w[0]).join("").slice(0, 2);
      return `<article class="card doc" data-id="${d.id}" tabindex="0" style="animation:bubbleIn .6s var(--ease) both;animation-delay:${Math.min(i, 12) * 40}ms">
        <div class="avatar">${esc(initials)}</div><b>${esc(d.name)}</b><small>${esc(d.specialty)}</small><small>${esc(d.clinic)} · ${esc(d.languages)}</small>
        <div class="slots"></div></article>`;
    }).join("");
  };
  draw("All");
  filters.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    $$(".chip", filters).forEach((c) => c.classList.toggle("on", c === chip));
    draw(chip.dataset.s);
  });
  const toggle = async (card) => {
    card.classList.toggle("open");
    const box = $(".slots", card);
    if (!box.dataset.loaded) {
      const slots = await api(`/api/doctors/${card.dataset.id}/slots?limit=4`);
      box.innerHTML = slots.map((s) => `<span class="slot-pill">${esc(fmtTime(s.starts_at))}</span>`).join("") || "<small>No free times</small>";
      box.dataset.loaded = "1";
    }
  };
  grid.addEventListener("click", (e) => { const c = e.target.closest(".doc"); if (c) toggle(c); });
  grid.addEventListener("keydown", (e) => { if (e.key === "Enter") { const c = e.target.closest(".doc"); if (c) toggle(c); } });
}

/* ---------- 10. start-up ---------- */
function init() {
  initMotion();
  initCarousel();
  initChat();
  initAuthEvents();
  initRecords();
  initVisits();
  initDoctors().catch((err) => toast("Cannot reach the server: " + err.message, true));
  heroDemo();
}
init();
