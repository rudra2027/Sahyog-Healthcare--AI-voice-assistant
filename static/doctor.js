/* =====================================================================
   Doctor portal: today's schedule, a patient's details and reports, and the consultation note.
   Every number here comes from API calls that only a signed-in doctor may make, and a doctor can only
   open patients who have a booking with them.
   ===================================================================== */
(() => {
"use strict";
const { $, $$, esc, toast, api, clock, dayLabel, fmtTime, reduceMotion } = window.Portal;

const state = { day: "today", appts: [], patient: null, thread: null, doctor: null };
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

/* ---------- the top of the page: greeting and the day at a glance ---------- */
async function loadSummary() {
  const [today, tomorrow, patients, reports, messages] = await Promise.all([api("/api/doctor/appointments?day=today"), api("/api/doctor/appointments?day=tomorrow"), api("/api/doctor/patients"), api("/api/doctor/reports"), api("/api/doctor/messages?unread=true")]);
  const now = new Date(), hour = now.getHours();
  const next = today.find((a) => window.Portal.parse(a.starts_at) >= now);
  $("#today").textContent = now.toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });
  $("#hello").textContent = `${hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening"}, ${state.doctor.name}`;
  $("#summary").textContent = today.length ? `You have ${today.length} appointment${today.length === 1 ? "" : "s"} today.` : "You have no appointments today.";
  const tile = (value, label) => `<div class="card tile reveal in"><b>${value}</b><span>${label}</span></div>`;
  $("#tiles").innerHTML = tile(today.length, "appointments today") + tile(tomorrow.length, "appointments tomorrow") +
    tile(next ? clock(next.starts_at) : "None", next ? `next: ${esc(next.patient_name)}` : "next appointment") + tile(reports.length, "new reports (2 weeks)");
  renderNewReports(reports);
  renderNewMessages(messages);
  return today.length;
}

function renderNewMessages(messages) {
  const list = $("#newMessages");
  list.innerHTML = messages.slice(0, 5).map((m) => `<li data-pid="${esc(m.patient_id)}" style="cursor:pointer"><svg class="icon"><use href="#i-user"/></svg><span><b>${esc(m.patient_name)}</b> <span class="pill info">message</span><br><small style="color:var(--muted)">${esc(m.text.length > 110 ? m.text.slice(0, 107) + "..." : m.text)}</small></span></li>`).join("");
  list.onclick = (e) => { const li = e.target.closest("li[data-pid]"); if (li) openPatient(li.dataset.pid); };
}

function renderNewReports(reports) {
  const list = $("#newReports");
  list.innerHTML = reports.length ? reports.slice(0, 6).map((r) => `<li data-pid="${esc(r.patient_id)}" style="cursor:pointer"><svg class="icon"><use href="#i-file"/></svg><span><b>${esc(r.patient_name)}</b><br><small style="color:var(--muted)">${esc(r.filename)} · ${esc(fmtTime(r.uploaded_at.slice(0, 16)))}</small></span>${r.status === "ready" ? '<span class="pill ok">ready</span>' : `<span class="pill warn">${esc(r.status)}</span>`}</li>`).join("")
    : '<li style="color:var(--muted)">Nothing uploaded in the last two weeks.</li>';
  list.onclick = (e) => { const li = e.target.closest("li[data-pid]"); if (li) openPatient(li.dataset.pid); };
}

/* ---------- schedule ---------- */
async function loadSchedule() {
  const list = $("#apptList");
  list.innerHTML = '<div class="empty">Loading...</div>';
  try {
    state.appts = await api(`/api/doctor/appointments?day=${state.day}`);
  } catch (err) { list.innerHTML = `<div class="empty">${esc(err.message)}</div>`; return; }
  if (!state.appts.length) { list.innerHTML = `<div class="empty">No appointments ${state.day === "today" ? "today" : state.day === "tomorrow" ? "tomorrow" : "coming up"}.</div>`; return; }
  const now = new Date(), firstUpcoming = state.appts.findIndex((a) => window.Portal.parse(a.starts_at) >= now);
  list.innerHTML = state.appts.map((a, i) => `<button class="appt ${i === firstUpcoming ? "next" : ""} ${state.patient?.id === a.patient_id ? "on" : ""}" data-pid="${esc(a.patient_id)}" style="animation-delay:${Math.min(i, 12) * 50}ms">
      <span class="t">${esc(clock(a.starts_at))}<small>${esc(dayLabel(a.starts_at))}</small></span>
      <span><b>${esc(a.patient_name)}${a.patient_age ? `, ${a.patient_age}` : ""}${i === firstUpcoming ? ' <span class="pill info">Next</span>' : ""}${a.kind === "followup" ? ' <span class="pill warn">Follow-up</span>' : ""}</b><span class="why">${esc(a.reason || "No reason given")}</span></span>
      <span class="chev" aria-hidden="true">&rsaquo;</span></button>`).join("");
}

