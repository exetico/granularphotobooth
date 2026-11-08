/* Booth admin panel — talks to /api/admin/* with a Bearer token. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const TOKEN_KEY = "booth_admin_token";

  // ── Token handling: #token=… in the URL, or a PIN session ───────────
  let token = null;
  const hash = new URLSearchParams(location.hash.slice(1));
  if (hash.get("token")) {
    token = hash.get("token");
    try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* ignore */ }
    history.replaceState(null, "", location.pathname); // keep the token out of the address bar
  } else {
    try { token = sessionStorage.getItem(TOKEN_KEY); } catch { token = null; }
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (token) headers.Authorization = "Bearer " + token;
    if (opts.json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
    }
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    let data = null;
    try { data = await res.json(); } catch { /* not json */ }
    if (res.status === 403 && path !== "/api/admin/login") { logout(); throw new Error("Session expired"); }
    if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
    return data;
  }

  let flashTimer = null;
  function flash(msg, bad = false) {
    const el = $("flash-msg");
    el.textContent = msg;
    el.className = "flash" + (bad ? " bad" : "");
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => el.classList.add("hidden"), bad ? 7000 : 3500);
  }

  // ── Login ───────────────────────────────────────────────────────────
  function buildPinpad() {
    const pad = $("pinpad"), input = $("pin");
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "⌫", "0", "OK"].forEach((k) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = k;
      if (k === "OK") b.className = "go";
      b.addEventListener("click", () => {
        if (k === "⌫") input.value = input.value.slice(0, -1);
        else if (k === "OK") submitPin();
        else if (input.value.length < 16) input.value += k;
      });
      pad.appendChild(b);
    });
    // Typing / pasting into the field itself: keep digits only
    input.addEventListener("input", () => { input.value = input.value.replace(/\D/g, "").slice(0, 16); });
    document.addEventListener("keydown", (e) => {
      if ($("login").classList.contains("hidden")) return;
      if (e.key === "Enter") { e.preventDefault(); submitPin(); return; }
      if (document.activeElement === input) return; // the browser types into the field
      if (/^[0-9]$/.test(e.key) && input.value.length < 16) input.value += e.key;
      else if (e.key === "Backspace") input.value = input.value.slice(0, -1);
    });
  }

  async function submitPin() {
    const input = $("pin");
    $("login-error").textContent = "";
    try {
      const res = await api("/api/admin/login", { method: "POST", json: { pin: input.value } });
      token = res.token;
      try { sessionStorage.setItem(TOKEN_KEY, token); } catch { /* ignore */ }
      input.value = "";
      showDash();
    } catch (e) {
      input.value = "";
      $("login-error").textContent = e.message;
    }
  }

  async function showLogin() {
    $("dash").classList.add("hidden");
    $("btn-logout").classList.add("hidden");
    $("login").classList.remove("hidden");
    try {
      const info = await api("/api/admin/info");
      $("pin-area").classList.toggle("hidden", !info.pin_enabled);
      $("no-pin").classList.toggle("hidden", info.pin_enabled);
    } catch { /* server down */ }
  }

  function logout() {
    if (token) fetch("/api/admin/logout", { method: "POST", headers: { Authorization: "Bearer " + token } }).catch(() => {});
    token = null;
    try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
    stopPolling();
    showLogin();
  }
  $("btn-logout").addEventListener("click", logout);

  // ── Status ──────────────────────────────────────────────────────────
  let pollTimer = null;
  function stopPolling() { clearInterval(pollTimer); pollTimer = null; }

  function dd(label, value, cls = "") {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt"); dt.textContent = label;
    const d = document.createElement("dd"); d.textContent = value; if (cls) d.className = cls;
    wrap.append(dt, d);
    return wrap;
  }

  function formatMB(mb) {
    if (mb == null) return "—";
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
  }

  async function refreshStatus() {
    let s;
    try { s = await api("/api/admin/status"); } catch (e) {
      $("state-pill").textContent = "offline"; $("state-pill").className = "pill bad"; return;
    }
    const cam = s.camera || {};
    $("state-pill").textContent = s.state;
    $("state-pill").className = "pill " + (cam.connected ? "ok" : "bad");
    const list = $("status-list");
    list.replaceChildren(
      dd("Booth", s.state),
      dd("Camera", (cam.connected ? "✔ " : "✘ ") + (cam.message || "—"), cam.connected ? "ok" : "bad"),
      dd("Free disk", formatMB(s.disk_free_mb), s.disk_full ? "bad" : ""),
      dd("Sessions since start", String(s.sessions_completed)),
      dd("Screens connected", String(s.clients)),
      dd("Last error", s.last_error || "none", s.last_error ? "bad" : ""),
      dd("Version", s.version),
    );
  }

  $("btn-test").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try {
      const r = await api("/api/admin/camera/test", { method: "POST" });
      $("test-img").src = r.url;
      $("test-result").classList.remove("hidden");
    } catch (err) { flash(err.message, true); }
    e.target.disabled = false;
  });
  $("btn-reconnect").addEventListener("click", async () => {
    try { await api("/api/admin/camera/reconnect", { method: "POST" }); flash("Reconnecting camera…"); }
    catch (err) { flash(err.message, true); }
  });
  $("btn-restart").addEventListener("click", async () => {
    if (!confirm("Restart the photo booth app now? The screen will be back in a few seconds.")) return;
    try { await api("/api/admin/restart", { method: "POST" }); } catch { /* connection drops */ }
    flash("Restarting…");
    stopPolling();
    const wait = setInterval(async () => {
      try { const r = await fetch("/healthz", { cache: "no-store" }); if (r.ok) { clearInterval(wait); location.reload(); } }
      catch { /* still down */ }
    }, 1000);
  });

  // ── Settings form ───────────────────────────────────────────────────
  // [path, label, type, options/hint]
  const SECTIONS = [
    ["Session", [
      ["sequence.shots", "Photos per session", "number", { min: 1, max: 10 }],
      ["sequence.first_countdown_seconds", "Countdown before first photo (s)", "number", { min: 0, max: 120, nullable: true, hint: "Empty = same as below" }],
      ["sequence.countdown_seconds", "Countdown before each photo (s)", "number", { min: 0, max: 120 }],
      ["sequence.show_shot_seconds", "Show each photo for (s)", "number", { min: 0, max: 30, step: 0.5 }],
      ["sequence.preview_duration_seconds", "Show result for (s)", "number", { min: 1, max: 300 }],
      ["sequence.retake_mode", "Retake button", "select", { choices: [["restart", "Starts a new session"], ["idle", "Goes back to start screen"]] }],
    ]],
    ["Layout", [
      ["compositor.enabled", "Combine photos into a layout", "checkbox"],
      ["compositor.template_map", "Layout", "layout"],
      ["compositor.template_image", "Background image (under photos)", "image"],
      ["compositor.overlay_image", "Overlay image (on top of photos)", "image"],
      ["compositor.background_color", "Background colour", "color"],
    ]],
    ["Camera", [
      ["camera.backend", "Camera", "select", { choices: [["webcam", "Webcam / USB camera"], ["dslr_gphoto2", "DSLR / mirrorless (gphoto2)"], ["ip_cam", "IP / phone camera"], ["dummy", "Simulated (testing)"]] }],
      ["camera.fallback_to_webcam", "Use webcam if the camera is missing", "checkbox"],
      ["camera.backends.webcam.device_index", "Webcam number", "number", { min: 0, max: 20, hint: "0 = first camera" }],
      ["ui.mirror_preview", "Mirror the live view", "checkbox"],
    ]],
    ["Screen texts", [
      ["ui.texts.idle", "Start screen", "text"],
      ["ui.texts.smile", "At the shutter", "text"],
      ["ui.texts.shot_of", "Photo counter", "text", { hint: "{shot} and {total} are replaced" }],
      ["ui.texts.processing", "While processing", "text"],
      ["ui.texts.review_title", "Result title", "text"],
      ["ui.texts.done", "Done button", "text"],
      ["ui.texts.retake", "Retake button", "text"],
      ["ui.texts.camera_offline", "Camera missing message", "text"],
    ]],
    ["Kiosk & access", [
      ["app.kiosk_mode", "Full screen on first touch", "checkbox"],
      ["ui.tap_anywhere_to_start", "Tap anywhere to start", "checkbox"],
      ["admin.pin", "Admin PIN (8+ digits, empty = off)", "pin", { hint: "Tap the top-left corner of the booth 5× to open admin" }],
    ]],
  ];

  let rawConfig = {}, effective = {}, assets = { layouts: [], images: [] };

  const getPath = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);
  const hasPath = (obj, path) => getPath(obj, path) !== undefined;
  function setPath(obj, path, value) {
    const keys = path.split(".");
    let o = obj;
    keys.slice(0, -1).forEach((k) => { if (typeof o[k] !== "object" || o[k] === null) o[k] = {}; o = o[k]; });
    o[keys[keys.length - 1]] = value;
  }

  function fieldEl(path, label, type, opt = {}) {
    const wrap = document.createElement("div");
    wrap.className = "field" + (type === "checkbox" ? " check" : "");
    const id = "f_" + path.replace(/\./g, "_");
    const lab = document.createElement("label");
    lab.htmlFor = id; lab.textContent = label;
    let input;
    const value = getPath(effective, path);

    if (type === "select" || type === "layout" || type === "image") {
      input = document.createElement("select");
      let choices = opt.choices || [];
      if (type === "layout") choices = assets.layouts.map((l) => [l.path, `${l.path} (${l.slots} photos)`]);
      if (type === "image") choices = [["", "— none —"]].concat(assets.images.map((p) => [p, p]));
      if (value && !choices.some(([v]) => v === value)) choices.push([value, value + " (missing?)"]);
      choices.forEach(([v, t]) => { const o = document.createElement("option"); o.value = v; o.textContent = t; input.appendChild(o); });
      input.value = value ?? "";
    } else {
      input = document.createElement("input");
      input.type = { number: "number", checkbox: "checkbox", color: "color", pin: "password", text: "text" }[type];
      if (type === "pin") { input.inputMode = "numeric"; input.autocomplete = "new-password"; input.placeholder = value ? "•••••••• (set)" : "not set"; }
      if (type === "checkbox") input.checked = !!value;
      else if (type === "color") input.value = /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#ffffff";
      else if (type !== "pin") input.value = value ?? "";
      ["min", "max", "step"].forEach((k) => { if (opt[k] !== undefined) input[k] = opt[k]; });
    }
    input.id = id;
    input.dataset.path = path;
    input.dataset.type = type;
    if (opt.nullable) input.dataset.nullable = "1";
    input.addEventListener("change", () => wrap.classList.add("changed"));
    input.addEventListener("input", () => wrap.classList.add("changed"));

    if (type === "checkbox") wrap.append(input, lab); else wrap.append(lab, input);
    if (opt.hint) { const h = document.createElement("span"); h.className = "hint"; h.textContent = opt.hint; wrap.appendChild(h); }
    return wrap;
  }

  function buildForm() {
    const form = $("settings");
    form.replaceChildren();
    SECTIONS.forEach(([title, fields]) => {
      const fs = document.createElement("fieldset");
      const lg = document.createElement("legend"); lg.textContent = title;
      fs.appendChild(lg);
      fields.forEach(([p, l, t, o]) => fs.appendChild(fieldEl(p, l, t, o)));
      form.appendChild(fs);
    });
  }

  function readValue(input) {
    const t = input.dataset.type;
    if (t === "checkbox") return input.checked;
    if (t === "number") {
      if (input.value === "" && input.dataset.nullable) return null;
      return Number(input.value);
    }
    return input.value;
  }

  async function loadConfig() {
    const [cfg, a] = await Promise.all([api("/api/admin/config"), api("/api/admin/assets")]);
    rawConfig = cfg.config || {};
    effective = cfg.effective || {};
    assets = a;
    buildForm();
    $("raw-config").value = JSON.stringify(rawConfig, null, 2);
    const list = $("image-list");
    list.replaceChildren(...(assets.images.length ? assets.images : ["(no images yet)"]).map((p) => {
      const li = document.createElement("li"); li.textContent = p; return li;
    }));
  }

  async function saveConfig(newRaw) {
    try {
      const r = await api("/api/admin/config", { method: "POST", json: { config: newRaw } });
      flash("Saved ✔ " + (r.note || ""));
      await loadConfig();
    } catch (e) { flash(e.message, true); }
  }

  $("btn-save").addEventListener("click", async () => {
    const updated = JSON.parse(JSON.stringify(rawConfig));
    document.querySelectorAll("#settings [data-path]").forEach((input) => {
      const path = input.dataset.path;
      if (input.dataset.type === "pin") {
        if (input.value === "") return; // unchanged
        setPath(updated, path, input.value === "0" ? "" : input.value);
        return;
      }
      const v = readValue(input);
      if (input.dataset.type === "number" && v !== null && Number.isNaN(v)) return;
      if (hasPath(rawConfig, path) || JSON.stringify(v) !== JSON.stringify(getPath(effective, path))) {
        setPath(updated, path, v);
      }
    });
    await saveConfig(updated);
  });

  $("btn-save-raw").addEventListener("click", async () => {
    let parsed;
    try { parsed = JSON.parse($("raw-config").value); } catch (e) { flash("Not valid JSON: " + e.message, true); return; }
    await saveConfig(parsed);
  });

  $("upload").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch("/api/admin/upload", { method: "POST", body: fd, headers: { Authorization: "Bearer " + token } });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Upload failed");
      flash(`Uploaded ${data.path} (${data.size[0]}×${data.size[1]})`);
      await loadConfig();
    } catch (err) { flash(err.message, true); }
    e.target.value = "";
  });

  // ── Photos & logs ───────────────────────────────────────────────────
  async function loadPhotos() {
    try {
      const r = await api("/api/admin/photos?limit=24");
      $("photo-count").textContent = `${r.total} in total`;
      $("photos").replaceChildren(...r.photos.map((p) => {
        const a = document.createElement("a");
        a.href = p.url; a.target = "_blank"; a.rel = "noopener"; a.title = p.name;
        const img = document.createElement("img"); img.src = p.url; img.loading = "lazy"; img.alt = p.name;
        a.appendChild(img);
        return a;
      }));
    } catch { /* ignore */ }
  }

  async function loadLogs() {
    try {
      const r = await api("/api/admin/logs?lines=300");
      const pre = $("logs");
      pre.textContent = r.lines.join("\n");
      pre.scrollTop = pre.scrollHeight;
    } catch (e) { $("logs").textContent = e.message; }
  }
  $("btn-logs").addEventListener("click", loadLogs);
  $("logs-details").addEventListener("toggle", (e) => { if (e.target.open) loadLogs(); });

  // ── Boot ────────────────────────────────────────────────────────────
  async function showDash() {
    $("login").classList.add("hidden");
    $("dash").classList.remove("hidden");
    $("btn-logout").classList.remove("hidden");
    try {
      await refreshStatus();
      await loadConfig();
      loadPhotos();
    } catch (e) { if (token) flash(e.message, true); return; }
    stopPolling();
    pollTimer = setInterval(refreshStatus, 3000);
  }

  buildPinpad();
  if (token) showDash(); else showLogin();
})();
