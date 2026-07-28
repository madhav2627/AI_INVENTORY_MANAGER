/**
 * scanner_advanced.js
 * -------------------
 * OCR-based label reader using Tesseract.js (loaded from CDN).
 * Captures a still frame from the live video, runs OCR, and extracts:
 *   - Expiry date  (patterns: EXP, EXPIRY, BEST BEFORE, BB, USE BY)
 *   - Mfg date     (patterns: MFG, MFD, MANUFACTURED, DATE OF MFG)
 *   - Batch number (patterns: BATCH, LOT, B.NO)
 *   - Serial number (patterns: SN, S/N, SERIAL)
 *
 * Usage:
 *   ScannerOCR.captureAndRead(videoElement).then(result => { ... })
 *   result = { expiry_date, mfg_date, batch_number, serial_number, raw_text }
 */

const ScannerOCR = (function () {
  "use strict";

  let _tesseractReady = false;
  let _tesseractLoading = false;
  const _loadCallbacks = [];

  // ── Load Tesseract.js from CDN lazily ────────────────────────────────────
  function ensureTesseract() {
    return new Promise((resolve) => {
      if (typeof Tesseract !== "undefined") {
        resolve();
        return;
      }
      if (_tesseractLoading) {
        _loadCallbacks.push(resolve);
        return;
      }
      _tesseractLoading = true;
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js";
      script.onload = () => {
        _tesseractReady = true;
        resolve();
        _loadCallbacks.forEach((cb) => cb());
        _loadCallbacks.length = 0;
      };
      script.onerror = () => resolve(); // fail silently
      document.head.appendChild(script);
    });
  }

  // ── Capture frame from <video> element ───────────────────────────────────
  function captureFrame(videoEl) {
    const canvas = document.createElement("canvas");
    canvas.width  = videoEl.videoWidth  || 640;
    canvas.height = videoEl.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
    // Enhance contrast for better OCR
    const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
    const data = imageData.data;
    for (let i = 0; i < data.length; i += 4) {
      const avg = (data[i] + data[i + 1] + data[i + 2]) / 3;
      const val = avg > 128 ? 255 : 0; // binarize
      data[i] = data[i + 1] = data[i + 2] = val;
    }
    ctx.putImageData(imageData, 0, 0);
    return canvas.toDataURL("image/png");
  }

  // ── Date normalization → YYYY-MM-DD ─────────────────────────────────────
  function normalizeDate(raw) {
    if (!raw) return "";
    // Patterns: DD/MM/YYYY, MM/YYYY, YYYY-MM-DD, DD-MM-YYYY, DD.MM.YYYY
    const patterns = [
      /(\d{2})[\/\-\.](\d{2})[\/\-\.](\d{4})/,  // DD/MM/YYYY or MM/DD/YYYY
      /(\d{4})[\/\-\.](\d{2})[\/\-\.](\d{2})/,  // YYYY-MM-DD
      /(\d{2})[\/\-\.](\d{4})/,                  // MM/YYYY
      /([A-Z]{3})[\/\-\s](\d{4})/i,              // MMM YYYY
    ];

    const MONTHS = { JAN:1,FEB:2,MAR:3,APR:4,MAY:5,JUN:6,JUL:7,AUG:8,SEP:9,OCT:10,NOV:11,DEC:12 };

    for (const p of patterns) {
      const m = raw.match(p);
      if (!m) continue;
      try {
        if (p === patterns[0]) {
          const [, a, b, c] = m;
          // Guess DD/MM/YYYY (day <= 31, month <= 12)
          if (parseInt(a) <= 31 && parseInt(b) <= 12) {
            return `${c}-${b.padStart(2,"0")}-${a.padStart(2,"0")}`;
          }
        } else if (p === patterns[1]) {
          return `${m[1]}-${m[2]}-${m[3]}`;
        } else if (p === patterns[2]) {
          return `${m[2]}-${m[1].padStart(2,"0")}-01`;
        } else if (p === patterns[3]) {
          const mo = MONTHS[m[1].toUpperCase()];
          if (mo) return `${m[2]}-${String(mo).padStart(2,"0")}-01`;
        }
      } catch (e) {}
    }
    return "";
  }

  // ── Parse OCR text for fields ────────────────────────────────────────────
  function parseOCRText(text) {
    const result = { expiry_date: "", mfg_date: "", batch_number: "", serial_number: "", raw_text: text };
    if (!text) return result;

    const lines = text.split(/\n/).map(l => l.trim()).filter(Boolean);

    const EXP_RE  = /(?:exp(?:iry)?|best before|bb|use by|use before|exp\.?date)[:\s]*([^\n]+)/i;
    const MFG_RE  = /(?:mfg\.?|mfd\.?|manufactured|date of mfg|dom)[:\s]*([^\n]+)/i;
    const BATCH_RE = /(?:batch|lot|b\.?no\.?)[:\s\#]*([A-Z0-9\-]+)/i;
    const SN_RE   = /(?:s[\./]?n\.?|serial(?:\s*no)?)[:\s]*([A-Z0-9\-]+)/i;

    const fullText = lines.join(" ");

    const expMatch   = fullText.match(EXP_RE);
    const mfgMatch   = fullText.match(MFG_RE);
    const batchMatch = fullText.match(BATCH_RE);
    const snMatch    = fullText.match(SN_RE);

    if (expMatch)   result.expiry_date   = normalizeDate(expMatch[1].trim());
    if (mfgMatch)   result.mfg_date      = normalizeDate(mfgMatch[1].trim());
    if (batchMatch) result.batch_number  = batchMatch[1].trim().substring(0, 50);
    if (snMatch)    result.serial_number = snMatch[1].trim().substring(0, 50);

    return result;
  }

  // ── Main API: captureAndRead ─────────────────────────────────────────────
  async function captureAndRead(videoEl) {
    await ensureTesseract();
    if (typeof Tesseract === "undefined") {
      return { expiry_date: "", mfg_date: "", batch_number: "", serial_number: "", raw_text: "", error: "Tesseract not loaded" };
    }

    const imageData = captureFrame(videoEl);
    try {
      const result = await Tesseract.recognize(imageData, "eng", {
        logger: () => {},
      });
      const text = result.data.text || "";
      return parseOCRText(text);
    } catch (e) {
      return { expiry_date: "", mfg_date: "", batch_number: "", serial_number: "", raw_text: "", error: String(e) };
    }
  }

  // ── Try to read from the active camera modal video ─────────────────────
  function readFromActiveCamera() {
    const video = document.querySelector("#scanner-viewport video");
    if (!video) return Promise.resolve(null);
    return captureAndRead(video);
  }

  return { captureAndRead, readFromActiveCamera, parseOCRText };
})();

window.ScannerOCR = ScannerOCR;
