/**
 * scanner.js — Advanced Barcode Scanner
 * Full rewrite with: all barcode formats, vibration, audio feedback,
 * duplicate guard, fast scanning mode, and offline support.
 */

(function () {
  "use strict";

  // ── Audio Feedback ────────────────────────────────────────────────────────
  let _audioCtx = null;

  function playScanBeep(success = true) {
    try {
      if (!_audioCtx) {
        _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
      const osc = _audioCtx.createOscillator();
      const gain = _audioCtx.createGain();
      osc.connect(gain);
      gain.connect(_audioCtx.destination);
      osc.type = "sine";
      osc.frequency.setValueAtTime(success ? 1047 : 330, _audioCtx.currentTime);
      gain.gain.setValueAtTime(0.3, _audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + 0.3);
      osc.start(_audioCtx.currentTime);
      osc.stop(_audioCtx.currentTime + 0.3);
    } catch (e) {}
  }

  window.playScanBeep = playScanBeep;

  // ── Vibration ─────────────────────────────────────────────────────────────
  function vibrate(pattern) {
    if (navigator.vibrate) {
      navigator.vibrate(pattern || [150, 50, 150]);
    }
  }

  // ── Format list (html5-qrcode supported formats) ──────────────────────────
  const ALL_FORMATS = [
    Html5QrcodeSupportedFormats.EAN_13,
    Html5QrcodeSupportedFormats.EAN_8,
    Html5QrcodeSupportedFormats.UPC_A,
    Html5QrcodeSupportedFormats.UPC_E,
    Html5QrcodeSupportedFormats.CODE_128,
    Html5QrcodeSupportedFormats.CODE_39,
    Html5QrcodeSupportedFormats.QR_CODE,
    Html5QrcodeSupportedFormats.ITF,
    Html5QrcodeSupportedFormats.CODE_93,
    Html5QrcodeSupportedFormats.CODABAR,
  ];

  // ── Scanner state ─────────────────────────────────────────────────────────
  let html5Qr = null;
  let running = false;
  let scanCooldown = false;
  let fastMode = false; // In fast mode, re-opens after each scan

  // ── DOM refs (resolved lazily) ────────────────────────────────────────────
  function el(id) { return document.getElementById(id); }

  // ── Open scanner modal ────────────────────────────────────────────────────
  function openScanner(opts) {
    opts = opts || {};
    fastMode = opts.fastMode || false;
    const callback = opts.onResult; // function(decodedText) called on scan

    const modal = el("camera-modal");
    const viewport = el("scanner-viewport");
    const statusEl = el("scan-status");
    const scanLine = el("scan-line");

    if (!modal || !viewport) return;

    // Reset viewport
    viewport.innerHTML = "";
    if (scanLine) scanLine.style.animationPlayState = "running";

    modal.classList.add("is-open");
    if (statusEl) statusEl.textContent = "Starting camera…";

    html5Qr = new Html5Qrcode("scanner-viewport", {
      formatsToSupport: ALL_FORMATS,
      verbose: false,
    });

    Html5Qrcode.getCameras()
      .then((cameras) => {
        if (!cameras || cameras.length === 0) {
          if (statusEl) statusEl.textContent = "No camera found on this device.";
          return;
        }
        // Prefer back camera
        const cam = cameras.find((c) => /back|rear|environment/i.test(c.label)) || cameras[cameras.length - 1];
        return html5Qr.start(
          { deviceId: { exact: cam.id } },
          {
            fps: 25,
            qrbox: { width: Math.min(300, window.innerWidth - 40), height: Math.min(180, window.innerHeight - 120) },
            aspectRatio: 1.6,
            formatsToSupport: ALL_FORMATS,
          },
          (decodedText, result) => {
            if (!running || scanCooldown) return;
            decodedText = (decodedText || "").trim().replace(/[\r\n\t]/g, "");
            if (!decodedText) return;

            scanCooldown = true;
            running = false;

            vibrate([180, 40, 100]);
            playScanBeep(true);

            if (statusEl) {
              statusEl.innerHTML = `<span class="scan-ok-badge">✓ Detected</span> <code>${decodedText}</code>`;
            }
            if (scanLine) scanLine.style.animationPlayState = "paused";

            if (callback) {
              callback(decodedText);
            } else if (window.SCANNER_ON_RESULT) {
              window.SCANNER_ON_RESULT(decodedText);
            }

            if (!fastMode) {
              setTimeout(() => closeScanner(), 600);
            } else {
              setTimeout(() => {
                scanCooldown = false;
                running = true;
                if (statusEl) statusEl.textContent = "Ready — point at next barcode.";
                if (scanLine) scanLine.style.animationPlayState = "running";
              }, 1500);
            }
          },
          () => { /* per-frame decode errors: expected, ignore */ }
        );
      })
      .then(() => {
        running = true;
        scanCooldown = false;
        if (statusEl) statusEl.textContent = "Point camera at barcode or QR code.";
      })
      .catch((err) => {
        console.error("Scanner error:", err);
        if (statusEl) statusEl.textContent = "Could not access camera. Check permissions.";
      });
  }

  // ── Close scanner ─────────────────────────────────────────────────────────
  function closeScanner() {
    const modal = el("camera-modal");
    if (modal) modal.classList.remove("is-open");
    running = false;
    scanCooldown = false;
    if (html5Qr) {
      html5Qr.stop().then(() => html5Qr.clear()).catch(() => {});
      html5Qr = null;
    }
    const vp = el("scanner-viewport");
    if (vp) vp.innerHTML = "";
  }

  // ── Expose globals ────────────────────────────────────────────────────────
  window.openScanner = openScanner;
  window.closeScanner = closeScanner;
  window.ScannerVibrate = vibrate;

  // ── Wire up standard open/close buttons on the page ─────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    const openBtn  = el("open-camera-btn");
    const closeBtn = el("close-camera-btn");
    const modal    = el("camera-modal");

    if (openBtn)  openBtn.addEventListener("click",  () => openScanner());
    if (closeBtn) closeBtn.addEventListener("click",  closeScanner);
    if (modal) {
      modal.addEventListener("click", (e) => {
        if (e.target === modal) closeScanner();
      });
    }
  });
})();