/* ---------- patient card, reports and questions about them ---------- */
async function openPatient(id) {
  const panel = $("#patientPanel");
  panel.innerHTML = '<div class="placeholder"><span class="typing"><i></i><i></i><i></i></span></div>';
  try {
    const card = await api(`/api/doctor/patients/${encodeURIComponent(id)}`);
    state.patient = card; state.thread = null;
    window.SahyogContext = { patientId: card.id };   // lets the assistant know whose card is open ("what does the report say?")
    $$(".appt").forEach((b) => b.classList.toggle("on", b.dataset.pid === id));
    panel.innerHTML = `<h3>${esc(card.name)}</h3>
      <div class="meta">${card.age ? `${card.age} years old · ` : ""}speaks ${esc({ en: "English", hi: "Hindi", mr: "Marathi" }[card.language] || card.language || "English")}</div>
      <h4>Visits with you</h4>
      ${card.visits.map((v) => `<div class="visit"><b>${esc(fmtTime(v.starts_at))}${v.kind === "followup" ? " · follow-up" : ""}</b><small>${esc(v.reason || "No reason given")}</small></div>`).join("") || '<div class="visit">None</div>'}
      ${card.messages?.length ? `<h4>From the patient</h4>${card.messages.map((m) => `<div class="visit"><b>${esc(fmtTime(m.created_at.slice(0, 16)))}</b><small>${esc(m.text)}</small></div>`).join("")}` : ""}
      <h4>Reports on file</h4>
      <ul class="doclist">${card.documents.map((d) => `<li><svg class="icon"><use href="#i-file"/></svg><span>${esc(d.filename)}</span><small style="color:var(--muted)">${esc(fmtTime((d.uploaded_at || "").slice(0, 16) || "2000-01-01 00:00"))}</small>${d.status === "ready" ? `<span class="pill ok">${d.pages} page${d.pages === 1 ? "" : "s"}</span>` : `<span class="pill warn">${esc(d.status)}</span>`}</li>`).join("") || '<li style="color:var(--muted)">No reports uploaded.</li>'}</ul>
      <h4>Ask about the reports</h4>
      <div class="inline-form" style="margin-top:0"><input class="field" id="pq" placeholder="e.g. What was the LDL?" aria-label="Question about this patient's reports"><button class="btn small" id="pAsk">Ask</button></div>
      <div id="pAnswer"></div>
      <h4>Ask them to come back</h4>
      <div class="inline-form" style="margin-top:0"><select class="field" id="fuSlot" aria-label="Follow-up time"></select></div>
      <div class="inline-form"><input class="field" id="fuReason" value="Follow-up after tests" aria-label="Reason for the follow-up"><button class="btn small" id="fuBook">Schedule</button></div>
      <div class="actions"><button class="btn small ghost" id="writeNote">Write a note for ${esc(card.name.split(" ")[0])}</button></div>`;
    loadFollowUpSlots();
    $("#fuBook").onclick = scheduleFollowUp;
    $("#pAsk").onclick = askAboutPatient;
    $("#pq").addEventListener("keydown", (e) => e.key === "Enter" && askAboutPatient());
    $("#writeNote").onclick = () => { selectForNote(); $("#note").scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth" }); };
    selectForNote(true);
  } catch (err) { panel.innerHTML = `<div class="placeholder">${esc(err.message)}</div>`; }
}

async function loadFollowUpSlots() {
  const free = await api("/api/doctor/slots?limit=14");
  $("#fuSlot").innerHTML = free.length ? free.map((s) => `<option value="${s.id}">${esc(window.Portal.dayLabel(s.starts_at))}, ${esc(clock(s.starts_at))}</option>`).join("") : '<option value="">No free times</option>';
}

async function scheduleFollowUp() {
  const slot = $("#fuSlot").value;
  if (!slot) return toast("You have no free times to offer.", true);
  try {
    await api("/api/doctor/followups", { method: "POST", body: { patient_id: state.patient.id, slot_id: +slot, reason: $("#fuReason").value.trim() || "Follow-up visit" } });
    toast(`Follow-up booked for ${state.patient.name}. They can see it under My visits.`);
    await Promise.all([loadSchedule(), openPatient(state.patient.id)]);
  } catch (err) { toast(err.message, true); }
}

async function askAboutPatient() {
  const q = $("#pq").value.trim(), out = $("#pAnswer");
  if (!q) return;
  out.innerHTML = '<div class="answer"><span class="typing"><i></i><i></i><i></i></span></div>';
  try {
    const res = await api(`/api/doctor/patients/${encodeURIComponent(state.patient.id)}/ask`, { method: "POST", body: { question: q } });
    const sources = (res.reply.match(/\(Source: ([^)]*)\)/) || [])[1];
    const clean = res.reply.replace(/\s*\(Source:[^)]*\)/, "");
    out.innerHTML = `<div class="answer ${res.outcome === "done" ? "" : "bad"}"><p>${esc(clean)}</p>` +
      (sources ? sources.split("; ").map((s) => `<span class="pill" style="margin:8px 8px 0 0">${esc(s)}</span>`).join("") : "") + "</div>";
  } catch (err) { out.innerHTML = `<div class="answer bad"><p>${esc(err.message)}</p></div>`; }
}

