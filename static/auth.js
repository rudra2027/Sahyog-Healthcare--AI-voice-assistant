/* =====================================================================
   Sign-in with Supabase (email + password), who you are (your role), and your profile.

   How it works, in plain words:
     1. The server tells us the Supabase address and public key (GET /api/config).
     2. The sign-in window talks to Supabase directly. Supabase checks the password and
        gives the browser a "session" with an access token.
     3. Every call to OUR API carries that token (see headers()). The server asks Supabase
        who the token belongs to, so nobody can pretend to be someone else.
     4. We then ask the server "who am I?" (GET /api/me). The answer includes a ROLE:
        patient, doctor or frontdesk. The role is decided on the server by an administrator,
        never by the browser, and it decides which portal you are allowed to see:
            patient -> /       doctor -> /doctor       frontdesk and compliance -> /desk
     5. Two small Supabase tables (supabase/schema.sql) store the profile (including what the
        bot looks like) and the chat history, private to each person.

   Other scripts use it through  window.SahyogAuth  (see the bottom of this file).
   ===================================================================== */
(() => {
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } },
};
const session$ = {   // the demo identity lives only in this tab, and only works when the server allows demo login
  get() { try { return sessionStorage.getItem("sahyog.demo"); } catch { return null; } },
  set(v) { try { sessionStorage.setItem("sahyog.demo", v); } catch { /* ignore */ } },
};

// Which portal does each role belong to?
const HOME = { patient: "/", doctor: "/doctor", frontdesk: "/desk", compliance: "/desk" };
const PORTAL_OF = { patient: "patient", doctor: "doctor", frontdesk: "desk", compliance: "desk" };

let sb = null;            // the Supabase client (null when sign-in is not configured)
let session = null;       // who is signed in right now (null = nobody)
let me = null;            // the server's answer to "who am I?" { id, name, role, doctor_id }
let profile = null;       // their row in the "profiles" table, if that table exists
let tablesOk = false;     // did the tables answer? If not, everything still works, just without saving
let staffSignup = { doctor: false, frontdesk: false };   // which staff roles the hospital lets people register for (from /api/config)
let modal = null, mode = "signin", resolveReady;
const readyPromise = new Promise((resolve) => { resolveReady = resolve; });

const user = () => session?.user || null;
const displayName = () => me?.name || profile?.display_name || user()?.user_metadata?.full_name || user()?.email?.split("@")[0] || "";

/* ---------- who am I, according to the server ---------- */
async function refreshMe() {
  me = null;
  if (!session?.access_token && !session$.get()) return;
  try {
    const res = await fetch("/api/me", { headers: headers() });
    if (res.ok) me = await res.json();
  } catch { /* offline: stay signed out */ }
}

/** Send people to their own portal. A doctor never sees the booking site, and a patient never sees the staff pages. */
function routeByRole() {
  if (!me) return;
  const here = document.body.dataset.portal;              // "patient", "doctor" or "desk" (set on each page's <body>)
  if (here && PORTAL_OF[me.role] !== here) location.replace(HOME[me.role] || "/");
}

/* ---------- the profile table ---------- */
async function syncProfile() {
  profile = null; tablesOk = false;
  if (!sb || !user()) return;
  try {
    const { data, error } = await sb.from("profiles").select("display_name,bot_seed").eq("id", user().id).maybeSingle();
    if (error) throw error;
    tablesOk = true;
    if (data) { profile = data; return; }
    // First sign-in: create the profile and keep the look this person already had in this browser.
    const row = { id: user().id, display_name: displayName(), bot_seed: store.get("sahyog.seed") || user().id };
    const { error: insertError } = await sb.from("profiles").insert(row);
    if (!insertError) profile = row;
  } catch (err) {
    console.info("Supabase tables are not set up yet (run supabase/schema.sql). Sign-in still works.", err.message || err);
  }
}

/** The seed that decides how this person's bot looks. */
function seed() {
  if (!user()) return null;
  return profile?.bot_seed || store.get("sahyog.seed." + user().id) || user().id;
}
async function saveSeed(value) {
  if (!user()) return;
  store.set("sahyog.seed." + user().id, value);
  if (profile) profile.bot_seed = value;
  if (tablesOk) await sb.from("profiles").update({ bot_seed: value }).eq("id", user().id);
}

/* ---------- chat history table (patients only: staff questions are not stored here) ---------- */
const keepsHistory = () => sb && user() && tablesOk && (!me || me.role === "patient");
function saveMessage(role, content) {
  if (!keepsHistory()) return;
  sb.from("chat_messages").insert({ role, content: String(content).slice(0, 4000) }).then(() => {});   // user_id fills itself in (auth.uid())
}
async function loadHistory() {
  if (!keepsHistory()) return [];
  const { data } = await sb.from("chat_messages").select("role,content").order("created_at", { ascending: false }).limit(12);
  return (data || []).reverse();
}

