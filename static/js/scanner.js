/**
 * scanner.js — Advanced Barcode Scanner v2 (bug-fixed)
 * Fixes:
 *  - Race condition: running flag is now set immediately (not after async start resolves)
 *  - Added fallback environment camera selection for mobile
 *  - Better cooldown handling with clear visual feedback
 *  - More barcode formats supported
 */

(function () {
  "use strict";

  // ── Audio Feedback ────────────────────────────────────────────────────────
  let _audioCtx = null;

  function playScanBeep(success) {
    success = success !== false;
    try {
      if (!_audioCtx) {
        _audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
      // Resume context if suspended (browser autoplay policy)
      if (_audioCtx.state === 'suspended') {
        _audioCtx.resume();
      }
      const osc  = _audioCtx.createOscillator();
      const gain = _audioCtx.createGain();
      osc.connect(gain);
      gain.connect(_audioCtx.destination);
      osc.type = "sine";
      osc.frequency.setValueAtTime(success ? 1047 : 330, _audioCtx.currentTime);
      gain.gain.setValueAtTime(0.3, _audioCtx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, _audioCtx.currentTime + 0.3);
      osc.start(_audioCtx.currentTime);
      osc.stop(_audioCtx.currentTime + 0.3);
    } catch (e) {
      // Audio context not available - silently ignore
    }
  }

  window.playScanBeep = playScanBeep;

  // ── Vibration ─────────────────────────────────────────────────────────────
  function vibrate(pattern) {
    try {
      if (navigator.vibrate) {
        navigator.vibrate(pattern || [150, 50, 150]);
      }
    } catch (e) {}
  }

  // ── Format list ──────────────────────────────────────────────────────────
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
    Html5QrcodeSupportedFormats.DATA_MATRIX,
    Html5QrcodeSupportedFormats.AZTEC,
  ].filter(function(f) { return f !== undefined && f !== null; });


  // ── Scanner state ─────────────────────────────────────────────────────────
  let html5Qr     = null;
  let running     = false;
  let scanCooldown = false;
  let fastMode    = false;
  let _scannerStarting = false; // prevent double-starts

  function el(id) { return document.getElementById(id); }

  // ── Open scanner modal ────────────────────────────────────────────────────
  function openScanner(opts) {
    if (_scannerStarting) return; // prevent double-open
    opts     = opts || {};
    fastMode = opts.fastMode || false;
    const callback = opts.onResult;

    const modal    = el("camera-modal");
    const viewport = el("scanner-viewport");
    const statusEl = el("scan-status");
    const scanLine = el("scan-line");

    if (!modal || !viewport) return;

    // Ensure any previous scanner is fully stopped first
    _stopScanner().then(function () {
      // Reset viewport
      viewport.innerHTML = "";
      if (scanLine) scanLine.style.animationPlayState = "running";

      modal.classList.add("is-open");
      if (statusEl) statusEl.textContent = "Starting camera…";

      _scannerStarting = true;

      html5Qr = new Html5Qrcode("scanner-viewport", {
        formatsToSupport: ALL_FORMATS,
        verbose: false,
      });

      const qrboxSize = {
        width:  Math.min(280, Math.max(window.innerWidth - 60, 150)),
        height: Math.min(160, Math.max(window.innerHeight - 200, 100)),
      };

      Html5Qrcode.getCameras()
        .then(function (cameras) {
          _scannerStarting = false;
          if (!cameras || cameras.length === 0) {
            if (statusEl) statusEl.textContent = "No camera found on this device.";
            return;
          }

          // Prefer rear/back/environment camera — critical for mobile
          const backCam =
            cameras.find(function (c) { return /back|rear|environment/i.test(c.label); }) ||
            cameras[cameras.length - 1];

          // BUG FIX: Set running = true BEFORE start() resolves to avoid race condition
          running      = true;
          scanCooldown = false;
          if (statusEl) statusEl.textContent = "Point camera at barcode or QR code.";

          return html5Qr.start(
            { deviceId: { exact: backCam.id } },
            {
              fps: 20,
              qrbox: qrboxSize,
              aspectRatio: 1.5,
              formatsToSupport: ALL_FORMATS,
              disableFlip: false,
            },
            function onDecode(decodedText) {
              if (!running || scanCooldown) return;

              decodedText = (decodedText || "").trim().replace(/[\r\n\t]/g, "");
              if (!decodedText) return;

              // Lock immediately to prevent duplicate scans
              scanCooldown = true;
              running      = false;

              vibrate([180, 40, 100]);
              playScanBeep(true);

              if (statusEl) {
                statusEl.innerHTML =
                  '<span class="scan-ok-badge">✓ Detected</span> <code>' +
                  decodedText +
                  "</code>";
              }
              if (scanLine) scanLine.style.animationPlayState = "paused";

              if (callback) {
                callback(decodedText);
              } else if (window.SCANNER_ON_RESULT) {
                window.SCANNER_ON_RESULT(decodedText);
              }

              if (!fastMode) {
                setTimeout(function () { closeScanner(); }, 700);
              } else {
                setTimeout(function () {
                  if (!html5Qr) return;
                  scanCooldown = false;
                  running      = true;
                  if (statusEl) statusEl.textContent = "Ready — point at next barcode.";
                  if (scanLine) scanLine.style.animationPlayState = "running";
                }, 1500);
              }
            },
            function onError() {
              /* per-frame decode errors are expected — ignore silently */
            }
          );
        })
        .catch(function (err) {
          _scannerStarting = false;
          running          = false;
          console.error("Scanner open error:", err);

          var msg = "Could not access camera.";
          if (err && (err.name === "NotAllowedError" || String(err).includes("NotAllowed"))) {
            msg = "Camera permission denied. Please allow camera access and try again.";
          } else if (err && String(err).includes("NotFound")) {
            msg = "No camera found. Make sure a camera is connected.";
          }
          if (statusEl) statusEl.textContent = msg;
        });
    });
  }

  // ── Internal stop helper (returns Promise) ─────────────────────────────────
  function _stopScanner() {
    running      = false;
    scanCooldown = false;
    if (html5Qr) {
      var old = html5Qr;
      html5Qr  = null;
      return old.stop().then(function () { old.clear(); }).catch(function () {});
    }
    return Promise.resolve();
  }

  // ── Close scanner ─────────────────────────────────────────────────────────
  function closeScanner() {
    const modal = el("camera-modal");
    if (modal) modal.classList.remove("is-open");
    _stopScanner().then(function () {
      const vp = el("scanner-viewport");
      if (vp) vp.innerHTML = "";
    });
  }

  // ── Expose globals ────────────────────────────────────────────────────────
  window.openScanner  = openScanner;
  window.closeScanner = closeScanner;
  window.ScannerVibrate = vibrate;

  // ── Wire up standard open/close buttons on the page ─────────────────────
  document.addEventListener("DOMContentLoaded", function () {
    const openBtn  = el("open-camera-btn");
    const closeBtn = el("close-camera-btn");
    const modal    = el("camera-modal");

    if (openBtn) {
      openBtn.addEventListener("click", function () { openScanner(); });
    }
    if (closeBtn) {
      closeBtn.addEventListener("click", closeScanner);
    }
    if (modal) {
      modal.addEventListener("click", function (e) {
        if (e.target === modal) closeScanner();
      });
    }

    // Resume audio context on first user interaction (browser autoplay policy)
    document.addEventListener("click", function resumeAudio() {
      if (_audioCtx && _audioCtx.state === "suspended") {
        _audioCtx.resume();
      }
      document.removeEventListener("click", resumeAudio);
    }, { once: true });
  });
})();