/* ---------- consultation note (draft, review, sign) ---------- */
function selectForNote(quiet) {
  if (!state.patient) return;
  const tag = $("#noteFor"); tag.hidden = false; tag.textContent = state.patient.name;
  $("#noteLead").textContent = `Writing a note for ${state.patient.name}. Dictate or paste the consultation, then review the draft.`;
  if (!quiet) toast(`Note for ${state.patient.name}`);
}

function initScribe() {
  $("#sampleBtn").onclick = async () => { $("#transcript").value = (await api("/api/scribe/sample")).transcript; };
  $("#draftBtn").onclick = startDraft;
  const dictate = $("#dictateBtn");
  if (!Recognition) { dictate.disabled = true; dictate.title = "Dictation needs Chrome or Edge"; }
  else {
    const rec = new Recognition();
    rec.lang = "en-IN"; rec.continuous = true; rec.interimResults = false;
    rec.onresult = (e) => { const t = e.results[e.results.length - 1][0].transcript.trim(); $("#transcript").value += `${$("#transcript").value ? "\n" : ""}Doctor: ${t}`; };
    rec.onend = () => dictate.classList.remove("danger");
    dictate.onclick = () => { if (dictate.classList.contains("danger")) rec.stop(); else { dictate.classList.add("danger"); rec.start(); } };
  }
  refreshLessons();
}

async function startDraft() {
  if (!state.patient) return toast("Open a patient from your schedule first.", true);
  const text = $("#transcript").value.trim();
  if (text.length < 10) return toast("Add the consultation first (try Load sample).", true);
  $("#draftOut").innerHTML = '<span class="typing"><i></i><i></i><i></i></span>';
  try {
    const res = await api("/api/scribe/start", { method: "POST", body: { transcript: text, patient_id: state.patient.id } });
    state.thread = res.thread_id;
    renderDraft(res);
  } catch (err) { $("#draftOut").innerHTML = `<p style="color:var(--danger)">${esc(err.message)}</p>`; }
}

const stockPill = (s) => {
  const label = s.status === "in_stock" ? `${s.medicine}: in stock (${s.qty}) at ${s.clinic}` : s.status === "out_of_stock" ? `${s.medicine}: out of stock`
    : s.status === "needs_molecule" ? `${s.medicine}: choose a medicine first` : `${s.medicine}: stock unknown`;
  return `<span class="pill ${s.status === "in_stock" ? "ok" : s.status === "out_of_stock" ? "bad" : "warn"}">${esc(label)}</span>`;
};