/* ---------- what the rest of the page asks us ---------- */
function headers() {
  if (session?.access_token) return { Authorization: `Bearer ${session.access_token}` };
  const demo = session$.get();                              // only honoured by the server when ALLOW_DEMO_LOGIN=true
  return demo ? { "X-User-Id": demo } : {};
}

/* ---------- the sign-in window ---------- */
function buildModal() {
  const staffPage = ["doctor", "desk"].includes(document.body.dataset.portal);
  modal = document.createElement("div");
  modal.className = "auth-back";
  modal.innerHTML = `
    <form class="auth-card" role="dialog" aria-modal="true" aria-labelledby="authTitle" novalidate>
      <span data-bot></span>
      <h3 id="authTitle">${staffPage ? "Staff sign-in" : "Welcome to Sahyog"}</h3>
      <p class="sub">${staffPage ? "Sign in with your staff account." : "Sign in so your assistant remembers you."}</p>
      <div class="auth-tabs" role="tablist"><button type="button" data-mode="signin" class="on">Sign in</button><button type="button" data-mode="signup">Create account</button></div>
      <div data-only="signup"><label for="authName">Your name</label><input id="authName" autocomplete="name" placeholder="Priya"></div>
      <div id="authRoleBox">
        <label for="authRole">I am a <span id="authRoleHint"></span></label>
        <select id="authRole"><option value="patient">Patient</option>${staffSignup.doctor ? '<option value="doctor">Doctor</option>' : ""}${staffSignup.frontdesk ? '<option value="frontdesk">Front desk / help desk staff</option>' : ""}</select>
        <div id="authDoctorBox" hidden>
          <label for="authSpecialty">Your specialty</label><select id="authSpecialty"></select>
          <label for="authClinic">Your clinic</label><select id="authClinic"></select>
        </div>
        <div id="authCodeBox" hidden><label for="authCode">Staff access code</label><input id="authCode" type="password" autocomplete="off" placeholder="Given to you by the hospital"></div>
      </div>
      <label for="authEmail">Email</label><input id="authEmail" type="email" autocomplete="email" placeholder="you@example.com" required>
      <label for="authPass">Password</label><input id="authPass" type="password" autocomplete="current-password" placeholder="At least 6 characters" required>
      <button class="btn" id="authGo">Sign in</button>
      <div class="auth-msg" id="authMsg" role="alert"></div>
      <p class="auth-note">${staffPage ? "Staff: choose Doctor or Front desk, enter the access code the hospital gave you, and create your account or sign in." : "Your assistant is unique to you. Demo data only, not medical advice."}</p>
    </form>`;
  document.body.append(modal);
  modal.addEventListener("click", (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && modal.classList.contains("open")) closeModal(); });
  modal.querySelectorAll(".auth-tabs button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  $(".auth-card", modal).addEventListener("submit", submit);
  $("#authRole", modal).addEventListener("change", paintRoleFields);
  if (!staffSignup.doctor && !staffSignup.frontdesk) $("#authRoleBox", modal).remove();   // no staff sign-up: everyone is a patient
  setMode("signin");
  fillDoctorChoices();
}

/** Staff choices only appear for roles that need them: doctors pick a specialty and clinic, both kinds type the access code. */
function paintRoleFields() {
  const role = $("#authRole", modal).value;
  $("#authDoctorBox", modal).hidden = role !== "doctor";
  $("#authCodeBox", modal).hidden = role === "patient";
}
async function fillDoctorChoices() {
  if (!$("#authSpecialty", modal)) return;
  try {
    const doctors = await (await fetch("/api/doctors")).json();
    const options = (key) => [...new Set(doctors.map((d) => d[key]))].sort().map((v) => `<option>${v}</option>`).join("");
    $("#authSpecialty", modal).innerHTML = options("specialty");
    $("#authClinic", modal).innerHTML = options("clinic");
  } catch { /* the doctor option then fails with a clear message */ }
}

/** Ask the server to make this new account a doctor or front-desk person. The server checks the code. */
async function claimRole(claim) {
  const res = await fetch("/api/me/role", { method: "POST", headers: { "Content-Type": "application/json", ...headers() }, body: JSON.stringify(claim) });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Could not set up your staff account.");
}

function setMode(next) {
  mode = next;
  modal.querySelectorAll(".auth-tabs button").forEach((b) => b.classList.toggle("on", b.dataset.mode === next));
  modal.querySelectorAll("[data-only=signup]").forEach((el) => { el.style.display = next === "signup" ? "" : "none"; });
  $("#authGo", modal).textContent = next === "signup" ? "Create my account" : "Sign in";
  $("#authPass", modal).autocomplete = next === "signup" ? "new-password" : "current-password";
  message("");
}
function message(text, good = false) { const m = $("#authMsg", modal); m.textContent = text; m.classList.toggle("ok", good); }
function openModal() { if (!sb) return; modal.classList.add("open"); setTimeout(() => $(mode === "signup" ? "#authName" : "#authEmail", modal).focus(), 300); }
function closeModal() { modal?.classList.remove("open"); }

