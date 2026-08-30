/**
 * GranularPhotoBooth — WebSocket client + state-driven UI
 *
 * State machine mirrored from the backend:
 *   IDLE ➜ COUNTDOWN ➜ CAPTURE ➜ PREVIEW ➜ PROCESS ➜ IDLE
 */

(function () {
  "use strict";

  // ─── DOM references ────────────────────────────────────────────────
  const livePreview       = document.getElementById("live-preview");
  const capturedContainer = document.getElementById("captured-container");
  const previewContainer  = document.getElementById("preview-container");
  const capturedImage     = document.getElementById("captured-image");
  const countdownOverlay  = document.getElementById("countdown-overlay");
  let   countdownNumber   = document.getElementById("countdown-number");
  const stateLabel        = document.getElementById("state-label");
  const shotCounter       = document.getElementById("shot-counter");
  const btnTrigger        = document.getElementById("btn-trigger");
  const btnConfirm        = document.getElementById("btn-confirm");
  const btnRetake         = document.getElementById("btn-retake");
  const toastContainer    = document.getElementById("toast-container");

  // ─── WebSocket setup ───────────────────────────────────────────────
  let ws = null;
  let reconnectDelay = 1000;

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws`);

    ws.addEventListener("open", () => {
      console.log("[ws] connected");
      reconnectDelay = 1000;
    });

    ws.addEventListener("message", (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      handleMessage(msg);
    });

    ws.addEventListener("close", () => {
      console.warn("[ws] disconnected — reconnecting in", reconnectDelay, "ms");
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 10000);
    });

    ws.addEventListener("error", (err) => {
      console.error("[ws] error", err);
    });
  }

  // ─── Public send helper (called from inline onclick attributes) ─────
  window.sendEvent = function (event, data = {}) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ event, ...data }));
    }
  };

  // ─── Message router ────────────────────────────────────────────────
  function handleMessage(msg) {
    switch (msg.event) {
      case "state_change":
        applyState(msg.state, msg.data || {});
        break;
      case "countdown_tick":
        updateCountdown(msg.data.remaining);
        break;
      case "preview_ready":
        showCapturedImage(msg.data.image_url);
        break;
      case "storage_complete":
        showToast("✔ Saved: " + (msg.data.paths || []).join(", "));
        break;
      case "error":
        showToast("⚠ " + msg.data.message, true);
        break;
      default:
        console.debug("[ws] unknown event:", msg.event);
    }
  }

  // ─── State handlers ────────────────────────────────────────────────

  function applyState(state, data) {
    stateLabel.textContent = state;
    console.info("[state]", state, data);
    switch (state) {
      case "IDLE":      enterIdle(data);      break;
      case "COUNTDOWN": enterCountdown(data); break;
      case "CAPTURE":   enterCapture(data);   break;
      case "PREVIEW":   enterPreview(data);   break;
      case "PROCESS":   enterProcess(data);   break;
      default:
        console.warn("[state] unknown state received:", state);
    }
  }

  function enterIdle() {
    showLivePreview();
    setButtons({ trigger: true, confirm: false, retake: false });
    countdownOverlay.classList.add("hidden");
    shotCounter.classList.add("hidden");
  }

  function enterCountdown(data) {
    showLivePreview();
    setButtons({ trigger: false, confirm: false, retake: false });
    countdownOverlay.classList.remove("hidden");
    if (data.shot && data.total) {
      shotCounter.textContent = `Shot ${data.shot} of ${data.total}`;
      shotCounter.classList.remove("hidden");
    }
    // Initial tick value will arrive via countdown_tick
  }

  function enterCapture() {
    countdownOverlay.classList.add("hidden");
    countdownNumber.textContent = "📷";
    countdownOverlay.classList.remove("hidden");
  }

  function enterPreview(data) {
    countdownOverlay.classList.add("hidden");
    if (data.image_data && /^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(data.image_data)) {
      capturedImage.src = data.image_data;
    }
    capturedContainer.classList.remove("hidden");
    previewContainer.classList.add("hidden");
    setButtons({ trigger: false, confirm: true, retake: true });
  }

  function enterProcess() {
    stateLabel.textContent = "PROCESSING…";
    setButtons({ trigger: false, confirm: false, retake: false });
  }

  // ─── Countdown tick ────────────────────────────────────────────────
  function updateCountdown(remaining) {
    countdownNumber.textContent = remaining;
    // Re-trigger CSS animation by cloning the node
    const clone = countdownNumber.cloneNode(true);
    countdownNumber.parentNode.replaceChild(clone, countdownNumber);
    countdownNumber = clone;
  }

  // ─── View helpers ──────────────────────────────────────────────────
  function showLivePreview() {
    previewContainer.classList.remove("hidden");
    capturedContainer.classList.add("hidden");
  }

  function showCapturedImage(url) {
    // Extract only the filename from the server-provided path and construct a
    // safe, known-prefix URL to prevent open-redirect or XSS via img.src.
    if (typeof url !== "string") {
      console.warn("[ui] rejected non-string image URL");
      return;
    }
    const filename = url.split("/").pop();
    if (!filename || !/^[\w\-.]+$/.test(filename)) {
      console.warn("[ui] rejected untrusted image filename:", filename);
      return;
    }
    capturedImage.src = "/output/" + filename + "?t=" + Date.now();
    capturedContainer.classList.remove("hidden");
    previewContainer.classList.add("hidden");
  }

  function setButtons({ trigger, confirm, retake }) {
    btnTrigger.classList.toggle("hidden", !trigger);
    btnConfirm.classList.toggle("hidden", !confirm);
    btnRetake.classList.toggle("hidden",  !retake);
  }

  // ─── Toast notifications ───────────────────────────────────────────
  function showToast(message, isError = false) {
    const toast = document.createElement("div");
    toast.className = "toast" + (isError ? " error" : "");
    toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(() => toast.remove(), 5000);
  }

  // ─── Keyboard shortcut (mirrors physical trigger) ──────────────────
  document.addEventListener("keydown", (ev) => {
    if (ev.code === "Space" && !ev.repeat) {
      ev.preventDefault();
      window.sendEvent("trigger");
    }
  });

  // ─── Boot ──────────────────────────────────────────────────────────
  connect();
})();
