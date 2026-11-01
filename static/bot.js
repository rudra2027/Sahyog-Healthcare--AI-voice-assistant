/* =====================================================================
   Sahyog assistant widget: the round bot in the bottom-right corner of every page.

   To put it on another page, add these three lines before </body>:
     <link rel="stylesheet" href="/static/bot.css">
     <script src="/static/auth.js"></script>   (sign-in, optional)
     <script src="/static/bot.js"></script>

   Parts of this file:
     1 seeded randomness     4 voice (speaks + measures loudness)
     2 drawing the bot       5 the widget (launcher, greeting, panel)
     3 small helpers         6 chat, microphone, start-up
   ===================================================================== */
(() => {
"use strict";

/* ---------- 3. small helpers (written first because everything below uses them) ---------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
// localStorage can be blocked (private windows), so every use is wrapped.
const store = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* ignore */ } },
};
const fmtTime = (s) => new Date(s.replace(" ", "T")).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });

/* ---------- 1. seeded randomness: the same seed ALWAYS gives the same bot ---------- */
// "hash" turns any text into a number, "rng" turns that number into a stream of random-looking numbers.
function hash(text) {
  let h = 1779033703 ^ text.length;
  for (let i = 0; i < text.length; i++) { h = Math.imul(h ^ text.charCodeAt(i), 3432918353); h = (h << 13) | (h >>> 19); }
  h = Math.imul(h ^ (h >>> 16), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909);
  return (h ^= h >>> 16) >>> 0;
}
function rng(seed) {
  let a = hash(String(seed));
  return () => { a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
const NAMES = ["Mira", "Kabir", "Tara", "Zoya", "Ishaan", "Noor", "Anaya", "Rohan", "Sia", "Dev", "Veda", "Arjun", "Leela", "Neel", "Isha", "Ayaan"];

/** Everything that makes one bot different from another, decided from the seed. */
function traitsFor(seed) {
  const r = rng(seed);
  const pick = (list) => list[Math.floor(r() * list.length)];
  const hue = Math.floor(r() * 360);
  return {
    hue, hue2: (hue + pick([40, 150, 180, 210, 300])) % 360,
    head: pick(["round", "squircle", "wide", "bean"]),
    eyes: pick(["dots", "tall", "happy", "glow"]),
    antenna: pick(["none", "ball", "twin", "leaf", "cross"]),
    ears: pick(["none", "discs", "fins"]),
    pattern: pick(["none", "stripes", "dots", "spark"]),
    cheeks: r() > 0.5, mouthW: 9 + Math.floor(r() * 8),
    float: (2.6 + r() * 2).toFixed(2) + "s", tilt: (5 + r() * 5).toFixed(2) + "s", blink: (3 + r() * 3).toFixed(2) + "s", delay: (-r() * 3).toFixed(2) + "s",
    name: pick(NAMES),
  };
}

/* ---------- 2. drawing the bot: one SVG string built from the traits ---------- */
let drawCount = 0; // every drawing needs its own element ids
function botSvg(t) {
  const id = `sbh${++drawCount}`;
  const skin = `hsl(${t.hue} 72% 56%)`, edge = `hsl(${t.hue} 70% 38%)`, plate = `hsl(${t.hue} 42% 13%)`;
  const light = `hsl(${t.hue2} 95% 72%)`, body = `hsl(${t.hue2} 62% 48%)`;
  // the head shape (drawn once, then reused for the colour AND as a cut-out for the pattern)
  const heads = {
    round: '<circle cx="60" cy="62" r="38"/>', squircle: '<rect x="22" y="26" width="76" height="72" rx="30"/>',
    wide: '<rect x="16" y="34" width="88" height="62" rx="24"/>', bean: '<ellipse cx="60" cy="62" rx="41" ry="35"/>',
  };
  const eyes = {
    dots: `<circle cx="47" cy="60" r="4.8" fill="${light}"/><circle cx="73" cy="60" r="4.8" fill="${light}"/>`,
    tall: `<ellipse cx="47" cy="60" rx="4" ry="7.5" fill="${light}"/><ellipse cx="73" cy="60" rx="4" ry="7.5" fill="${light}"/>`,
    happy: `<path d="M41 63q6-9 12 0M67 63q6-9 12 0" fill="none" stroke="${light}" stroke-width="3.6" stroke-linecap="round"/>`,
    glow: `<circle cx="47" cy="60" r="9" fill="${light}" opacity=".22"/><circle cx="73" cy="60" r="9" fill="${light}" opacity=".22"/><circle cx="47" cy="60" r="5.6" fill="${light}"/><circle cx="73" cy="60" r="5.6" fill="${light}"/><circle cx="45.5" cy="58" r="1.6" fill="#fff"/><circle cx="71.5" cy="58" r="1.6" fill="#fff"/>`,
  };
  const antenna = {
    none: "",
    ball: `<path d="M60 26V14" stroke="${edge}" stroke-width="3" stroke-linecap="round"/><circle class="sb-glow" cx="60" cy="11" r="5" fill="${light}" style="color:${light}"/>`,
    twin: `<path d="M52 27L46 13M68 27L74 13" stroke="${edge}" stroke-width="3" stroke-linecap="round"/><circle class="sb-glow" cx="46" cy="11" r="3.6" fill="${light}" style="color:${light}"/><circle class="sb-glow" cx="74" cy="11" r="3.6" fill="${light}" style="color:${light}"/>`,
    leaf: `<path d="M60 27V19" stroke="${edge}" stroke-width="3" stroke-linecap="round"/><path class="sb-glow" d="M60 20C58 8 70 5 80 8 77 18 69 21 60 20Z" fill="${light}" style="color:${light}"/>`,
    cross: `<path d="M60 26V17" stroke="${edge}" stroke-width="3" stroke-linecap="round"/><path class="sb-glow" d="M60 4v14M53 11h14" stroke="#fff" stroke-width="4.4" stroke-linecap="round" style="color:#fff"/>`,
  };
  const ears = {
    none: "",
    discs: `<circle cx="15" cy="64" r="7" fill="${edge}"/><circle cx="105" cy="64" r="7" fill="${edge}"/>`,
    fins: `<path d="M22 56L8 46 10 74 22 70Z M98 56L112 46 110 74 98 70Z" fill="${edge}"/>`,
  };
  const pattern = {
    none: "",
    stripes: '<g opacity=".28" fill="#fff"><rect x="10" y="30" width="100" height="5"/><rect x="10" y="39" width="100" height="3"/></g>',
    dots: '<g opacity=".5" fill="#fff"><circle cx="34" cy="40" r="3"/><circle cx="86" cy="40" r="3"/><circle cx="60" cy="35" r="2.4"/><circle cx="46" cy="90" r="2.4"/><circle cx="76" cy="91" r="2.4"/></g>',
    spark: '<path d="M88 34l2.4 6 6 2.4-6 2.4L88 51l-2.4-6-6-2.4 6-2.4z" fill="#fff" opacity=".85"/>',
  };
  return `<svg class="sb-av" viewBox="0 0 120 120" role="img" aria-label="${esc(t.name)}, your Sahyog assistant" style="--float:${t.float};--tilt:${t.tilt};--blink:${t.blink};--delay:${t.delay}">
    <defs><g id="${id}">${heads[t.head]}</g><clipPath id="${id}c"><use href="#${id}"/></clipPath></defs>
    <g class="sb-float">
      <rect x="38" y="93" width="44" height="22" rx="11" fill="${body}"/><path d="M60 98v12M54 104h12" stroke="#fff" stroke-width="3.4" stroke-linecap="round"/>
      <rect class="sb-arm sb-wave" x="82" y="96" width="9" height="19" rx="4.5" fill="${body}"/>
      <g class="sb-tilt">
        ${antenna[t.antenna]}${ears[t.ears]}
        <use href="#${id}" fill="${skin}" stroke="${edge}" stroke-width="3"/>
        <g clip-path="url(#${id}c)">${pattern[t.pattern]}<ellipse cx="44" cy="40" rx="22" ry="9" fill="#fff" opacity=".16"/></g>
        <rect x="27" y="44" width="66" height="40" rx="20" fill="${plate}"/>
        <g class="sb-look"><g class="sb-eyes">${eyes[t.eyes]}</g>
          ${t.cheeks ? `<circle cx="36" cy="72" r="4" fill="${light}" opacity=".28"/><circle cx="84" cy="72" r="4" fill="${light}" opacity=".28"/>` : ""}
          <ellipse class="sb-mouth" cx="60" cy="73" rx="${t.mouthW / 2}" ry="4" fill="${light}"/></g>
      </g>
    </g></svg>`;
}

/* ---------- 4. voice: speaks the replies one sentence at a time and measures how loud it is ---------- */
// The page's own chat (app.js) uses this too, so ONE voice engine drives every animation.
const Voice = {
  level: 0,            // 0 (silent) to 1 (loud): the bot's mouth follows this number
  speaking: false,
  token: 0,            // bumped on stop(), so sentences still being prepared know they are out of date
  audio: null, ctx: null, listeners: new Set(),

  on(fn) { this.listeners.add(fn); },
  _emit() { this.listeners.forEach((fn) => fn(this.speaking)); },

  /** Browsers only allow sound after a click, so the first click anywhere unlocks the audio engine. */
  unlock() {
    if (this.ctx) { if (this.ctx.state === "suspended") this.ctx.resume(); return; }
    try { this.ctx = new (window.AudioContext || window.webkitAudioContext)(); } catch { this.ctx = null; }
  },

  stop() {
    this.token++;
    if (this.audio) { this.audio.pause(); this.audio = null; }
    this.speaking = false; this.level = 0; this._emit();
  },

  /** Play one audio clip and move `level` with its loudness. Resolves when it ends. */
  _play(blob, onPlaying) {
    return new Promise((resolve) => {
      const audio = new Audio(URL.createObjectURL(blob));
      this.audio = audio;
      let analyser = null, samples = null;
      try {   // an "analyser" is a tiny tap on the audio that tells us how loud it is right now
        if (this.ctx) {
          const source = this.ctx.createMediaElementSource(audio);
          analyser = this.ctx.createAnalyser(); analyser.fftSize = 256;
          samples = new Uint8Array(analyser.fftSize);
          source.connect(analyser); analyser.connect(this.ctx.destination);
        }
      } catch { analyser = null; }
      const tick = () => {
        if (this.audio !== audio) return;
        let level;
        if (analyser) {   // loudness = average distance of the wave from the centre line
          analyser.getByteTimeDomainData(samples);
          let sum = 0; for (const v of samples) sum += Math.abs(v - 128);
          level = Math.min(1, (sum / samples.length / 128) * 5);
        } else { level = 0.3 + 0.3 * Math.sin(performance.now() / 70); }   // no analyser: a gentle fake wobble
        this.level += (level - this.level) * 0.5;   // smooth, so the mouth does not flicker
        requestAnimationFrame(tick);
      };
      audio.onplaying = () => { this.speaking = true; this._emit(); onPlaying?.(); requestAnimationFrame(tick); };
      audio.onended = audio.onerror = () => { if (this.audio === audio) { this.speaking = false; this.level = 0; this._emit(); } resolve(); };
      audio.play().catch(() => resolve());
    });
  },

  /** Speak a reply. All sentences are requested at once and play in order, so the first one starts fast. */
  async speak(text, agent, { onFirst, onUnavailable } = {}) {
    this.stop();
    const token = this.token;
    const sentences = (text.replace(/\(Source:[^)]*\)/g, "").match(/[^.!?]+[.!?]*/g) || []).map((s) => s.trim()).filter(Boolean);
    const clips = sentences.map((s) =>
      fetch("/api/tts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: s, agent }) })
        .then((r) => (r.ok ? r.blob() : null)).catch(() => null));
    for (let i = 0; i < clips.length; i++) {
      const blob = await clips[i];
      if (token !== this.token) return;
      if (!blob) { onUnavailable?.(); return; }
      await this._play(blob, i === 0 ? onFirst : undefined);
      if (token !== this.token) return;
    }
  },
};
window.SahyogVoice = Voice;
addEventListener("pointerdown", () => Voice.unlock(), { passive: true });
addEventListener("keydown", () => Voice.unlock(), { passive: true });

/* ---------- 5. the widget: launcher, greeting bubble, panel ---------- */
// What each kind of person can ask. Patients book and ask about their reports; doctors and the front desk
// ask about the day's work. The server decides what each role is allowed to see, whatever the page says.
const ROLES = {
  patient: {
    chips: [["I have chest pain", "My mother has an ache in her left arm and some breathlessness since this morning."],
      ["Back pain, 3 weeks", "Dull lower back pain for three weeks, worse after long sitting. No fever, no weakness in the legs."],
      ["What does a GP do?", "What does a general physician do?"], ["Ask about a report", "What does my report say about cholesterol?"]],
    placeholder: "Describe your symptoms...", agent: "triage",
    nudge: "Not feeling well? Tell me what is wrong and I will find your doctor.",
    hello: (who, name) => `${who ? `Hello ${who}.` : "Hello."} I am ${name}. Just talk, I am listening, or type below. Tell me what is wrong, or ask about a report.`,
  },
  doctor: {
    chips: [["My schedule today", "What is my schedule today?"], ["Who is next?", "Who is my next patient?"],
      ["Tomorrow", "What appointments do I have tomorrow?"]],
    placeholder: "Ask about your day or a patient...", agent: "records",
    nudge: "Ask me about your schedule, your next patient, or a patient's reports.",
    hello: (who, name) => `Hello ${who}. I am ${name}. Ask me about your schedule, your next patient, or a patient's details and reports.`,
  },
  frontdesk: {
    chips: [["Doctors free now", "Which doctors are available today?"], ["Bookings today", "How many appointments are there today?"],
      ["Open tickets", "Are there any open tickets?"], ["Free cardiology slot", "What is the next free slot for cardiology?"]],
    placeholder: "Try: move Priya to 4 pm tomorrow...", agent: "triage",
    nudge: "I can move a booking, change a doctor, or block a doctor's day. Just tell me.",
    hello: (who, name) => `Hello ${who}. I am ${name}. Tell me what to change, for example: move Priya to 4 pm tomorrow, or Dr. Rao is on leave today. I always ask before I change anything.`,
  },
};
ROLES.compliance = ROLES.frontdesk;
const roleNow = () => window.SahyogAuth?.role?.() || "patient";
const isStaff = () => roleNow() !== "patient";
const staffPage = () => ["doctor", "desk"].includes(document.body.dataset.portal);
const bot = { seed: null, t: null, open: false, thread: null, greeted: false, muted: store.get("sahyog.mute") === "1", busy: false,
  micOn: true,   // hands-free listening is ON every visit; muting lasts only until the page is reloaded
  talking: false, talkId: 0, recRunning: false,
  micBlocked: null, starting: false, device: "", sttMode: "browser", transcribing: false };   // micBlocked = the reason the browser gave, if it refused
let root;          // the whole widget element
const avatars = new Set();   // every drawn bot on the page (launcher, panel, sign-in window) so they all move together

/** Which seed should this person's bot use? Signed in = saved/derived from their account. Otherwise a random one kept in this browser. */
function currentSeed() {
  const fromAccount = window.SahyogAuth?.seed?.();
  if (fromAccount) return fromAccount;
  let local = store.get("sahyog.seed");
  if (!local) { local = Math.random().toString(36).slice(2, 10); store.set("sahyog.seed", local); }
  return local;
}

/** Draw the bot into every place that shows it, and re-colour the widget. */
function paintBot() {
  bot.seed = currentSeed();
  bot.t = traitsFor(bot.seed);
  root.style.setProperty("--h", bot.t.hue);
  avatars.clear();
  $$("[data-bot]").forEach((slot) => { slot.innerHTML = botSvg(bot.t); avatars.add($(".sb-av", slot)); });
  $("#sbName").textContent = bot.t.name;
  $("#sbGreetName").textContent = `Hi, I am ${bot.t.name}`;
  $("#sbPanel").setAttribute("aria-label", `${bot.t.name}, Sahyog assistant`);
}
window.addEventListener("sahyog:paint-bot", () => root && paintBot());

/** Chips, placeholder and visibility follow the person's role. */
function applyRole() {
  const role = ROLES[roleNow()];
  $("#sbChips").innerHTML = role.chips.map(([label, text]) => `<button type="button" class="sb-chip" data-q="${esc(text)}">${esc(label)}</button>`).join("");
  $$("#sbChips .sb-chip").forEach((c) => c.addEventListener("click", () => { bot.thread = null; send(c.dataset.q); }));
  $("#sbInput").placeholder = role.placeholder;
  $("#sbGreetText").textContent = role.nudge;
  root.style.display = staffPage() && !window.SahyogAuth?.role?.() ? "none" : "";   // staff pages hide the bot until someone is signed in
  if (typeof paintMic === "function" && recognition !== undefined) paintMic();
}

function setState(name) { root.dataset.state = name; $("#sbCaption").textContent = { speaking: "Speaking...", listening: "Listening...", thinking: "Thinking...", idle: "" }[name] || ""; }

/** Per animation frame: copy the voice loudness into the CSS variables that move the mouth and the rings. */
function animate() {
  const level = Voice.level;
  for (const av of avatars) av.style.setProperty("--mouth", level.toFixed(3));
  root.style.setProperty("--level", level.toFixed(3));
  if (micAnalyser && bot.open) $("#sbMic").style.setProperty("--ml", micLevel().toFixed(3));
  requestAnimationFrame(animate);
}

/** Eyes follow the mouse a little. Cheap, and it makes the bot feel alive. */
function lookAtPointer(e) {
  for (const av of avatars) {
    const box = av.getBoundingClientRect();
    const dx = (e.clientX - (box.left + box.width / 2)) / innerWidth, dy = (e.clientY - (box.top + box.height / 2)) / innerHeight;
    av.style.setProperty("--lx", (dx * 7).toFixed(2)); av.style.setProperty("--ly", (dy * 5).toFixed(2));
  }
}

function buildWidget() {
  root = document.createElement("div");
  root.className = "sb";
  root.id = "sahyogBot";
  root.dataset.state = "idle";
  root.innerHTML = `
    <div class="sb-greet" id="sbGreet" hidden role="status">
      <button class="sb-greet-x" aria-label="Dismiss">&times;</button>
      <b id="sbGreetName">Hi</b><span id="sbGreetText"></span>
    </div>
    <button class="sb-launch" id="sbLaunch" aria-label="Open the Sahyog assistant"><span data-bot></span><span class="sb-dot"></span></button>
    <section class="sb-panel" id="sbPanel" role="dialog" aria-modal="false">
      <div class="sb-head">
        <div class="sb-title"><b id="sbName">Sahyog</b><span>Sahyog assistant · online</span></div>
        <button class="sb-iconbtn" id="sbMute" aria-label="Turn the voice off" title="Voice on/off"></button>
        <button class="sb-iconbtn spin" id="sbDice" aria-label="Give me a new look" title="New look"></button>
        <button class="sb-iconbtn" id="sbClose" aria-label="Close"></button>
      </div>
      <div class="sb-stage"><i class="sb-ring"></i><i class="sb-ring"></i><span data-bot id="sbStageBot"></span><div class="sb-caption" id="sbCaption"></div></div>
      <div class="sb-msgs" id="sbMsgs" aria-live="polite"></div>
      <div class="sb-chips" id="sbChips"></div>
      <form class="sb-form" id="sbForm">
        <button type="button" class="sb-round mic" id="sbMic" aria-label="Mute the microphone" title="Microphone on: just talk. Tap to mute. Hold to talk right now."></button>
        <input id="sbInput" autocomplete="off" placeholder="Describe your symptoms..." aria-label="Message">
        <button class="sb-round" aria-label="Send"></button>
      </form>
    </section>`;
  document.body.append(root);

  // small inline icons (the page's icon sprite may not exist on other pages)
  const svg = (d) => `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
  bot.icons = {
    on: svg('<path d="M11 5 6 9H3v6h3l5 4zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>'),
    off: svg('<path d="M11 5 6 9H3v6h3l5 4zM22 9l-6 6M16 9l6 6"/>'),
  };
  $("#sbMute").innerHTML = bot.muted ? bot.icons.off : bot.icons.on;
  $("#sbMute").classList.toggle("off", bot.muted);
  $("#sbDice").innerHTML = svg('<rect x="3" y="3" width="18" height="18" rx="4"/><circle cx="8.5" cy="8.5" r="1.2"/><circle cx="15.5" cy="15.5" r="1.2"/><circle cx="12" cy="12" r="1.2"/>');
  $("#sbClose").innerHTML = svg('<path d="M6 6l12 12M18 6 6 18"/>');
  bot.icons.micOn = svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>');
  bot.icons.micOff = svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M4 4l16 16"/>');
  paintMic();
  $("#sbForm .sb-round:last-child").innerHTML = svg('<path d="M5 12h14M13 6l6 6-6 6"/>');

  paintBot();
  applyRole();

  $("#sbLaunch").addEventListener("click", () => toggle(true));
  $("#sbClose").addEventListener("click", () => toggle(false));
  $("#sbGreet").addEventListener("click", (e) => { if (!e.target.closest(".sb-greet-x")) toggle(true); });
  $(".sb-greet-x").addEventListener("click", (e) => { e.stopPropagation(); dismissGreeting(); });
  $("#sbStageBot").addEventListener("click", () => { Voice.stop(); setState("idle"); });   // tap the bot to make it stop talking
  $("#sbMute").addEventListener("click", () => {
    bot.muted = !bot.muted; store.set("sahyog.mute", bot.muted ? "1" : "0");
    $("#sbMute").innerHTML = bot.muted ? bot.icons.off : bot.icons.on; $("#sbMute").classList.toggle("off", bot.muted);
    $("#sbMute").setAttribute("aria-label", bot.muted ? "Turn the voice on" : "Turn the voice off");
    if (bot.muted) { Voice.stop(); setState("idle"); }
  });
  $("#sbDice").addEventListener("click", async () => {   // a brand-new look, saved to the account when signed in
    const fresh = Math.random().toString(36).slice(2, 10);
    store.set("sahyog.seed", fresh);
    await window.SahyogAuth?.saveSeed?.(fresh);
    paintBot();
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && bot.open) toggle(false); });
  if (!reduceMotion) addEventListener("pointermove", lookAtPointer, { passive: true });
  Voice.on((speaking) => { if (speaking) setState("speaking"); else if (root.dataset.state === "speaking") setState("idle"); });
  requestAnimationFrame(animate);

  // the greeting bubble pops up a few seconds after arrival, once per visit
  let shown = false; try { shown = sessionStorage.getItem("sahyog.greeted") === "1"; } catch { /* ignore */ }
  if (!shown) setTimeout(() => { if (!bot.open) $("#sbGreet").hidden = false; }, 2600);
}

function dismissGreeting() { $("#sbGreet").hidden = true; try { sessionStorage.setItem("sahyog.greeted", "1"); } catch { /* ignore */ } }

function toggle(open) {
  bot.open = open;
  root.classList.toggle("open", open);
  dismissGreeting();
  if (open) {
    Voice.unlock();
    if (!bot.greeted) { bot.greeted = true; greet(); }
    setTimeout(() => $("#sbInput").focus(), 350);
    setTimeout(resumeListening, 600);   // hands-free: start listening (waits while the greeting is being spoken)
  } else { Voice.stop(); stopListening(); releaseMicStream(); setState("idle"); }
}

/* ---------- 6. chat, microphone, start-up ---------- */
const msgs = () => $("#sbMsgs");
function bubble(role, html, extra = "") {
  const b = document.createElement("div");
  b.className = `sb-b ${role} ${extra}`;
  b.innerHTML = html;
  msgs().append(b);
  msgs().scrollTop = msgs().scrollHeight;
  return b;
}

function greet() {
  const who = window.SahyogAuth?.name?.();
  const text = ROLES[roleNow()].hello(who, bot.t.name);
  bubble("bot", esc(text));
  say(text, "triage");
  window.SahyogAuth?.loadHistory?.().then((rows) => {   // past messages of a signed-in person, if the table exists
    if (!rows?.length) return;
    const first = msgs().firstChild;
    rows.forEach((m) => { const b = document.createElement("div"); b.className = `sb-b ${m.role}`; b.textContent = m.content; msgs().insertBefore(b, first); });
  });
}

/** Speak through the shared voice, unless the person muted it. The microphone is paused while the bot
 *  talks (so it does not hear itself) and listens again as soon as the bot has finished. */
async function say(text, agent) {
  if (bot.muted) { resumeListening(); return; }
  const id = ++bot.talkId;
  bot.talking = true; stopListening();
  await Voice.speak(text, agent, { onUnavailable: () => { $("#sbCaption").textContent = "Voice is unavailable. Replies stay on screen."; } });
  if (id === bot.talkId) { bot.talking = false; setTimeout(resumeListening, 400); }   // a newer reply may already be speaking
}

/** Headers that tell the server who is asking: the Supabase token when signed in, else a demo persona. */
function authHeaders() {
  return window.SahyogAuth?.headers?.() || { "X-User-Id": "p1" };
}
async function post(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify(body) });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return res.json();
}

const agentFor = (res) => (res.outcome === "emergency" ? "emergency" : res.citations?.length ? "records" : ROLES[roleNow()].agent);

async function send(text) {
  text = (text || "").trim();
  if (!text || bot.busy) return;
  bot.busy = true;
  Voice.stop(); stopListening();
  $("#sbInput").value = "";
  bubble("user", esc(text));
  window.SahyogAuth?.saveMessage?.("user", text);
  const typing = bubble("bot", '<span class="sb-typing"><i></i><i></i><i></i></span>');
  setState("thinking");
  try {
    const res = isStaff() ? await post("/api/assistant", { message: text, patient_id: window.SahyogContext?.patientId || null }) : await post("/api/chat", { message: text, thread_id: bot.thread, about: null });
    typing.remove();
    bot.thread = res.thread_id || null;
    showReply(res);
  } catch (err) {
    typing.remove();
    setState("idle");
    if (err.status === 401) {
      const b = bubble("bot", "Please sign in so I know who you are.<div class='sb-link'><button class='sb-chip' type='button'>Sign in</button></div>");
      $(".sb-chip", b).onclick = () => window.SahyogAuth?.openModal?.();
    } else bubble("bot", esc(err.message), "emergency");
    setTimeout(resumeListening, 300);
  } finally { bot.busy = false; }
}

function showReply(res) {
  setState("idle");
  const sources = (res.reply.match(/\(Source: ([^)]*)\)/) || [])[1];
  const clean = res.reply.replace(/\s*\(Source:[^)]*\)/, "");
  const chips = sources ? sources.split("; ").map((s) => `<span class="src">${esc(s)}</span>`).join("") : "";
  bubble("bot", esc(clean) + (chips ? `<div>${chips}</div>` : ""), res.outcome === "emergency" ? "emergency" : "");
  window.SahyogAuth?.saveMessage?.("bot", clean);
  const showPatient = res.patient_id && !res.changed && res.outcome !== "confirm";     // a record question opens that patient; a plan waiting for a yes does not move the screen
  if (res.changed || showPatient) window.dispatchEvent(new CustomEvent("sahyog:desk", { detail: { changed: !!res.changed, patient_id: showPatient ? res.patient_id : null } }));   // the front desk console refreshes itself
  if (res.options?.length) showOptions(res.options, res.thread_id);
  say(res.reply, agentFor(res));
}

function showOptions(options, thread) {
  const box = document.createElement("div");
  box.className = "sb-opts";
  options.forEach((o, i) => {
    const row = document.createElement("div");
    row.className = "sb-opt"; row.style.animationDelay = `${i * 110}ms`;
    row.innerHTML = `<div><b>${esc(o.name)}</b><small>${esc(o.clinic)} · ${esc(o.specialty)}<br>${esc(fmtTime(o.starts_at))}</small></div>`;
    const btn = Object.assign(document.createElement("button"), { className: "sb-chip", textContent: "Book", type: "button" });
    btn.onclick = async () => {
      $$("button", box).forEach((b) => (b.disabled = true));
      Voice.stop(); setState("thinking");
      try {
        const res = await post("/api/chat/resume", { thread_id: thread, slot_id: o.slot_id });
        box.style.opacity = ".45";
        bubble("user", `Book ${esc(o.name)}`);
        showReply(res);
      } catch (err) { setState("idle"); bubble("bot", esc(err.message), "emergency"); $$("button", box).forEach((b) => (b.disabled = false)); }
    };
    row.append(btn); box.append(row);
  });
  msgs().append(box);
  msgs().scrollTop = msgs().scrollHeight;
}

// Microphone: the browser turns speech into text (Chrome and Edge, and they need internet).
// Hands-free: it listens by itself whenever the panel is open and the bot is neither talking nor thinking.
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null, heard = "";

const wantListening = () => (!!recognition || bot.sttMode === "server") && !bot.transcribing && bot.micOn && !bot.micBlocked && bot.open && !bot.busy && !bot.talking && !Voice.speaking && !document.hidden;
// While the panel is open we hold the microphone stream. Two reasons: the browser asks permission clearly,
// and we can measure how loud YOU are (the ring around the mic button), which shows whether the right
// microphone is being heard even if speech-to-text has a problem.
let micStream = null, micAnalyser = null, micSamples = null, micCtx = null;
async function ensureMicStream() {
  if (micStream) return true;
  if (!navigator.mediaDevices?.getUserMedia) return true;   // very old browser: let SpeechRecognition ask by itself
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    bot.device = micStream.getAudioTracks()[0]?.label || "default microphone";
    try {   // its own audio context, so measuring the mic never depends on the speaker side
      micCtx = micCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (micCtx.state === "suspended") await micCtx.resume();
      const source = micCtx.createMediaStreamSource(micStream);
      micAnalyser = micCtx.createAnalyser(); micAnalyser.fftSize = 256; micSamples = new Uint8Array(256);
      source.connect(micAnalyser);   // measuring only: it is NOT connected to the speakers
    } catch { micAnalyser = null; }
    return true;
  } catch (err) {
    micProblem(err.name === "NotFoundError" ? "No microphone was found. You can still type."
      : "Microphone is blocked. Allow it in the address bar (lock icon), then click the mic.", err.name);
    return false;
  }
}
function releaseMicStream() {
  micStream?.getTracks().forEach((t) => t.stop());
  micStream = null; micAnalyser = null; $("#sbMic")?.style.setProperty("--ml", 0);
}
function micLevel() {   // 0 (quiet) to 1 (loud), from the live stream
  if (!micAnalyser) return 0;
  micAnalyser.getByteTimeDomainData(micSamples);
  let sum = 0; for (const v of micSamples) sum += Math.abs(v - 128);
  return Math.min(1, (sum / micSamples.length / 128) * 8);
}

function startListening() {
  if (!wantListening() || bot.recRunning || bot.starting) return;
  bot.starting = true;
  ensureMicStream().then((ok) => {
    bot.starting = false;
    if (!ok || !wantListening() || bot.recRunning) return;
    if (bot.sttMode === "server") startServerListening();
    else { heard = ""; try { recognition.start(); } catch { /* already running */ } }
  });
}
function stopListening() { heard = ""; stopServerListening(); try { recognition?.abort(); } catch { /* not running */ } }
const resumeListening = () => startListening();

// ---- Listening with the SERVER's speech-to-text (default) -------------------------------------
// We watch the loudness of the microphone ourselves ("voice activity detection"):
//   quiet -> loud   = the person started talking: start recording
//   loud -> quiet for ~1 second = they finished: upload the recording, get the words back, send them.
const vad = { rec: null, chunks: [], start: 0, quietSince: 0, floor: 0.02, peak: 0, raf: 0, active: false, cancelled: false };

function startServerListening() {
  if (vad.active) return;
  vad.active = true; bot.recRunning = true; vad.rec = null; vad.floor = 0.02;
  $("#sbMic").classList.add("listening"); setState("listening");
  $("#sbCaption").textContent = `Listening on ${bot.device.slice(0, 38)}. Say something...`;
  vad.raf = setInterval(vadTick, 40);   // a timer, not animation frames: those pause when the tab is hidden
}

function stopServerListening() {
  clearInterval(vad.raf);
  if (vad.rec) { vad.cancelled = true; try { vad.rec.stop(); } catch { /* already stopped */ } vad.rec = null; }
  vad.active = false; bot.recRunning = false; $("#sbMic")?.classList.remove("listening");
}

function vadTick() {
  if (!vad.active) return;
  if (!wantListening() && !vad.rec) { stopServerListening(); if (root.dataset.state === "listening") setState("idle"); return; }
  const level = micLevel(), now = performance.now();
  const threshold = Math.max(0.03, vad.floor * 2.2);        // "loud" = clearly above the room's background noise (works with quiet microphones)
  if (!vad.rec) {
    if (level > threshold) beginRecording(now);
    else {
      vad.floor += (level - vad.floor) * 0.02;              // learn how noisy the room is
      if (!vad.since) vad.since = now;
      if (now - vad.since > 12000) { $("#sbCaption").textContent = "I cannot hear you. Move closer, or press and hold the mic button to talk."; vad.since = now; }
    }
  } else {
    vad.peak = Math.max(vad.peak * 0.995, level);            // how loud this person is speaking
    const quiet = level < Math.max(threshold, vad.peak * 0.18);   // "finished" is relative, so a noisy room does not keep it open
    if (!quiet) vad.quietSince = 0;
    else if (!vad.quietSince) vad.quietSince = now;
    if ((vad.quietSince && now - vad.quietSince > 900) || now - vad.start > 20000) return finishRecording(now);
  }
}

function beginRecording(now) {
  const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"].find((m) => window.MediaRecorder?.isTypeSupported(m));
  vad.rec = new MediaRecorder(micStream, mime ? { mimeType: mime } : undefined);
  vad.chunks = []; vad.cancelled = false; vad.start = now; vad.quietSince = 0; vad.peak = 0; vad.since = 0;
  vad.rec.ondataavailable = (e) => { if (e.data.size) vad.chunks.push(e.data); };
  vad.rec.start();
  $("#sbCaption").textContent = "Hearing you...";
}

function finishRecording(now) {
  const rec = vad.rec, talked = now - vad.start - 900;       // minus the final stretch of silence
  clearInterval(vad.raf); vad.rec = null; vad.active = false; bot.recRunning = false;
  $("#sbMic").classList.remove("listening");
  rec.onstop = () => {
    const blob = new Blob(vad.chunks, { type: rec.mimeType || "audio/webm" });
    if (vad.cancelled || talked < 400) { setTimeout(startListening, 100); return; }   // a click or cough, not speech
    transcribe(blob);
  };
  rec.stop();
}

async function transcribe(blob) {
  bot.transcribing = true; setState("thinking"); $("#sbCaption").textContent = "Transcribing...";
  try {
    const form = new FormData(); form.append("file", blob, "voice.webm");
    const res = await fetch("/api/stt", { method: "POST", headers: authHeaders(), body: form });
    if (res.status === 503) {                                // server has no speech model: use the browser's service instead
      bot.sttMode = Recognition ? "browser" : "none"; bot.transcribing = false;
      if (bot.sttMode === "none") micProblem("Voice input is not available. You can still type.", "stt-503"); else startListening();
      return;
    }
    if (!res.ok) throw new Error(`stt ${res.status}`);
    const text = ((await res.json()).text || "").trim();
    bot.transcribing = false;
    console.debug("[mic] server heard:", JSON.stringify(text));
    if (text) { $("#sbInput").value = text; send(text); }
    else { setState("idle"); $("#sbCaption").textContent = "I did not catch that. Say it again."; setTimeout(startListening, 300); }
  } catch (err) {
    bot.transcribing = false; setState("idle");
    $("#sbCaption").textContent = "Could not transcribe that. Say it again, or type.";
    setTimeout(startListening, 1500);
  }
}

/** The microphone button shows whether the mic is on, muted by you, or refused by the browser. */
function paintMic() {
  const mic = $("#sbMic");
  const live = bot.micOn && !bot.micBlocked;
  mic.innerHTML = live ? bot.icons.micOn : bot.icons.micOff;
  mic.classList.toggle("off", !live);
  mic.setAttribute("aria-label", live ? "Mute the microphone" : "Turn the microphone on");
  mic.title = bot.micBlocked ? `Microphone problem: ${bot.micBlocked}. Click to try again.` : live ? "Microphone on: just talk. Click to mute." : "Microphone muted. Click to turn on.";
  $("#sbInput").placeholder = live && (Recognition || bot.sttMode === "server") ? "Listening... or type here" : ROLES[roleNow()].placeholder;
}
/** The browser refused or failed: say why, and stop retrying until the person clicks the mic again. */
function micProblem(text, code) {
  bot.micBlocked = code || text; paintMic();
  $("#sbCaption").textContent = text;
  console.warn("Sahyog microphone:", code || text);
}

function initMic() {
  const mic = $("#sbMic");
  if (!Recognition && bot.sttMode !== "server") { bot.micOn = false; paintMic(); mic.disabled = true; mic.title = "Voice input needs Chrome or Edge. You can still type."; $("#sbCaption").textContent = "Voice input needs Chrome or Edge. You can still type."; return; }
  if (Recognition) wireBrowserRecognition(mic);
  wireMicButton(mic);
  document.addEventListener("visibilitychange", () => { if (document.hidden) stopListening(); else resumeListening(); });
  paintMic();
}

function wireBrowserRecognition(mic) {
  recognition = new Recognition();
  recognition.lang = "en-IN"; recognition.interimResults = true; recognition.continuous = false;
  recognition.onstart = () => { bot.recRunning = true; mic.classList.add("listening"); setState("listening"); $("#sbCaption").textContent = `Listening on ${bot.device.slice(0, 38)}. Say something...`; };
  recognition.onspeechstart = () => { $("#sbCaption").textContent = "Hearing you..."; };
  recognition.onnomatch = () => { $("#sbCaption").textContent = "I could not make that out. Try again."; };
  recognition.onresult = (e) => { heard = [...e.results].map((r) => r[0].transcript).join(" "); $("#sbInput").value = heard; $("#sbCaption").textContent = heard.slice(-60); };   // show what is heard, live
  recognition.onend = () => {
    bot.recRunning = false; mic.classList.remove("listening");
    if (root.dataset.state === "listening") setState("idle");
    const text = heard.trim(); heard = "";
    console.debug("[mic] ended, heard:", JSON.stringify(text));
    if (text) send(text);                       // the person finished a sentence: send it
    else setTimeout(startListening, 300);       // silence: listen again
  };
  recognition.onerror = (e) => {
    if (e.error === "no-speech" || e.error === "aborted") return;   // normal: nobody spoke
    if (e.error === "not-allowed" || e.error === "service-not-allowed") micProblem("Microphone is blocked. Allow it in the address bar (lock icon), then click the mic.", e.error);
    else if (e.error === "audio-capture") micProblem("No microphone was found. You can still type.", e.error);
    else if (e.error === "network") micProblem("This browser's speech service is unreachable (needs internet; Chrome or Edge). You can still type.", e.error);
    else micProblem(`Microphone problem (${e.error}). You can still type.`, e.error);
  };
}

/** The microphone button:
 *    tap            = mute / unmute (it also retries after a problem)
 *    press and hold = talk right now, whatever the voice detection thinks; let go to send.
 *  Hold-to-talk is the safety net for very quiet microphones. */
function wireMicButton(mic) {
  let pressedAt = 0, holding = false, holdTimer = 0;
  mic.addEventListener("pointerdown", () => {
    pressedAt = performance.now(); holding = false;
    holdTimer = setTimeout(async () => {          // held for a moment: this is "talk now", not a tap
      if (!bot.micOn || bot.micBlocked || bot.busy || bot.transcribing || bot.sttMode !== "server") return;
      holding = true; Voice.stop(); bot.talking = false;
      if (!(await ensureMicStream())) return;
      if (!vad.active) startServerListening();
      if (!vad.rec) beginRecording(performance.now());
      $("#sbCaption").textContent = "Talking... let go of the mic to send";
    }, 350);
  });
  const release = () => {
    clearTimeout(holdTimer);
    if (holding && vad.rec) finishRecording(performance.now() + 900);     // let go = send
  };
  mic.addEventListener("pointerup", release);
  mic.addEventListener("pointerleave", release);
  mic.addEventListener("click", () => {
    if (holding || performance.now() - pressedAt > 350) { holding = false; return; }   // that was a hold, not a tap
    if (vad.rec) { finishRecording(performance.now() + 900); return; }   // mid-sentence tap = "that is all, send it"
    if (bot.micBlocked) { bot.micBlocked = null; bot.micOn = true; }   // try again
    else bot.micOn = !bot.micOn;
    paintMic();
    $("#sbCaption").textContent = "";
    if (bot.micOn) { Voice.stop(); bot.talking = false; startListening(); } else stopListening();
  });
}

async function start() {
  buildWidget();
  try {   // does the server transcribe speech itself ("server") or should we use the browser's own service?
    const config = await (await fetch("/api/config")).json();
    if (config.stt === "server" && window.MediaRecorder) bot.sttMode = "server";
  } catch { /* stay with the browser's speech service */ }
  initMic();
  $("#sbForm").addEventListener("submit", (e) => { e.preventDefault(); send($("#sbInput").value); });
  // When someone signs in or out the bot changes, and the conversation starts fresh.
  addEventListener("sahyog:auth", () => {
    paintBot(); applyRole(); bot.thread = null; bot.greeted = false; msgs().innerHTML = "";
    if (bot.open) { bot.greeted = true; greet(); }
  });
  window.SahyogBot = { _rec: () => recognition, _bot: bot, _vad: () => ({ ...vad, level: micLevel(), want: wantListening(), analyser: !!micAnalyser, ctx: micCtx?.state }), open: () => toggle(true), close: () => toggle(false), drawFor: (seed) => botSvg(traitsFor(seed)), traitsFor };
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