async function submit(e) {
  e.preventDefault();
  const email = $("#authEmail", modal).value.trim(), password = $("#authPass", modal).value;
  if (!email || password.length < 6) return message("Enter your email and a password of at least 6 characters.");
  const go = $("#authGo", modal); go.disabled = true; message("");
  const role = $("#authRole", modal)?.value || "patient";      // also used when signing in: it lets staff who registered as patients, or who confirmed their email first, claim their role
  const claim = role === "patient" ? null : { role, code: $("#authCode", modal).value, specialty: $("#authSpecialty", modal).value, clinic: $("#authClinic", modal).value };
  if (claim && !claim.code) { go.disabled = false; return message("Enter the staff access code."); }
  try {
    const result = mode === "signup"
      ? await sb.auth.signUp({ email, password, options: { data: { full_name: $("#authName", modal).value.trim() || email.split("@")[0] } } })
      : await sb.auth.signInWithPassword({ email, password });
    if (result.error) throw result.error;
    if (!result.data.session) {      // e-mail confirmation is on: they confirm, then sign in again with the same role and code
      message(claim ? "Check your email to confirm your account, then sign in here again with the same role and staff code." : "Check your email to confirm your account, then sign in.", true); return;
    }
    if (claim) {
      session = result.data.session;
      try {
        if (mode === "signin") await refreshMe();
        if (mode === "signup" || me?.role === "patient") await claimRole(claim);     // someone who is already staff just signs in
      } catch (err) { message(err.message + " You are signed in as a patient for now."); return; }
      await afterIdentityChange();                                                   // pick up the new role now: the page then opens their own portal
    }
    closeModal();
  } catch (err) {
    message(/invalid login/i.test(err.message) ? "That email and password do not match." : err.message);
  } finally { go.disabled = false; }
}

/* ---------- the button in the top navigation ---------- */
function buildNavButton() {
  const tools = $(".nav-tools");
  if (!tools) return;
  const box = document.createElement("div");
  box.className = "nav-auth";
  box.innerHTML = '<span class="who" id="authWho"></span><button class="btn small ghost" id="authBtn" type="button">Sign in</button>';
  tools.prepend(box);
  $("#authBtn").addEventListener("click", () => (signedIn() ? signOut() : openModal()));
}
function paintNav() {
  const btn = $("#authBtn"); if (!btn) return;
  btn.textContent = signedIn() ? "Sign out" : "Sign in";
  $("#authWho").textContent = signedIn() ? displayName() : "";
}
const signedIn = () => !!user() || !!me;
async function signOut() {
  session$.set(""); me = null;
  if (sb && user()) await sb.auth.signOut(); else location.href = "/";
}

/* ---------- start-up ---------- */
let lastIdentity = null;   // who we last told the page about, so repeated Supabase events do not restart the page
async function afterIdentityChange() {
  await syncProfile();
  await refreshMe();
  paintNav();
  routeByRole();
  const identity = `${me?.id || "nobody"}:${me?.role || ""}`;
  if (identity !== lastIdentity) {              // only announce a real change: signed in, signed out, or a new role
    const first = lastIdentity === null;
    lastIdentity = identity;
    window.dispatchEvent(new CustomEvent("sahyog:auth", { detail: { signedIn: signedIn(), role: me?.role || null, first } }));
  }
  resolveReady();
}

async function start() {
  let config = {};
  try { config = await (await fetch("/api/config")).json(); } catch { /* server not reachable: stay signed out */ }
  api.demoLogin = !!config.demo_login;
  staffSignup = { ...staffSignup, ...(config.staff_signup || {}) };
  // Local demos only: open any page with ?as=d1 (doctor), ?as=fd1 (front desk) or ?as=p1 (patient) when the server allows it.
  const asParam = new URLSearchParams(location.search).get("as");
  if (asParam && config.demo_login) { session$.set(asParam); history.replaceState(null, "", location.pathname); }

  if (config.auth_enabled && window.supabase?.createClient) {
    sb = window.supabase.createClient(config.supabase_url, config.supabase_key);
    buildModal();
    buildNavButton();
    window.dispatchEvent(new Event("sahyog:paint-bot"));   // draw the bot inside the sign-in window too
    // setTimeout(...,0): Supabase asks us not to call it again from inside this callback directly.
    sb.auth.onAuthStateChange((event, next) => {
      session = next;
      if (event === "TOKEN_REFRESHED") return;
      setTimeout(afterIdentityChange, 0);
    });
  } else {
    buildNavButton();
    await afterIdentityChange();
  }
  api.ready = true;
}

const api = {
  ready: false, demoLogin: false,
  user, name: displayName, seed, headers, saveSeed, saveMessage, loadHistory, openModal, closeModal,
  signedIn, enabled: () => !!sb, whenReady: () => readyPromise,
  me: () => me, role: () => me?.role || null, home: (role) => HOME[role] || "/",
};
window.SahyogAuth = api;
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
