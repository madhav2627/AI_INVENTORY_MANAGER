(function () {
  const currency = window.CURRENCY || "";
  let cart = []; // {product_id, name, price, qty, unit, stock}

  // Global Audio Synthesizer scanner Beep
  window.playScanBeep = function() {
    try {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(900, audioCtx.currentTime); // Pitch A5/B5
      gain.gain.setValueAtTime(0.08, audioCtx.currentTime); // low volume
      gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.12);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start();
      osc.stop(audioCtx.currentTime + 0.12);
    } catch (e) {
      console.warn("Audio Context beep failed", e);
    }
  };

  const cartItemsEl = document.getElementById("cart-items");
  const cartEmptyEl = document.getElementById("cart-empty");
  const cartCountEl = document.getElementById("cart-count");
  const subtotalEl = document.getElementById("sum-subtotal");
  const totalEl = document.getElementById("sum-total");
  const discountInput = document.getElementById("discount-input");
  const taxInput = document.getElementById("tax-input");
  const checkoutBtn = document.getElementById("checkout-btn");
  const scanInput = document.getElementById("scan-input");
  const searchInput = document.getElementById("product-search");
  const productGrid = document.getElementById("product-grid");

  // AI Quick Add Selectors
  const aiModal = document.getElementById("ai-quickadd-modal");
  const closeAiBtn = document.getElementById("close-ai-btn");
  const cancelAiBtn = document.getElementById("ai-cancel-btn");
  const saveAiBtn = document.getElementById("ai-save-btn");
  const aiAnalyzingState = document.getElementById("ai-analyzing-state");
  const aiResultState = document.getElementById("ai-result-state");
  const aiProdName = document.getElementById("ai-prod-name");
  const aiProdCategory = document.getElementById("ai-prod-category");
  const aiProdUnit = document.getElementById("ai-prod-unit");
  const aiProdPrice = document.getElementById("ai-prod-price");
  const aiProdCost = document.getElementById("ai-prod-cost");
  const aiProdStock = document.getElementById("ai-prod-stock");
  const aiProdCode = document.getElementById("ai-prod-code");
  const aiInferredRegion = document.getElementById("ai-inferred-region");

  function closeAiModal() {
    if (aiModal) aiModal.classList.remove("is-open");
  }
  if (closeAiBtn) closeAiBtn.onclick = closeAiModal;
  if (cancelAiBtn) cancelAiBtn.onclick = closeAiModal;
  if (aiModal) {
    aiModal.onclick = (e) => {
      if (e.target === aiModal) closeAiModal();
    };
  }

  function money(n) {
    return currency + Number(n || 0).toFixed(2);
  }

  function findCartLine(productId) {
    return cart.find((c) => c.product_id === productId);
  }

  function addToCart(product) {
    const existing = findCartLine(product.id);
    if (existing) {
      if (existing.qty + 1 > product.stock) {
        flashScanStatus(`Only ${product.stock} ${product.unit} of ${product.name} available.`, true);
        return;
      }
      existing.qty += 1;
    } else {
      if (product.stock <= 0) {
        flashScanStatus(`${product.name} is out of stock.`, true);
        return;
      }
      cart.push({
        product_id: product.id,
        name: product.name,
        price: product.price,
        qty: 1,
        unit: product.unit,
        stock: product.stock,
      });
    }
    renderCart();
  }

  function changeQty(productId, delta) {
    const line = findCartLine(productId);
    if (!line) return;
    const newQty = line.qty + delta;
    if (newQty <= 0) {
      cart = cart.filter((c) => c.product_id !== productId);
    } else if (newQty > line.stock) {
      return;
    } else {
      line.qty = newQty;
    }
    renderCart();
  }

  function removeLine(productId) {
    cart = cart.filter((c) => c.product_id !== productId);
    renderCart();
  }

  function renderCart() {
    cartItemsEl.innerHTML = "";
    if (cart.length === 0) {
      cartItemsEl.appendChild(cartEmptyEl);
      cartEmptyEl.style.display = "block";
    } else {
      cart.forEach((line) => {
        const row = document.createElement("div");
        row.className = "cart-line";
        row.innerHTML = `
          <div>
            <div class="c-name">${line.name}</div>
            <div class="c-unit">${money(line.price)} / ${line.unit}</div>
          </div>
          <div class="qty-control">
            <button data-action="dec">&minus;</button>
            <span>${line.qty}</span>
            <button data-action="inc">+</button>
          </div>
          <div class="c-total">${money(line.price * line.qty)}</div>
          <div class="c-remove" data-action="remove">Remove</div>
        `;
        row.querySelector('[data-action="inc"]').onclick = () => changeQty(line.product_id, 1);
        row.querySelector('[data-action="dec"]').onclick = () => changeQty(line.product_id, -1);
        row.querySelector('[data-action="remove"]').onclick = () => removeLine(line.product_id);
        cartItemsEl.appendChild(row);
      });
    }

    const totalQty = cart.reduce((s, c) => s + c.qty, 0);
    cartCountEl.textContent = `${totalQty} item${totalQty === 1 ? "" : "s"}`;
    checkoutBtn.disabled = cart.length === 0;
    updateTotals();

    // Update mobile elements
    const mobileCartBadge = document.getElementById("mobile-cart-badge");
    const mobileFooterCount = document.getElementById("mobile-footer-count");
    const mobileFooterTotal = document.getElementById("mobile-footer-total");
    const mobileCheckoutFooter = document.getElementById("mobile-checkout-footer");

    if (mobileCartBadge) mobileCartBadge.textContent = totalQty;
    if (mobileFooterCount) mobileFooterCount.textContent = `${totalQty} item${totalQty === 1 ? "" : "s"}`;
    if (mobileFooterTotal) {
      const subtotal = cart.reduce((s, c) => s + c.price * c.qty, 0);
      const discount = parseFloat(discountInput.value || 0);
      const taxRate = parseFloat(taxInput.value || 0);
      const taxAmount = Math.max(0, subtotal - discount) * (taxRate / 100);
      const total = Math.max(0, subtotal - discount) + taxAmount;
      mobileFooterTotal.textContent = money(total);
    }
    if (mobileCheckoutFooter) {
      if (cart.length === 0) {
        mobileCheckoutFooter.classList.remove("is-visible");
      } else {
        mobileCheckoutFooter.classList.add("is-visible");
      }
    }
  }

  function updateTotals() {
    const subtotal = cart.reduce((s, c) => s + c.price * c.qty, 0);
    const discount = parseFloat(discountInput.value || 0);
    const taxRate = parseFloat(taxInput.value || 0);
    const taxAmount = Math.max(0, subtotal - discount) * (taxRate / 100);
    const total = Math.max(0, subtotal - discount) + taxAmount;
    subtotalEl.textContent = money(subtotal);
    totalEl.textContent = money(total);
  }

  discountInput.addEventListener("input", updateTotals);
  taxInput.addEventListener("input", updateTotals);

  function flashScanStatus(msg, isError) {
    const status = document.getElementById("scan-status");
    if (status) {
      status.textContent = msg;
      status.style.color = isError ? "var(--danger)" : "var(--text-2)";
    }
  }

  // Manual product tiles
  document.querySelectorAll(".product-tile").forEach((tile) => {
    tile.addEventListener("click", () => {
      addToCart({
        id: parseInt(tile.dataset.id),
        name: tile.dataset.name,
        price: parseFloat(tile.dataset.price),
        stock: parseFloat(tile.dataset.stock),
        unit: tile.dataset.unit,
      });
    });
  });

  // Scan input (USB scanner behaves like a keyboard + Enter)
  scanInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const code = scanInput.value.trim();
      scanInput.value = "";
      if (!code) return;
      lookupAndAdd(code);
    }
  });

  const aiCustomHint = document.getElementById("ai-custom-hint");
  const aiRegenerateBtn = document.getElementById("ai-regenerate-btn");

  if (aiRegenerateBtn && aiCustomHint) {
    aiRegenerateBtn.onclick = () => {
      const code = aiProdCode.value;
      const hint = aiCustomHint.value.trim();
      
      aiAnalyzingState.style.display = "block";
      aiResultState.style.display = "none";
      
      fetch(`/inventory/ai_lookup?code=${encodeURIComponent(code)}&hint=${encodeURIComponent(hint)}`)
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(data => {
          aiProdName.value = data.name;
          aiProdCategory.value = data.category;
          aiProdUnit.value = data.unit_label;
          aiProdPrice.value = data.unit_price;
          aiProdCost.value = data.cost_price;
          aiProdStock.value = data.stock_qty;
          aiInferredRegion.textContent = data.region_inferred;
          
          aiAnalyzingState.style.display = "none";
          aiResultState.style.display = "block";
        })
        .catch(() => {
          flashScanStatus("AI scan assistant failed to regenerate.", true);
          closeAiModal();
        });
    };
  }

  window.lookupAndAdd = function (code) {
    fetch(`/billing/lookup?code=${encodeURIComponent(code)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        if (data.found) {
          if (window.playScanBeep) window.playScanBeep();
          addToCart({
            id: data.product.id,
            name: data.product.name,
            price: data.product.unit_price,
            stock: data.product.stock_qty,
            unit: data.product.unit_label,
          });
          flashScanStatus(`Added ${data.product.name} to the sale.`, false);
        }
      })
      .catch(() => {
        flashScanStatus(`Code not found. AI scan assistant launched...`, false);
        if (aiModal) {
          aiModal.classList.add("is-open");
          aiAnalyzingState.style.display = "block";
          aiResultState.style.display = "none";
          aiProdCode.value = code;
          if (aiCustomHint) aiCustomHint.value = "";
          
          fetch(`/inventory/ai_lookup?code=${encodeURIComponent(code)}`)
            .then(r => r.ok ? r.json() : Promise.reject())
            .then(data => {
              aiProdName.value = data.name;
              aiProdCategory.value = data.category;
              aiProdUnit.value = data.unit_label;
              aiProdPrice.value = data.unit_price;
              aiProdCost.value = data.cost_price;
              aiProdStock.value = data.stock_qty;
              aiInferredRegion.textContent = data.region_inferred;
              
              aiAnalyzingState.style.display = "none";
              aiResultState.style.display = "block";
            })
            .catch(() => {
              flashScanStatus("AI scan assistant failed to analyze.", true);
              closeAiModal();
            });
        }
      });
  };

  if (saveAiBtn) {
    saveAiBtn.onclick = () => {
      const code = aiProdCode.value;
      const name = aiProdName.value.trim();
      const category = aiProdCategory.value.trim();
      const unit = aiProdUnit.value.trim();
      const price = parseFloat(aiProdPrice.value || 0);
      const cost = parseFloat(aiProdCost.value || 0);
      const stock = parseFloat(aiProdStock.value || 0);
      
      if (!name) {
        alert("Product name is required.");
        return;
      }
      
      saveAiBtn.disabled = true;
      const originalHtml = saveAiBtn.innerHTML;
      saveAiBtn.innerHTML = '<span class="btn-spinner" style="border-width:2px; width:12px; height:12px; margin-right:6px;"></span> Saving...';
      
      fetch("/barcode/create_product", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name,
          category: category,
          value: code,
          code_type: "CODE128",
          unit_price: price,
          cost_price: cost,
          stock_qty: stock,
          unit_label: unit,
          reorder_level: 5
        })
      })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(res => {
        closeAiModal();
        
        // Add to billing cart
        addToCart({
          id: res.product_id,
          name: res.name,
          price: price,
          stock: stock,
          unit: unit
        });
        
        // Show success toast
        const toast = document.getElementById("ai-toast");
        if (toast) {
          toast.innerHTML = `<span style="font-size:16px;">🤖</span> Added newly predicted <strong>${res.name}</strong> to sale!`;
          toast.classList.add("is-visible");
          setTimeout(() => {
            toast.classList.remove("is-visible");
          }, 4500);
        }
        
        // Restore button state
        saveAiBtn.disabled = false;
        saveAiBtn.innerHTML = originalHtml;
      })
      .catch(() => {
        alert("Failed to save product.");
        saveAiBtn.disabled = false;
        saveAiBtn.innerHTML = originalHtml;
      });
    };
  }

  // Live product search
  let searchTimer;
  searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const q = searchInput.value.trim();
      fetch(`/billing/search?q=${encodeURIComponent(q)}`)
        .then((r) => r.json())
        .then((products) => {
          productGrid.innerHTML = "";
          if (products.length === 0) {
            productGrid.innerHTML = `<div class="empty-row" style="grid-column:1/-1;">No products match "${q}".</div>`;
            return;
          }
          products.forEach((p) => {
            const tile = document.createElement("div");
            const low = p.stock_qty <= p.reorder_level;
            const out = p.stock_qty <= 0;
            tile.className = `product-tile ${low ? "low" : ""} ${out ? "out" : ""}`;
            tile.innerHTML = `
              <div class="p-name">${p.name}</div>
              <div class="p-price">${money(p.unit_price)}</div>
              <div class="p-stock">${Number(p.stock_qty).toFixed(1)} ${p.unit_label} in stock</div>
            `;
            tile.addEventListener("click", () =>
              addToCart({
                id: p.id,
                name: p.name,
                price: p.unit_price,
                stock: p.stock_qty,
                unit: p.unit_label,
              })
            );
            productGrid.appendChild(tile);
          });
        });
    }, 200);
  });

  // Checkout
  checkoutBtn.addEventListener("click", () => {
    if (cart.length === 0) return;
    checkoutBtn.disabled = true;
    checkoutBtn.textContent = "Processing\u2026";

    fetch("/billing/checkout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: cart.map((c) => ({ product_id: c.product_id, quantity: c.qty })),
        discount: parseFloat(discountInput.value || 0),
        tax_rate: parseFloat(taxInput.value || 0),
        payment_method: document.getElementById("payment-method").value,
      }),
    })
      .then((r) => r.json().then((data) => ({ ok: r.ok, data })))
      .then(({ ok, data }) => {
        if (!ok) {
          alert(data.error || "Could not complete the sale.");
          checkoutBtn.disabled = false;
          checkoutBtn.textContent = "Complete sale";
          return;
        }
        window.open(`/receipt/${data.invoice_no}`, "_blank");
        cart = [];
        renderCart();
        checkoutBtn.textContent = "Complete sale";
        setTimeout(() => window.location.reload(), 600);
      })
      .catch(() => {
        alert("Something went wrong completing the sale.");
        checkoutBtn.disabled = false;
        checkoutBtn.textContent = "Complete sale";
      });
  });

  // Mobile tab toggling
  const gridContainer = document.getElementById("pos-grid-container");
  const btnShowCatalog = document.getElementById("btn-show-catalog");
  const btnShowCart = document.getElementById("btn-show-cart");
  const mobileViewCartBtn = document.getElementById("mobile-view-cart-btn");

  if (gridContainer && btnShowCatalog && btnShowCart) {
    btnShowCatalog.addEventListener("click", () => {
      gridContainer.classList.remove("show-cart");
      btnShowCatalog.classList.add("is-active");
      btnShowCart.classList.remove("is-active");
    });

    const switchToCart = () => {
      gridContainer.classList.add("show-cart");
      btnShowCart.classList.add("is-active");
      btnShowCatalog.classList.remove("is-active");
    };

    btnShowCart.addEventListener("click", switchToCart);
    if (mobileViewCartBtn) {
      mobileViewCartBtn.addEventListener("click", switchToCart);
    }
  }

  renderCart();
})();
