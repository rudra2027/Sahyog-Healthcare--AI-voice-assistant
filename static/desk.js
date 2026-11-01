/* =====================================================================
   Front desk console.
     Day board     every doctor for one day: who is booked when, which times are free, which are blocked
     Appointments  the same day as a list, with Move and Cancel
     Patients      search a patient: all their bookings, the files they uploaded (names only) and their tickets
     Tickets       things that need a person
     Audit         compliance only
   Moving a booking (a new time, or a different doctor) happens in one window. The assistant (the round button)
   does the same things by talking, and this page refreshes whenever it changes something.
   Compliance can look at everything but cannot change anything.
   ===================================================================== */
(() => {
"use strict";
const { $, $$, esc, toast, api, table, parse, clock } = window.Portal;

const iso = (d) => d.toLocaleDateString("en-CA");
const plusDays = (n) => iso(new Date(Date.now() + n * 864e5));
const state = { view: "board", day: iso(new Date()), role: null, board: null, patientId: null, search: "", clinic: "", find: "", doctors: [], tickets: [] };
const canEdit = () => state.role === "frontdesk";
const dayName = (day) => day === plusDays(0) ? "today" : day === plusDays(1) ? "tomorrow" : parse(day + " 00:00").toLocaleDateString([], { weekday: "long", day: "numeric", month: "short" });
const when = (s) => `${parse(s).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })}, ${clock(s)}`;
const initials = (name) => name.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();
const TITLES = { board: "Day board", appts: "Appointments", patients: "Patients", tickets: "Tickets", audit: "Audit trail" };

/* ---------- the numbers across the top ---------- */
async function renderKpis() {
  const board = state.board, t = board.totals, total = t.booked + t.free + t.blocked || 1;
  state.tickets = await api("/api/tickets");
  const open = state.tickets.filter((x) => x.status === "open"), urgent = open.filter((x) => x.kind === "emergency");
  const kpi = (value, label, hot) => `<div class="kpi ${hot ? "hot" : ""}"><b>${value}</b><span>${label}</span></div>`;
  $("#kpis").innerHTML = kpi(t.booked, `booked ${dayName(state.day)}`) + kpi(t.free, "free times") + kpi(t.blocked, "blocked times")
    + `<div class="kpi"><b>${Math.round((t.booked / Math.max(t.booked + t.free, 1)) * 100)}%</b><span>of open time is booked</span><div class="bar"><i style="width:${Math.round((t.booked / Math.max(t.booked + t.free, 1)) * 100)}%"></i></div></div>`
    + kpi(open.length, "open tickets", false) + kpi(urgent.length, "urgent tickets", urgent.length > 0);
  const badge = $("#ticketCount"); badge.hidden = !open.length; badge.textContent = open.length;
}

async function loadBoard() { state.board = await api("/api/desk/board?day=" + state.day); await renderKpis(); }

/* ---------- day board ---------- */
function slotChips(doc) {
  const now = new Date();
  const all = [...doc.bookings.map((b) => ({ ...b, t: "booked" })), ...doc.free.map((s) => ({ ...s, t: "free" })), ...doc.blocked.map((s) => ({ ...s, t: "blocked" }))]
    .sort((a, b) => a.starts_at.localeCompare(b.starts_at));
  return all.map((s) => {
    const past = parse(s.starts_at) < now ? " past" : "";
    if (s.t === "booked") {
      return `<button class="tslot booked${s.kind === "followup" ? " followup" : ""}${past}" data-appt="${s.id}" data-doc="${esc(doc.id)}" title="${esc(s.patient_name)}${s.reason ? " · " + esc(s.reason) : ""}">${esc(clock(s.starts_at))}<small>${esc(s.patient_name.split(" ")[0])}</small></button>`;
    }
    if (s.t === "blocked") return `<button class="tslot blocked${past}" data-unblock="${s.slot_id}" title="Blocked. ${canEdit() ? "Tap to open it again." : ""}" ${canEdit() ? "" : "disabled"}>${esc(clock(s.starts_at))}<small>blocked</small></button>`;
    return `<button class="tslot free${past}" data-block="${s.slot_id}" title="Free. ${canEdit() ? "Tap twice to block it." : ""}" ${past || !canEdit() ? "disabled" : ""}>${esc(clock(s.starts_at))}</button>`;
  }).join("");
}

function renderBoard() {
  const board = state.board;
  const clinics = [...new Set(board.doctors.map((d) => d.clinic))];
  let docs = board.doctors.filter((d) => (!state.clinic || d.clinic === state.clinic) && (!state.find || `${d.name} ${d.specialty}`.toLowerCase().includes(state.find)));
  docs = docs.filter((d) => d.bookings.length + d.free.length + d.blocked.length > 0);
  $("#view").innerHTML = `<div class="toolbar">
      <select class="field" id="fClinic" aria-label="Clinic"><option value="">All clinics</option>${clinics.map((c) => `<option ${c === state.clinic ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <input class="field" id="fFind" placeholder="Find a doctor or specialty" value="${esc(state.find)}" aria-label="Find a doctor">
      <span class="spacer"></span>
      <span class="legend"><span><i style="background:color-mix(in srgb,var(--accent) 30%,transparent)"></i>booked</span><span><i style="border:1.5px dashed var(--muted)"></i>free</span><span><i style="background:repeating-linear-gradient(135deg,var(--surface-2),var(--surface-2) 3px,var(--line) 3px,var(--line) 6px)"></i>blocked</span></span>
    </div>
    <div class="doctors">${docs.map((d) => {
      const n = d.bookings.length, f = d.free.length, x = d.blocked.length, total = n + f + x || 1;
      const away = x > 0 && f === 0 && n === 0;
      return `<section class="dcard ${away ? "away" : ""}" data-doctor="${esc(d.id)}">
        <div class="dhead"><div><b>${esc(d.name)}</b><small>${esc(d.specialty)} · ${esc(d.clinic)}</small></div>
          <div class="tools">${away ? '<span class="pill warn">Away</span>' : f ? `<span class="pill ok">${f} free</span>` : '<span class="pill bad">Full</span>'}
            ${canEdit() ? `<button class="btn small ghost" data-away="${esc(d.id)}" title="Block the rest of ${dayName(state.day)}">Doctor away</button>` : ""}</div></div>
        <div class="load" title="${n} booked, ${f} free, ${x} blocked"><i class="b" style="width:${(n / total) * 100}%"></i><i class="x" style="width:${(x / total) * 100}%"></i></div>
        <div class="dstats"><span><b>${n}</b> booked</span><span><b>${f}</b> free</span>${x ? `<span><b>${x}</b> blocked</span>` : ""}</div>
        <div class="tgrid">${slotChips(d)}</div></section>`;
    }).join("") || '<div class="empty">No doctor has times on this day.</div>'}</div>
    <p class="note">Tap a booked time to see the patient and move, change doctor or cancel. Blue chips are bookings; a green tint is a follow-up the doctor asked for.</p>`;
  $("#fClinic").onchange = (e) => { state.clinic = e.target.value; renderBoard(); };
  $("#fFind").oninput = (e) => { state.find = e.target.value.trim().toLowerCase(); const at = e.target.selectionStart; renderBoard(); const box = $("#fFind"); box.focus(); box.setSelectionRange(at, at); };
  $$("[data-appt]").forEach((b) => b.onclick = () => openBooking(Number(b.dataset.appt)));
  $$("[data-unblock]").forEach((b) => b.onclick = () => slotAction(b.dataset.unblock, "unblock"));
  $$("[data-block]").forEach((b) => b.onclick = () => twoTap(b, "Block?", () => slotAction(b.dataset.block, "block")));
  $$("[data-away]").forEach((b) => b.onclick = () => openAway(b.dataset.away));
}

/** A first tap arms the button, a second tap within four seconds does it. */
function twoTap(button, armedText, run) {
  if (button.dataset.sure) { run(); return; }
  const was = button.innerHTML;
  button.dataset.sure = "1"; button.classList.add("sure"); button.innerHTML = armedText;
  setTimeout(() => { if (button.isConnected) { delete button.dataset.sure; button.classList.remove("sure"); button.innerHTML = was; } }, 4000);
}

async function slotAction(slotId, action) {
  try { await api(`/api/desk/slots/${slotId}/${action}`, { method: "POST" }); toast(action === "block" ? "Time blocked." : "Time opened again."); await refresh(); }
  catch (err) { toast(err.message, true); }
}

/* ---------- appointments list ---------- */
function allBookings() {
  return state.board.doctors.flatMap((d) => d.bookings.map((b) => ({ ...b, doctor_id: d.id, doctor_name: d.name, specialty: d.specialty, clinic: d.clinic }))).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
}
function renderAppts() {
  const q = state.search;
  const rows = allBookings().filter((a) => !q || a.patient_name.toLowerCase().includes(q) || a.doctor_name.toLowerCase().includes(q));
  $("#view").innerHTML = `<div class="toolbar"><input class="field" id="fSearch" placeholder="Search a patient or doctor" value="${esc(state.search)}" aria-label="Search"></div>` +
    table(["Time", "Patient", "Doctor", "Clinic", "Reason", ""], rows.map((a) => [
      `<b>${esc(clock(a.starts_at))}</b>`,
      `<a href="#" data-patient="${esc(a.patient_id)}">${esc(a.patient_name)}</a>${a.patient_age ? `<br><small>${a.patient_age} years</small>` : ""}${a.kind === "followup" ? ' <span class="pill info">follow-up</span>' : ""}`,
      `${esc(a.doctor_name)}<br><small>${esc(a.specialty)}</small>`, esc(a.clinic), esc(a.reason || ""),
      canEdit() ? `<div class="actions"><button class="btn small ghost" data-move="${a.id}">Shift time</button><button class="btn small ghost" data-switch="${a.id}">Change doctor</button><button class="btn small ghost" data-cancel="${a.id}">Cancel</button></div>` : ""]));
  $("#fSearch").oninput = (e) => { state.search = e.target.value.trim().toLowerCase(); const at = e.target.selectionStart; renderAppts(); const box = $("#fSearch"); box.focus(); box.setSelectionRange(at, at); };
  wireBookingButtons();
}

function wireBookingButtons() {
  $$("[data-move]").forEach((b) => b.onclick = () => openMove(Number(b.dataset.move), "time"));
  $$("[data-switch]").forEach((b) => b.onclick = () => openMove(Number(b.dataset.switch), "doctor"));
  $$("[data-cancel]").forEach((b) => b.onclick = () => twoTap(b, "Tap again to cancel", async () => {
    try { await api(`/api/appointments/${b.dataset.cancel}/cancel`, { method: "POST" }); toast("Booking cancelled. The time is free again."); await refresh(); }
    catch (err) { toast(err.message, true); }
  }));
  $$("[data-patient]").forEach((a) => a.onclick = (e) => { e.preventDefault(); openPatient(a.dataset.patient); });
}

/* ---------- the window shown when a booked time is tapped ---------- */
function findBooking(id) { return allBookings().find((a) => a.id === id); }
function openBooking(id) {
  const a = findBooking(id); if (!a) return;
  showModal(`<h3>${esc(a.patient_name)}</h3><p class="sub">${esc(dayName(a.starts_at.slice(0, 10)))} at ${esc(clock(a.starts_at))}</p>
    <dl class="kv"><dt>Doctor</dt><dd>${esc(a.doctor_name)} · ${esc(a.specialty)}</dd><dt>Clinic</dt><dd>${esc(a.clinic)}</dd>
      <dt>Visit</dt><dd>${a.kind === "followup" ? "Follow-up asked by the doctor" : "Visit"}</dd><dt>Reason</dt><dd>${esc(a.reason || "Not given")}</dd>${a.patient_age ? `<dt>Age</dt><dd>${a.patient_age} years</dd>` : ""}</dl>
    <div class="foot"><button class="btn ghost small" data-patient="${esc(a.patient_id)}">Patient record</button>
      ${canEdit() ? `<button class="btn ghost small" data-cancel="${a.id}">Cancel booking</button><button class="btn ghost small" data-switch="${a.id}">Change doctor</button><button class="btn small" data-move="${a.id}">Shift time</button>` : ""}
      <button class="btn ghost small" data-close>Close</button></div>`);
  const card = $(".modal-card");
  $$("[data-patient]", card).forEach((b) => b.onclick = () => { closeModal(); openPatient(b.dataset.patient); });
  $$("[data-move]", card).forEach((b) => b.onclick = () => openMove(a.id, "time"));
  $$("[data-switch]", card).forEach((b) => b.onclick = () => openMove(a.id, "doctor"));
  $$("[data-cancel]", card).forEach((b) => b.onclick = () => twoTap(b, "Tap again to cancel", async () => {
    try { await api(`/api/appointments/${a.id}/cancel`, { method: "POST" }); closeModal(); toast("Booking cancelled. The time is free again."); await refresh(); }
    catch (err) { toast(err.message, true); }
  }));
}

/* ---------- moving a booking: a new time, or a different doctor ---------- */
async function openMove(appointmentId, mode, known) {
  const a = known || findBooking(appointmentId) || (await findInPatient(appointmentId)); if (!a) return toast("That booking is no longer there.", true);
  if (!state.doctors.length) state.doctors = await api("/api/doctors");
  const pick = { mode, doctor: a.doctor_id, day: a.starts_at.slice(0, 10), slot: null };
  const same = state.doctors.filter((d) => d.specialty === a.specialty && d.id !== a.doctor_id), others = state.doctors.filter((d) => d.specialty !== a.specialty);
  const doctorSelect = () => mode === "time"
    ? `<p><b>${esc(a.doctor_name)}</b> · ${esc(a.specialty)}, ${esc(a.clinic)}</p>`
    : `<select class="field" id="mvDoctor">${same.length ? `<optgroup label="Same specialty (${esc(a.specialty)})">${same.map((d) => `<option value="${esc(d.id)}">${esc(d.name)} · ${esc(d.clinic)}</option>`).join("")}</optgroup>` : ""}
        <optgroup label="Other specialties">${others.map((d) => `<option value="${esc(d.id)}">${esc(d.name)} · ${esc(d.specialty)}, ${esc(d.clinic)}</option>`).join("")}</optgroup></select>`;
  showModal(`<h3>${mode === "time" ? "Shift the time" : "Change the doctor"}</h3>
    <p class="sub">${esc(a.patient_name)} is booked ${esc(when(a.starts_at))} with ${esc(a.doctor_name)}.</p>
    <label>${mode === "time" ? "Doctor" : "New doctor"}</label>${doctorSelect()}
    <label>Day</label><div class="drow" id="mvDays">${[0, 1, 2].map((n) => `<button type="button" class="dchip" data-day="${plusDays(n)}">${["Today", "Tomorrow", dayName(plusDays(2)).split(",")[0]][n]}</button>`).join("")}</div>
    <label>Free times</label><div class="tgrid" id="mvSlots"><div class="empty">Loading...</div></div>
    <div class="foot"><button class="btn ghost small" data-close>Back</button><button class="btn small" id="mvGo" disabled>Confirm the move</button></div>`);
  if (mode === "doctor") { pick.doctor = $("#mvDoctor").value; $("#mvDoctor").onchange = (e) => { pick.doctor = e.target.value; pick.slot = null; loadSlots(); }; }
  $$("#mvDays .dchip").forEach((c) => c.onclick = () => { pick.day = c.dataset.day; pick.slot = null; loadSlots(); });
  async function loadSlots() {
    $$("#mvDays .dchip").forEach((c) => c.classList.toggle("on", c.dataset.day === pick.day));
    $("#mvGo").disabled = true;
    const slots = await api(`/api/desk/doctors/${pick.doctor}/slots?day=${pick.day}`);
    $("#mvSlots").innerHTML = slots.length ? slots.map((s) => `<button type="button" class="tslot ${s.taken === 2 ? "blocked" : "free"}" data-slot="${s.id}" ${s.taken === 2 ? "disabled" : ""}>${esc(clock(s.starts_at))}${s.taken === 2 ? "<small>blocked</small>" : ""}</button>`).join("") : '<div class="empty">No free times that day.</div>';
    $$("#mvSlots [data-slot]").forEach((b) => b.onclick = () => { pick.slot = Number(b.dataset.slot); $$("#mvSlots .tslot").forEach((x) => x.classList.toggle("booked", x === b)); $("#mvGo").disabled = false; });
  }
  $("#mvGo").onclick = async () => {
    try {
      const r = await api(`/api/appointments/${a.id}/reschedule`, { method: "POST", body: { slot_id: pick.slot } });
      closeModal(); toast(r.changed_doctor ? "Doctor changed. The old time is free again." : "Booking moved. The old time is free again.");
      await refresh();
    } catch (err) { toast(err.message, true); loadSlots(); }
  };
  loadSlots();
}

/* ---------- a doctor is away ---------- */
function openAway(doctorId) {
  const doc = state.board.doctors.find((d) => d.id === doctorId);
  const now = new Date(), nextHour = `${String(Math.min(now.getHours() + 1, 17)).padStart(2, "0")}:00`;
  const today = state.day === plusDays(0);
  showModal(`<h3>${esc(doc.name)} is away</h3><p class="sub">Every free time from the hour you choose, ${esc(dayName(state.day))}, is closed so nobody can book it. Patients who are already booked are listed so you can move them.</p>
    <label>From</label><select class="field" id="awayFrom"><option value="00:00">The whole day</option>${["09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00"].map((t) => `<option value="${t}" ${today && t === nextHour ? "selected" : ""}>${esc(clock(state.day + " " + t))}</option>`).join("")}</select>
    <div class="foot"><button class="btn ghost small" data-close>Back</button><button class="btn small danger" id="awayGo">Close these times</button></div>`);
  $("#awayGo").onclick = async () => {
    try {
      const r = await api(`/api/desk/doctors/${doctorId}/block-day`, { method: "POST", body: { day: state.day, from_time: $("#awayFrom").value } });
      await refresh();
      if (!r.to_move.length) { closeModal(); return toast(`${r.blocked} times closed.`); }
      showModal(`<h3>${r.blocked} times closed</h3><p class="sub">${r.to_move.length} patient${r.to_move.length === 1 ? " is" : "s are"} still booked with ${esc(doc.name)}. Move them to another time or doctor.</p>
        ${table(["Time", "Patient", ""], r.to_move.map((a) => [esc(clock(a.starts_at)), esc(a.patient_name), `<div class="actions"><button class="btn small ghost" data-move="${a.id}">Shift time</button><button class="btn small" data-switch="${a.id}">Change doctor</button></div>`]))}
        <div class="foot"><button class="btn small" data-close>Done</button></div>`);
      $$(".modal-card [data-move]").forEach((b) => b.onclick = () => openMove(Number(b.dataset.move), "time"));
      $$(".modal-card [data-switch]").forEach((b) => b.onclick = () => openMove(Number(b.dataset.switch), "doctor"));
    } catch (err) { toast(err.message, true); }
  };
}

/* ---------- patients ---------- */
async function renderPatients() {
  $("#view").innerHTML = `<div class="split"><div><div class="toolbar"><input class="field" id="pSearch" placeholder="Search a patient" aria-label="Search a patient" style="max-width:none"></div><div class="plist" id="plist"></div></div><div id="pdetail"><div class="cpanel empty">Choose a patient to see their bookings and files.</div></div></div>`;
  const load = async () => {
    const people = await api("/api/desk/patients?q=" + encodeURIComponent($("#pSearch").value.trim()));
    $("#plist").innerHTML = people.map((p) => `<button class="prow ${p.id === state.patientId ? "on" : ""}" data-pid="${esc(p.id)}"><b>${esc(p.name)}</b><small>${p.age ? p.age + " years · " : ""}${p.next_visit ? "next " + esc(when(p.next_visit)) : "no upcoming visit"}<br>${p.visits} booking${p.visits === 1 ? "" : "s"} · ${p.documents} file${p.documents === 1 ? "" : "s"}</small></button>`).join("") || '<div class="empty">No patient found.</div>';
    $$("[data-pid]").forEach((b) => b.onclick = () => openPatient(b.dataset.pid));
  };
  let t; $("#pSearch").oninput = () => { clearTimeout(t); t = setTimeout(load, 200); };
  await load();
  if (state.patientId) await showPatient(state.patientId);
}

async function openPatient(id) {
  closeModal();
  state.patientId = id;
  if (state.view !== "patients") { setView("patients"); return; }
  await showPatient(id);
  $$("[data-pid]").forEach((b) => b.classList.toggle("on", b.dataset.pid === id));
}

let recordCache = {};
async function findInPatient(appointmentId) {
  const v = Object.values(recordCache).flatMap((r) => r.visits.map((x) => ({ ...x, patient_name: r.patient.name, patient_id: r.patient.id }))).find((x) => x.id === appointmentId);
  return v && v.starts_at ? v : null;
}

async function showPatient(id) {
  const box = $("#pdetail"); if (!box) return;
  let rec;
  try { rec = await api("/api/desk/patients/" + id); } catch (err) { box.innerHTML = `<div class="cpanel empty">${esc(err.message)}</div>`; return; }
  recordCache[id] = rec;
  const p = rec.patient, now = new Date().toISOString().slice(0, 16).replace("T", " ");
  const upcoming = rec.visits.filter((v) => v.status === "booked" && v.starts_at >= now).length;
  box.innerHTML = `<div class="stack"><div class="cpanel"><div class="phead"><span class="pavatar">${esc(initials(p.name))}</span><div><h2>${esc(p.name)}</h2>
      <p>${p.age ? p.age + " years · " : ""}${esc(p.language || "")}${p.phone ? " · " + esc(p.phone) : ""}</p></div>
      <span class="pill info" style="margin-left:auto">${upcoming} upcoming</span></div>
    <h3>Bookings <small>every visit, newest first</small></h3>
    ${table(["When", "Doctor", "Clinic", "Type", "Reason", "Status", ""], rec.visits.map((v) => [
      v.starts_at ? `<b>${esc(clock(v.starts_at))}</b><br><small>${esc(parse(v.starts_at).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" }))}</small>` : '<small>time released</small>',
      v.doctor_name ? `${esc(v.doctor_name)}<br><small>${esc(v.specialty)}</small>` : "—", esc(v.clinic || ""), v.kind === "followup" ? '<span class="pill info">Follow-up</span>' : "Visit", esc(v.reason || ""),
      `<span class="pill ${v.status === "booked" ? (v.starts_at < now ? "" : "ok") : "bad"}">${v.status === "booked" ? (v.starts_at < now ? "Done" : "Booked") : esc(v.status)}</span>`,
      canEdit() && v.status === "booked" && v.starts_at >= now ? `<div class="actions"><button class="btn small ghost" data-move="${v.id}">Shift time</button><button class="btn small ghost" data-switch="${v.id}">Change doctor</button><button class="btn small ghost" data-cancel="${v.id}">Cancel</button></div>` : ""]))}</div>
    <div class="cpanel"><h3>Documents and reports uploaded <small>names and dates only; the front desk does not read the contents</small></h3>
    ${table(["File", "Uploaded", "Pages", "Status"], rec.documents.map((d) => [esc(d.filename), esc(d.uploaded_at ? when(d.uploaded_at.slice(0, 16)) : "—"), d.pages || "—", `<span class="pill ${d.status === "ready" ? "ok" : "warn"}">${d.status === "ready" ? "Ready" : "Being read"}</span>`]))}</div>
    <div class="cpanel"><h3>Tickets</h3>${table(["#", "Kind", "Reason", "Opened", "Status"], rec.tickets.map((t) => [t.id, esc(t.kind.replace("_", " ")), esc(t.reason), esc(t.created_at), `<span class="pill ${t.status === "open" ? "warn" : "ok"}">${esc(t.status)}</span>`]))}</div></div>`;
  wireBookingButtons();
}

/* ---------- tickets and audit ---------- */
function renderTickets() {
  const rows = state.tickets;
  $("#view").innerHTML = table(["#", "Kind", "Patient", "Reason", "Opened", "Status", ""], rows.map((t) => [
    t.id, `<span class="pill ${t.kind === "emergency" ? "bad" : "warn"}">${t.kind === "emergency" ? "Emergency" : "Front desk"}</span>`,
    t.patient_id ? `<a href="#" data-patient="${esc(t.patient_id)}">${esc(t.patient_name || "")}</a>` : "", esc(t.reason), esc(t.created_at),
    `<span class="pill ${t.status === "open" ? "warn" : "ok"}">${esc(t.status)}</span>`, t.status === "open" && canEdit() ? `<button class="btn small" data-resolve="${t.id}">Mark done</button>` : ""]));
  wireBookingButtons();
  $$("[data-resolve]").forEach((b) => b.onclick = async () => {
    try { await api(`/api/tickets/${b.dataset.resolve}/resolve`, { method: "POST" }); toast("Ticket closed."); await refresh(); } catch (err) { toast(err.message, true); }
  });
}

async function renderAudit() {
  $("#view").innerHTML = '<div class="toolbar"><input class="field" id="auditQ" placeholder="Filter by record, e.g. patient:p1"><button class="btn small" id="auditGo">Search</button></div><div id="auditRows"></div>';
  const load = async () => {
    const rows = await api(`/api/audit?record=${encodeURIComponent($("#auditQ").value)}`);
    $("#auditRows").innerHTML = table(["When", "Who", "Role", "What", "Record"], rows.map((a) => [esc(a.at), esc(a.actor), esc(a.role), esc(a.purpose), esc(a.record)]));
  };
  $("#auditGo").onclick = load; $("#auditQ").onkeydown = (e) => e.key === "Enter" && load();
  await load();
}

/* ---------- the shell ---------- */
function showModal(html) { const m = $("#modal"); $(".modal-card", m).innerHTML = html; m.hidden = false; $$("[data-close]", m).forEach((b) => b.onclick = closeModal); }
function closeModal() { $("#modal").hidden = true; }

async function render() {
  $("#viewTitle").textContent = TITLES[state.view];
  $("#daybar").hidden = !["board", "appts"].includes(state.view);
  $("#kpis").hidden = !["board", "appts"].includes(state.view);
  try {
    if (["board", "appts"].includes(state.view)) { await loadBoard(); state.view === "board" ? renderBoard() : renderAppts(); }
    else if (state.view === "patients") await renderPatients();
    else if (state.view === "tickets") { state.tickets = await api("/api/tickets"); renderTickets(); const open = state.tickets.filter((x) => x.status === "open"); const badge = $("#ticketCount"); badge.hidden = !open.length; badge.textContent = open.length; }
    else await renderAudit();
  } catch (err) { $("#view").innerHTML = `<div class="cpanel empty">${esc(err.message)}</div>`; }
}

/** Reload what is on screen after anything changed (a button, or the assistant). */
async function refresh() {
  recordCache = {};
  if (!$("#modal").hidden) closeModal();
  await render();
}

function setView(view) {
  state.view = view;
  $$("#sideNav button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  render();
}

window.Portal.start(["frontdesk", "compliance"], async (role, me) => {
  state.role = role;
  $("#today").textContent = `${new Date().toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" })} · ${me.name}`;
  $("#day2").textContent = new Date(Date.now() + 2 * 864e5).toLocaleDateString([], { weekday: "short", day: "numeric" });
  if (role === "compliance") { $("#auditNav").hidden = false; state.view = "audit"; $$("#sideNav button").forEach((b) => b.classList.toggle("on", b.dataset.view === "audit")); }
  $("#sideNav").onclick = (e) => { const b = e.target.closest("[data-view]"); if (b) setView(b.dataset.view); };
  $("#daybar").onclick = (e) => {
    const b = e.target.closest("[data-offset]"); if (!b) return;
    state.day = plusDays(Number(b.dataset.offset));
    $$("#daybar button").forEach((x) => x.classList.toggle("on", x === b));
    render();
  };
  $("#askBtn").onclick = () => window.SahyogBot?.open();
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  // The assistant changed something (or named a patient): show it here.
  addEventListener("sahyog:desk", (e) => { if (e.detail?.changed) refresh(); else if (e.detail?.patient_id) openPatient(e.detail.patient_id); });
  setInterval(() => { if (!document.hidden && $("#modal").hidden && ["board", "appts", "tickets"].includes(state.view)) render(); }, 60000);   // a quiet refresh every minute
  render();
});
})();
