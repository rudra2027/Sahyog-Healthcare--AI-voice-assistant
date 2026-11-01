/* =====================================================================
   Shared helpers for the two staff portals (doctor.html and desk.html).
   The patient site has its own app.js. Staff pages never load the booking site's code.

   Portal.start(["doctor"], init) waits until we know who is signed in, then:
     - nobody signed in    -> shows the "Staff sign-in" screen
     - wrong role          -> auth.js has already sent them to their own portal
     - the right role      -> runs init(role) and shows the page
   ===================================================================== */
(() => {
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const parse = (s) => new Date(s.replace(" ", "T"));
const clock = (s) => parse(s).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const fmtTime = (s) => parse(s).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
const dayLabel = (s) => {
  const d = parse(s), today = new Date(), tomorrow = new Date(Date.now() + 864e5);
  return d.toDateString() === today.toDateString() ? "Today" : d.toDateString() === tomorrow.toDateString() ? "Tomorrow" : d.toLocaleDateString([], { weekday: "long" });
};

function toast(message, bad = false) {
  const t = Object.assign(document.createElement("div"), { className: "toast" + (bad ? " bad" : ""), textContent: message });
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

/** Call the API with the signed-in person's token. The server checks the role on every call. */
async function api(path, { method = "GET", body, form } = {}) {
  const headers = { ...window.SahyogAuth.headers() };
  let payload;
  if (form) payload = form;
  else if (body) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* the body was not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

function table(headers, rows) {
  if (!rows.length) return '<div class="empty">Nothing to show here.</div>';
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map((r, i) =>
    `<tr style="animation-delay:${Math.min(i, 15) * 25}ms">${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

/** Fade-in on scroll, frosted nav, and the soft light that follows the pointer on cards. */
function motion() {
  const nav = $("#nav");
  addEventListener("scroll", () => nav.classList.toggle("scrolled", scrollY > 8), { passive: true });
  const reveal = new IntersectionObserver((entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); reveal.unobserve(e.target); } }), { threshold: 0.12 });
  $$(".reveal").forEach((n) => reveal.observe(n));
  document.addEventListener("pointermove", (e) => {
    const card = e.target.closest?.(".card");
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${e.clientX - r.left}px`); card.style.setProperty("--my", `${e.clientY - r.top}px`);
  });
}

async function start(allowedRoles, init) {
  motion();
  await window.SahyogAuth.whenReady();
  const role = window.SahyogAuth.role();
  if (!role) {                                   // nobody is signed in: show the gate, not the page
    $("#gate").hidden = false;
    $("#gateBtn").onclick = () => window.SahyogAuth.openModal();
    if (window.SahyogAuth.enabled()) setTimeout(() => window.SahyogAuth.openModal(), 400);
    addEventListener("sahyog:auth", () => { if (window.SahyogAuth.me()) location.reload(); });   // signed in from the gate: open the portal
    return;
  }
  if (!allowedRoles.includes(role)) return;      // auth.js is already sending them to their own portal
  $("#gate").hidden = true;
  document.body.classList.add("ready");
  const startedAs = window.SahyogAuth.me()?.id;
  addEventListener("sahyog:auth", () => { if (window.SahyogAuth.me()?.id !== startedAs) location.reload(); });   // a different person: start clean
  init(role, window.SahyogAuth.me());
}

window.Portal = { $, $$, esc, sleep, reduceMotion, parse, clock, fmtTime, dayLabel, toast, api, table, start };
})();