function renderDraft(res) {
  const d = res.draft;
  const row = (letter, title, body, i) => `<div class="soap-row" style="animation-delay:${i * 90}ms"><div class="letter">${letter}</div><div><b>${title}</b><div>${body}</div></div></div>`;
  const plan = d.plan.map((line, i) => {
    const gap = line.includes("[");
    const suggested = line.includes("earlier notes");
    return `<div class="plan-line"><input data-plan="${i}" class="${gap ? "gap" : ""}" value="${esc(line)}" aria-label="Plan line ${i + 1}">${suggested ? `<button class="btn small ghost" data-accept="${i}">Accept</button>` : ""}</div>`;
  }).join("");
  $("#draftOut").innerHTML = `<div class="soap">${row("S", "Subjective", esc(d.subjective), 0)}${row("O", "Objective", esc(d.objective), 1)}${row("A", "Assessment", esc(d.assessment), 2)}${row("P", "Plan", plan, 3)}</div>
    <div class="stock">${res.stock.map(stockPill).join("")}</div>
    <p style="color:var(--muted);font-size:13px;margin-top:12px">Amber lines have a gap. Replace each [bracket] with your decision (for example "Atorvastatin 10 mg nightly"), then sign. Nothing is saved until you sign.</p>
    <div class="inline-form"><input class="field" id="rejectNote" placeholder="Comment if you reject, e.g. add the follow-up date"></div>
    <div class="actions"><button class="btn" id="signBtn">Sign note</button><button class="btn ghost" id="rejectBtn">Reject and redraft</button></div>`;
  $$("[data-accept]").forEach((b) => b.onclick = () => { const inp = $(`[data-plan="${b.dataset.accept}"]`); inp.value = inp.value.replace(/\s*\[[^\]]*\]/, ""); inp.classList.remove("gap"); b.remove(); });
  $("#signBtn").onclick = () => review("sign");
  $("#rejectBtn").onclick = () => review("reject");
}

async function review(action) {
  const plan = $$("[data-plan]").map((i) => i.value.trim()).filter(Boolean);
  try {
    const res = await api("/api/scribe/review", { method: "POST", body: { thread_id: state.thread, action, plan: action === "sign" ? plan : null, comments: $("#rejectNote")?.value || "" } });
    if (res.status === "waiting_for_review") { renderDraft(res); toast("Redrafted with your comments."); return; }
    const n = res.note, learned = n.learned || [];
    $("#draftOut").innerHTML = `<div class="answer"><h3><svg class="icon"><use href="#i-shield"/></svg> Signed. Note version ${n.version}</h3>
      <p style="color:var(--muted);font-size:14px">Saved and locked · reference <code>${esc((n.hash || "").slice(0, 12))}</code></p>
      <div class="stock">${(n.stock || []).map(stockPill).join("")}</div>
      ${learned.map((l) => `<p style="margin-top:12px"><span class="pill info">Noted</span> ${esc(l.key.replace("drug:", ""))} → ${esc(l.suggestion)}</p>`).join("")}</div>`;
    toast("Note signed and saved.");
    refreshLessons();
  } catch (err) { toast(err.message, true); }
}

async function refreshLessons() {
  try {
    const lessons = await api("/api/lessons");
    $("#lessons").innerHTML = lessons.length ? lessons.map((l) => `<div class="lesson"><span><b>${esc(l.key.replace("drug:", ""))}</b> → ${esc(l.suggestion)}</span>
      <span class="pill ${l.status === "active" ? "ok" : l.status === "ready_for_approval" ? "warn" : ""}">${l.status === "active" ? "on" : l.status === "ready_for_approval" ? "waiting for you" : "learning"}</span>
      ${l.status === "ready_for_approval" ? `<button class="btn small" data-approve="${esc(l.key.replace("drug:", ""))}">Turn on</button>` : ""}</div>`).join("")
      : '<p style="color:var(--muted)">Nothing yet. When you pick the same medicine for a drug class twice, I will offer it as a suggestion next time.</p>';
    $$("[data-approve]").forEach((b) => b.onclick = async () => { await api(`/api/lessons/${b.dataset.approve}/approve`, { method: "POST" }); toast("Done. It will now appear as a suggestion."); refreshLessons(); });
  } catch { /* optional panel */ }
}

/* ---------- start ---------- */
window.Portal.start(["doctor"], async (role, me) => {
  state.doctor = me;
  $$("#dayTabs .tab").forEach((t) => t.addEventListener("click", () => {
    $$("#dayTabs .tab").forEach((x) => x.classList.toggle("on", x === t));
    state.day = t.dataset.day; loadSchedule();
  }));
  $("#apptList").addEventListener("click", (e) => { const b = e.target.closest(".appt"); if (b) openPatient(b.dataset.pid); });
  initScribe();
  let todayCount = 1;
  try { todayCount = await loadSummary(); } catch (err) { toast(err.message, true); }
  if (todayCount === 0) {                       // nothing left today: open on "All upcoming" so the page is not empty
    state.day = "";
    $$("#dayTabs .tab").forEach((t) => t.classList.toggle("on", t.dataset.day === ""));
  }
  loadSchedule();
});
})();
