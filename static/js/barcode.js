/* ============================================================
   barcode.js  —  v4  (fetch + blob URL approach)
   The core fix: use fetch() to get the image, convert to a
   Blob URL, then set img.src. This bypasses all browser
   image-cache issues that caused onload to silently fail.
   ============================================================ */
(function () {

  /* ── DOM refs ─────────────────────────────────────────────── */
  var typeToggle   = document.getElementById("type-toggle");
  var modeToggle   = document.getElementById("mode-toggle");
  var valueInput   = document.getElementById("value-input");
  var valueHint    = document.getElementById("value-hint");
  var generateBtn  = document.getElementById("generate-btn");
  var printBtn     = document.getElementById("print-btn");
  var downloadBtn  = document.getElementById("download-btn");
  var copyBtn      = document.getElementById("copy-btn");
  var copyImgBtn   = document.getElementById("copy-img-btn");
  var assignBtn    = document.getElementById("assign-btn");
  var productSelect= document.getElementById("product-select");
  var createBtn    = document.getElementById("create-btn");
  var existingMode = document.getElementById("existing-mode");
  var newMode      = document.getElementById("new-mode");
  var previewActions    = document.getElementById("preview-actions");
  var previewPlaceholder= document.getElementById("preview-placeholder");
  var labelTicket  = document.getElementById("label-ticket");

  var labelNameInput    = document.getElementById("label-name");
  var labelPriceInput   = document.getElementById("label-price");
  var labelCurrencyInput= document.getElementById("label-currency");
  var showNameCb        = document.getElementById("show-name");
  var showPriceCb       = document.getElementById("show-price");
  var showCodeTextCb    = document.getElementById("show-code-text");

  var ticketImg     = document.getElementById("ticket-img");
  var ticketName    = document.getElementById("ticket-name");
  var ticketCode    = document.getElementById("ticket-code");
  var ticketPriceRow= document.getElementById("ticket-price-row");
  var ticketCurrency= document.getElementById("ticket-currency");
  var ticketPrice   = document.getElementById("ticket-price");

  var toast    = document.getElementById("toast");
  var toastMsg = document.getElementById("toast-msg");

  /* ── State ───────────────────────────────────────────────── */
  var currentType  = typeToggle.querySelector("button.is-active").dataset.type;
  var currentValue = "";
  var currentSize  = "md";
  var generated    = false;
  var mode         = "existing";
  var activeBlobUrl= null;
  var toastTimer   = null;

  /* ── SVG icons ───────────────────────────────────────────── */
  var SVG_BARCODE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="17" y="17" width="4" height="4"/><rect x="14" y="14" width="3" height="3"/></svg>';
  var SVG_SPINNER = '<span class="btn-spinner"></span>';

  /* ── Helpers ─────────────────────────────────────────────── */
  function showToast(msg, duration) {
    toastMsg.textContent = msg;
    toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toast.classList.remove("is-visible");
    }, duration || 2800);
  }

  function setGenerateLoading(yes) {
    generateBtn.disabled = yes;
    generateBtn.innerHTML = yes
      ? SVG_SPINNER + " Generating\u2026"
      : SVG_BARCODE + " Generate code";
  }

  function updateTicketLabel() {
    if (!generated) return;
    var name     = labelNameInput.value.trim();
    var price    = labelPriceInput.value.trim();
    var currency = labelCurrencyInput.value.trim() || "\u20B9";

    ticketName.textContent   = name;
    ticketName.style.display = (showNameCb.checked && name) ? "block" : "none";

    ticketCode.textContent   = currentValue || "(auto-generated)";
    ticketCode.style.display = showCodeTextCb.checked ? "block" : "none";

    ticketCurrency.textContent = currency;
    ticketPrice.textContent    = price ? parseFloat(price).toFixed(2) : "\u2014";
    ticketPriceRow.style.display = (showPriceCb.checked && price) ? "flex" : "none";
  }

  function revealTicket() {
    generated = true;

    previewPlaceholder.style.display = "none";
    labelTicket.style.display = "flex";

    /* Pop-in animation — remove class first so re-generate re-triggers it */
    labelTicket.classList.remove("is-revealed");
    void labelTicket.offsetWidth; /* force reflow */
    labelTicket.classList.add("is-revealed");

    previewActions.style.display = "flex";
    printBtn.style.display       = "inline-flex";
    copyBtn.style.display        = "inline-flex";

    updateTicketLabel();
    assignBtn.disabled = !productSelect.value;
    var hasName = document.getElementById("new-name").value.trim();
    createBtn.disabled = !valueInput.value.trim() || !hasName;
  }

  /* ── Code type toggle ────────────────────────────────────── */
  var hints = {
    CODE128: "Accepts letters and numbers. Good for general inventory codes.",
    EAN13:   "Numeric only \u2014 12 digits will be padded/generated automatically.",
    QR:      "Accepts any text, URLs, or longer data strings.",
  };

  typeToggle.querySelectorAll("button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      typeToggle.querySelectorAll("button").forEach(function (b) { b.classList.remove("is-active"); });
      btn.classList.add("is-active");
      currentType = btn.dataset.type;
      valueHint.textContent = hints[currentType] || "";
    });
  });

  /* ── Mode toggle ─────────────────────────────────────────── */
  modeToggle.querySelectorAll("button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      modeToggle.querySelectorAll("button").forEach(function (b) { b.classList.remove("is-active"); });
      btn.classList.add("is-active");
      mode = btn.dataset.mode;
      existingMode.style.display = mode === "existing" ? "block" : "none";
      newMode.style.display      = mode === "new"      ? "block" : "none";
    });
  });

  /* ── Label size selector ─────────────────────────────────── */
  document.querySelectorAll(".size-pill").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll(".size-pill").forEach(function (b) { b.classList.remove("is-active"); });
      btn.classList.add("is-active");
      currentSize = btn.dataset.size;
      /* Preserve is-revealed class if already shown */
      var wasRevealed = labelTicket.classList.contains("is-revealed");
      labelTicket.className = "label-ticket size-" + currentSize + (wasRevealed ? " is-revealed" : "");
    });
  });

  /* ── Live label updates ──────────────────────────────────── */
  [labelNameInput, labelPriceInput, labelCurrencyInput].forEach(function (el) {
    el.addEventListener("input", updateTicketLabel);
  });
  [showNameCb, showPriceCb, showCodeTextCb].forEach(function (cb) {
    cb.addEventListener("change", updateTicketLabel);
  });

  function flashInput(el) {
    if (!el) return;
    el.style.transition = 'none';
    el.style.backgroundColor = 'var(--brass-glow)';
    el.style.borderColor = 'var(--brass)';
    void el.offsetWidth;
    el.style.transition = 'all 1.2s ease';
    el.style.backgroundColor = '';
    el.style.borderColor = '';
  }

  function autoFillBarcodeAi(code) {
    if (!code) return;
    
    var nameInputEl = document.getElementById("new-name");
    if (nameInputEl && nameInputEl.value.trim() !== "") {
      return;
    }

    fetch("/inventory/ai_lookup?code=" + encodeURIComponent(code))
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        var mapping = {
          "new-name": data.name,
          "new-category": data.category,
          "new-unit": data.unit_label,
          "new-price": data.unit_price,
          "new-cost": data.cost_price,
          "new-stock": data.stock_qty,
          "new-reorder": data.reorder_level,
          "label-name": data.name,
          "label-price": data.unit_price
        };

        for (var id in mapping) {
          var el = document.getElementById(id);
          if (el) {
            el.value = mapping[id];
            flashInput(el);
          }
        }
        
        createBtn.disabled = false;
        updateTicketLabel();
        showToast("🤖 AI pre-filled product details (" + data.region_inferred + ")", 3500);
      })
      .catch(function (err) {
        console.error("AI lookup failed", err);
      });
  }

  /* ── GENERATE (core fix: fetch → blob URL) ───────────────── */
  generateBtn.addEventListener("click", function () {
    var value = valueInput.value.trim();
    var url = "/barcode/preview?type=" + encodeURIComponent(currentType)
            + "&value=" + encodeURIComponent(value)
            + "&_t=" + Date.now(); /* cache-bust */

    setGenerateLoading(true);

    fetch(url)
      .then(function (response) {
        if (!response.ok) {
          /* Try to parse error JSON from server */
          return response.text().then(function (text) {
            var msg = "Server error " + response.status;
            try { msg = JSON.parse(text).error || msg; } catch (e) {}
            throw new Error(msg);
          });
        }
        var finalValue = response.headers.get("X-Barcode-Value") || value;
        return response.blob().then(function (blob) {
          return { blob: blob, finalValue: finalValue };
        });
      })
      .then(function (res) {
        /* Free the previous blob URL to avoid memory leaks */
        if (activeBlobUrl) { URL.revokeObjectURL(activeBlobUrl); }
        activeBlobUrl = URL.createObjectURL(res.blob);

        /* Set the image source — no onload needed, we already have the data */
        ticketImg.src = activeBlobUrl;
        currentValue  = res.finalValue; /* get the exact code value used */
        valueInput.value = res.finalValue;

        setGenerateLoading(false);
        revealTicket();
        showToast("Code generated!", 2000);
        
        // Auto fill new product form fields using deterministic AI
        autoFillBarcodeAi(res.finalValue);
      })
      .catch(function (err) {
        setGenerateLoading(false);
        showToast((err && err.message) ? err.message : "Could not generate code. Is the server running?", 4500);
      });
  });

  /* ── Pulse "ready" animation on generate button ──────────── */
  generateBtn.classList.add("pulse-ready");
  generateBtn.addEventListener("click", function () {
    generateBtn.classList.remove("pulse-ready");
  }, { once: true });

  /* ── Copy code to clipboard ──────────────────────────────── */
  function copyCode() {
    var val = valueInput.value.trim() || currentValue;
    if (!val || val === "(auto-generated)") {
      showToast("No explicit code value to copy.", 2500);
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(val)
        .then(function () { showToast("Copied to clipboard!"); })
        .catch(function () { fallbackCopy(val); });
    } else {
      fallbackCopy(val);
    }
  }

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;opacity:0;top:0;left:0;";
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    try { document.execCommand("copy"); showToast("Copied!"); }
    catch (e) { showToast("Copy failed \u2014 please copy manually.", 3200); }
    document.body.removeChild(ta);
  }

  copyBtn.addEventListener("click", copyCode);
  if (copyImgBtn) { copyImgBtn.addEventListener("click", copyCode); }

  /* ── Download barcode image ──────────────────────────────── */
  downloadBtn.addEventListener("click", function () {
    if (!generated || !activeBlobUrl) return;
    var a      = document.createElement("a");
    a.href     = activeBlobUrl;
    a.download = (valueInput.value.trim() || "barcode") + ".png";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    showToast("Downloading\u2026");
  });

  /* ── Print labels ────────────────────────────────────────── */
  printBtn.addEventListener("click", function () {
    if (!generated || !activeBlobUrl) return;

    var qty      = Math.max(1, Math.min(200, parseInt(document.getElementById("print-qty").value) || 1));
    var name     = showNameCb.checked  ? labelNameInput.value.trim()  : "";
    var price    = showPriceCb.checked ? labelPriceInput.value.trim() : "";
    var currency = labelCurrencyInput.value.trim() || "\u20B9";
    var showCode = showCodeTextCb.checked;
    var codeText = valueInput.value.trim() || currentValue || "";

    var singleLabel =
      '<div style="display:inline-flex;flex-direction:column;align-items:center;' +
      'background:#fff;border:1px solid #ccc;border-radius:5px;' +
      'padding:12px 14px;margin:5px;width:190px;' +
      'font-family:Arial,sans-serif;box-sizing:border-box;page-break-inside:avoid;">' +
      (name ? '<div style="font-weight:700;font-size:11px;text-align:center;margin-bottom:7px;word-break:break-word;line-height:1.3;">' + name + '</div>' : '') +
      '<img src="' + activeBlobUrl + '" style="width:100%;max-height:65px;object-fit:contain;">' +
      (showCode && codeText ? '<div style="font-family:monospace;font-size:7.5px;letter-spacing:0.07em;margin-top:3px;color:#555;text-align:center;">' + codeText + '</div>' : '') +
      (price ? '<div style="border-top:1px solid #eee;margin-top:7px;padding-top:5px;width:100%;text-align:center;"><span style="font-size:11px;color:#888;">' + currency + '</span><span style="font-size:18px;font-weight:700;color:#8a6a3d;"> ' + parseFloat(price).toFixed(2) + '</span></div>' : '') +
      '</div>';

    var allLabels = "";
    for (var i = 0; i < qty; i++) { allLabels += singleLabel; }

    var win = window.open("", "_blank");
    if (!win) {
      showToast("Pop-up blocked \u2014 please allow pop-ups for this page.", 4000);
      return;
    }
    win.document.write(
      '<!DOCTYPE html><html><head><title>Print ' + qty + ' label' + (qty > 1 ? "s" : "") + '</title>' +
      '<style>* { box-sizing:border-box; } body { margin:10px;background:#fff; } .wrap { display:flex;flex-wrap:wrap;gap:4px; } @media print { body { margin:6mm; } @page { margin:6mm; } }</style>' +
      '</head><body><div class="wrap">' + allLabels + '</div>' +
      '<script>window.onload=function(){window.print();};<\/script></body></html>'
    );
    win.document.close();
  });

  /* ── Assign to existing product ──────────────────────────── */
  productSelect.addEventListener("change", function () {
    assignBtn.disabled = !productSelect.value || !generated;
  });

  assignBtn.addEventListener("click", function () {
    var value = valueInput.value.trim();
    if (!value) { showToast("Enter an explicit code value before assigning.", 3200); return; }
    if (!productSelect.value) { showToast("Select a product first.", 3000); return; }

    assignBtn.disabled    = true;
    assignBtn.textContent = "Assigning\u2026";

    fetch("/barcode/assign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ product_id: productSelect.value, code_type: currentType, value: value }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        assignBtn.textContent = "Assign to selected product";
        assignBtn.disabled    = false;
        if (!res.ok) { showToast(res.data.error || "Could not assign code.", 3500); return; }
        showToast("Code assigned to product!");
      })
      .catch(function () {
        assignBtn.textContent = "Assign to selected product";
        assignBtn.disabled    = false;
        showToast("Network error \u2014 could not assign.", 3500);
      });
  });

  /* ── Create new product ──────────────────────────────────── */
  document.getElementById("new-name").addEventListener("input", function () {
    createBtn.disabled = !valueInput.value.trim() || !generated || !document.getElementById("new-name").value.trim();
  });

  createBtn.addEventListener("click", function () {
    var value = valueInput.value.trim();
    var name  = document.getElementById("new-name").value.trim();
    if (!value) { showToast("Generate a code with an explicit value first.", 3200); return; }
    if (!name)  { showToast("Enter a product name.", 3000); return; }

    createBtn.disabled    = true;
    createBtn.textContent = "Saving\u2026";

    fetch("/barcode/create_product", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name:          name,
        code_type:     currentType,
        value:         value,
        category:      document.getElementById("new-category").value.trim(),
        unit_label:    document.getElementById("new-unit").value.trim(),
        unit_price:    document.getElementById("new-price").value,
        cost_price:    document.getElementById("new-cost").value,
        stock_qty:     document.getElementById("new-stock").value,
        reorder_level: document.getElementById("new-reorder").value,
      }),
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        createBtn.textContent = "Save as new product";
        if (!res.ok) { showToast(res.data.error || "Could not create product.", 3500); createBtn.disabled = false; return; }
        showToast(res.data.name + " added to inventory!");
        setTimeout(function () { window.location.reload(); }, 1800);
      })
      .catch(function () {
        createBtn.textContent = "Save as new product";
        createBtn.disabled    = false;
        showToast("Network error \u2014 could not save.", 3500);
      });
  });

})();
