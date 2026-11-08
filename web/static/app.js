/**
 * GranularPhotoBooth — kiosk client.
 *
 * The server is the single source of truth.  This page only renders the
 * state it is told about, and re-syncs itself whenever anything looks off:
 *   - on (re)connect the server sends a full "hello" snapshot
 *   - if the server was restarted (new boot id) the page reloads itself,
 *     so the kiosk always runs code that matches the backend
 *   - the live view re-attaches automatically after errors
 */
(function () {
  "use strict";

  const cfg = window.__cfg || {};
  const T = Object.assign({
    idle: "Tap anywhere to start", get_ready: "Get ready!", smile: "Smile!",
    processing: "Making your photos…", review_title: "Looking good!",
    done: "Done", retake: "Retake", shot_of: "Photo {shot} of {total}",
    camera_offline: "Camera not connected — reconnecting…",
    disk_full: "Storage is almost full — please tell the host",
    connection_lost: "Reconnecting…", error: "Something went wrong — please try again",
  }, cfg.texts || {});
  const fmt = (s, o) => String(s).replace(/\{(\w+)\}/g, (_, k) => (k in o ? o[k] : ""));

  const $ = (id) => document.getElementById(id);
  const el = {
    app: $("app"), live: $("live-preview"), livePlaceholder: $("live-placeholder"),
    idle: $("idle-overlay"), idleText: $("idle-text"), btnTrigger: $("btn-trigger"),
    countdown: $("countdown-overlay"), countdownNumber: $("countdown-number"), shotCounter: $("shot-counter"),
    flash: $("flash"), shotView: $("shot-view"), shotImg: $("shot-img"),
    processing: $("processing"), processingText: $("processing-text"),
    review: $("review"), reviewTitle: $("review-title"), reviewImg: $("review-img"),
    btnConfirm: $("btn-confirm"), btnRetake: $("btn-retake"), progressBar: $("review-progress-bar"),
    banner: $("banner"), bannerText: $("banner-text"), bannerDetail: $("banner-detail"),
    statusBar: $("status-bar"), stateLabel: $("state-label"), cameraLabel: $("camera-label"),
    toasts: $("toast-container"),
  };

  // ── Static texts & options ─────────────────────────────────────────
  el.idleText.textContent = T.idle;
  el.processingText.textContent = T.processing;
  el.reviewTitle.textContent = T.review_title;
  el.btnConfirm.textContent = "✔ " + T.done;
  el.btnRetake.textContent = "↩ " + T.retake;
  if (cfg.countdown_blur) document.documentElement.style.setProperty("--countdown-blur", "blur(6px)");
  if (cfg.mirror_preview === false) document.documentElement.style.setProperty("--mirror", "none");
  if (cfg.show_status_bar) el.statusBar.classList.remove("hidden");

  // ── Client state ───────────────────────────────────────────────────
  const view = {
    state: "BOOT", connected: false, cameraOk: true, cameraMsg: "", diskFull: false,
    lastMessageAt: Date.now(),
  };
  let ws = null;
  let reconnectDelay = 500;
  let reviewTimer = null;
  let toastTimer = null;

  // ── WebSocket ──────────────────────────────────────────────────────
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    try {
      ws = new WebSocket(`${proto}://${location.host}/ws`);
    } catch (e) {
      scheduleReconnect();
      return;
    }
    ws.addEventListener("open", () => {
      view.connected = true;
      reconnectDelay = 500;
      startLiveView();
      renderBanner();
    });
    ws.addEventListener("message", (ev) => {
      view.lastMessageAt = Date.now();
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      try { handleMessage(msg); } catch (e) { reportError(e); }
    });
    ws.addEventListener("close", () => {
      view.connected = false;
      renderBanner();
      scheduleReconnect();
    });
    ws.addEventListener("error", () => { try { ws.close(); } catch { /* ignore */ } });
  }

  function scheduleReconnect() {
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 5000);
  }

  function send(event, data = {}) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(Object.assign({ event }, data)));
      return true;
    }
    return false;
  }
  window.sendEvent = send; // backwards compatibility

  // ── Messages ───────────────────────────────────────────────────────
  function handleMessage(msg) {
    const d = msg.data || {};
    switch (msg.event) {
      case "hello":
        if (cfg.boot_id && d.boot_id && d.boot_id !== cfg.boot_id) {
          location.reload(); // server restarted (maybe upgraded) → reload UI
          return;
        }
        if (d.camera) setCamera(d.camera);
        view.diskFull = !!d.disk_full;
        applyState(d.state, d.state_data || {});
        break;
      case "state_change": applyState(msg.state, d); break;
      case "countdown_tick": tick(d.remaining); break;
      case "shot_captured": showShot(d.url); break;
      case "camera_status": setCamera(d); break;
      case "disk_status": view.diskFull = !!d.disk_full; renderBanner(); break;
      case "config_reloaded":
        if (view.state === "IDLE") location.reload();
        break;
      case "error": showToast(errorText(d), true); break;
      case "warning": console.warn("[booth]", d.message); break;
      case "storage_complete": break;
      default: break;
    }
  }

  function errorText(d) {
    if (d.code === "camera_offline" || d.code === "camera") return T.camera_offline;
    if (d.code === "disk_full") return T.disk_full;
    return T.error;
  }

  function setCamera(status) {
    view.cameraOk = !!status.connected;
    view.cameraMsg = status.message || "";
    el.cameraLabel.textContent = (status.backend || "no camera") + (status.fallback ? " (fallback)" : "");
    if (status.connected && status.preview === false) {
      el.live.classList.add("hidden");
    } else {
      el.live.classList.remove("hidden");
    }
    if (status.connected) startLiveView();
    renderBanner();
  }

  // ── Screens ────────────────────────────────────────────────────────
  const SCREENS = ["idle", "countdown", "shotView", "processing", "review"];
  function show(...names) {
    SCREENS.forEach((n) => el[n].classList.toggle("hidden", !names.includes(n)));
  }

  function applyState(state, data) {
    view.state = state;
    el.app.dataset.state = state;
    el.stateLabel.textContent = state;
    clearInterval(reviewTimer);
    switch (state) {
      case "IDLE":
        show("idle");
        break;
      case "COUNTDOWN":
        show("countdown");
        el.countdownNumber.classList.remove("smile");
        el.countdownNumber.textContent = data.seconds ? "" : T.smile;
        el.shotCounter.textContent = data.total > 1 ? fmt(T.shot_of, data) : "";
        el.shotCounter.classList.toggle("hidden", !(data.total > 1));
        break;
      case "CAPTURE":
        show("countdown");
        el.countdownNumber.classList.remove("tick");
        el.countdownNumber.classList.add("smile");
        el.countdownNumber.textContent = T.smile;
        flash();
        break;
      case "PROCESS":
        show("processing");
        break;
      case "PREVIEW":
        enterReview(data);
        break;
      default:
        break;
    }
    renderBanner();
  }

  function tick(n) {
    const node = el.countdownNumber;
    node.classList.remove("smile", "tick");
    node.textContent = n;
    void node.offsetWidth; // restart animation
    node.classList.add("tick");
  }

  function flash() {
    el.flash.classList.remove("on");
    void el.flash.offsetWidth;
    el.flash.classList.add("on");
  }

  const SAFE_URL = /^\/(media|output)\/[\w\-./]+\.jpe?g$/i;

  function showShot(url) {
    if (!SAFE_URL.test(url || "")) return;
    el.shotImg.src = url;
    show("shotView");
  }

  function enterReview(data) {
    const url = SAFE_URL.test(data.composite || "") ? data.composite : (data.frames || []).filter((u) => SAFE_URL.test(u)).pop();
    if (url) el.reviewImg.src = url;
    el.btnConfirm.disabled = false;
    el.btnRetake.disabled = false;
    show("review");
    const total = Math.max(1, Number(data.seconds) || 10) * 1000;
    const started = Date.now();
    el.progressBar.style.transform = "scaleX(1)";
    reviewTimer = setInterval(() => {
      const left = Math.max(0, 1 - (Date.now() - started) / total);
      el.progressBar.style.transform = `scaleX(${left})`;
      if (left <= 0) clearInterval(reviewTimer);
    }, 100);
  }

  function renderBanner() {
    let text = "", detail = "";
    if (!view.connected) {
      text = T.connection_lost;
    } else if (!view.cameraOk && (view.state === "IDLE" || view.state === "BOOT")) {
      text = T.camera_offline;
      detail = view.cameraMsg;
    } else if (view.diskFull && view.state === "IDLE") {
      text = T.disk_full;
    }
    el.bannerText.textContent = text;
    el.bannerDetail.textContent = detail;
    el.banner.classList.toggle("hidden", !text);
  }

  // ── Live view (MJPEG) with automatic recovery ──────────────────────
  let liveRetry = null;
  function startLiveView() {
    clearTimeout(liveRetry);
    el.live.src = "/preview?t=" + Date.now();
  }
  el.live.addEventListener("error", () => {
    clearTimeout(liveRetry);
    liveRetry = setTimeout(startLiveView, 1500);
  });

  // ── Input ──────────────────────────────────────────────────────────
  let lastPress = 0;
  function pressStart() {
    const now = Date.now();
    if (now - lastPress < 600) return;
    lastPress = now;
    if (view.state === "IDLE") send("trigger");
  }

  el.btnTrigger.addEventListener("click", (e) => { e.stopPropagation(); pressStart(); });
  el.idle.addEventListener("pointerdown", () => { if (cfg.tap_anywhere_to_start !== false) pressStart(); });
  el.btnConfirm.addEventListener("click", () => { el.btnConfirm.disabled = el.btnRetake.disabled = true; send("confirm"); });
  el.btnRetake.addEventListener("click", () => { el.btnConfirm.disabled = el.btnRetake.disabled = true; send("retake"); });

  const triggerKeys = new Set(cfg.trigger_keys || ["Space", "Enter"]);
  document.addEventListener("keydown", (ev) => {
    const key = ev.code || ev.key;
    if (triggerKeys.has(key) || triggerKeys.has(ev.key)) {
      ev.preventDefault();
      if (ev.repeat) return;
      if (view.state === "IDLE") pressStart();
      else if (view.state === "PREVIEW") send("confirm");
      return;
    }
    if (cfg.lock_ui !== false) blockBrowserKeys(ev);
  });

  // ── Kiosk lockdown ─────────────────────────────────────────────────
  function blockBrowserKeys(ev) {
    const k = (ev.key || "").toLowerCase();
    const mod = ev.ctrlKey || ev.metaKey;
    // reload, print, save, find, zoom, history navigation, devtools, new window
    const blocked =
      k === "f5" || k === "f11" || k === "f12" || k === "backspace" ||
      (mod && ["r", "p", "s", "f", "o", "n", "t", "+", "-", "=", "0", "l"].includes(k)) ||
      (ev.altKey && (k === "arrowleft" || k === "arrowright"));
    if (blocked) ev.preventDefault();
  }

  if (cfg.lock_ui !== false) {
    ["contextmenu", "selectstart", "dragstart", "gesturestart", "gesturechange"].forEach((t) =>
      document.addEventListener(t, (e) => e.preventDefault(), { passive: false }));
    document.addEventListener("touchmove", (e) => { if (e.touches.length > 1) e.preventDefault(); }, { passive: false });
    document.addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });
    let lastTouchEnd = 0; // block double-tap zoom
    document.addEventListener("touchend", (e) => {
      const now = Date.now();
      if (now - lastTouchEnd < 350) e.preventDefault();
      lastTouchEnd = now;
    }, { passive: false });
    history.pushState(null, "", location.href);
    window.addEventListener("popstate", () => history.pushState(null, "", location.href));
  }

  // Full screen on first touch (browsers require a user gesture)
  if (cfg.kiosk_mode) {
    const goFull = () => {
      const de = document.documentElement;
      if (!document.fullscreenElement && de.requestFullscreen) de.requestFullscreen().catch(() => {});
    };
    document.addEventListener("pointerdown", goFull);
  }

  // Keep the screen awake
  let wakeLock = null;
  async function keepAwake() {
    try {
      if ("wakeLock" in navigator && document.visibilityState === "visible") {
        wakeLock = await navigator.wakeLock.request("screen");
      }
    } catch { /* not supported / not allowed */ }
  }
  document.addEventListener("visibilitychange", keepAwake);
  keepAwake();

  // Hide the mouse cursor when it is not used
  const hideAfter = (cfg.hide_cursor_after_seconds ?? 3) * 1000;
  if (hideAfter > 0) {
    let cursorTimer = null;
    const wake = () => {
      document.body.classList.remove("hide-cursor");
      clearTimeout(cursorTimer);
      cursorTimer = setTimeout(() => document.body.classList.add("hide-cursor"), hideAfter);
    };
    document.addEventListener("mousemove", wake);
    wake();
  }

  // ── Hidden admin access: tap the top-left corner N times ────────────
  const cornerTaps = Number(cfg.admin_corner_taps) || 0;
  if (cornerTaps > 0) {
    const hot = document.createElement("div");
    hot.id = "admin-hotspot";
    document.body.appendChild(hot);
    let taps = [];
    hot.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      e.preventDefault();
      const now = Date.now();
      taps = taps.filter((t) => now - t < 3000);
      taps.push(now);
      if (taps.length >= cornerTaps && (view.state === "IDLE" || !view.connected)) location.href = "/admin";
    });
  }
  // Operators with a keyboard: Ctrl+Shift+A
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey && e.shiftKey && (e.key === "A" || e.key === "a")) location.href = "/admin";
  }, true);

  // ── Toasts ─────────────────────────────────────────────────────────
  function showToast(message, isError = false) {
    el.toasts.innerHTML = "";
    const toast = document.createElement("div");
    toast.className = "toast" + (isError ? " error" : "");
    toast.textContent = message;
    el.toasts.appendChild(toast);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.remove(), 5000);
  }

  // ── Self-healing ───────────────────────────────────────────────────
  let errorCount = 0;
  function reportError(e) {
    errorCount += 1;
    console.error(e);
    send("client_error", { message: String((e && (e.stack || e.message)) || e) });
    if (errorCount > 20) location.reload();
  }
  window.addEventListener("error", (e) => reportError(e.error || e.message));
  window.addEventListener("unhandledrejection", (e) => reportError(e.reason));

  // If we are mid-session but hear nothing for a while, ask for a snapshot
  setInterval(() => {
    if (view.connected && view.state !== "IDLE" && Date.now() - view.lastMessageAt > 45000) {
      view.lastMessageAt = Date.now();
      send("hello");
    }
  }, 5000);

  // ── Boot ───────────────────────────────────────────────────────────
  show();
  connect();
})();
