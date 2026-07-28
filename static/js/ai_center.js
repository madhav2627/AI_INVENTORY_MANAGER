(function() {
  const currency = window.CURRENCY || "";

  // ── Fetch full AI report on load ──────────────────────────────────────
  fetch("/ai/report")
    .then(r => r.ok ? r.json() : Promise.reject(r))
    .then(report => {
      renderStatusBar(report.summary);
      renderStockHealth(report.stockout_predictions);
      renderDemandForecast(report.demand_forecast);
      renderAnomalies(report.anomalies);
      renderRestockQueue(report.restock_recommendations);
      renderDeadStock(report.dead_stock);
      renderSlowMoving(report.slow_moving);
      renderPurchaseOrders(report.purchase_orders);
      renderWarehouseInfo(report.warehouses);
      renderAgentLog(report.agent_log);
    })
    .catch(() => {
      document.getElementById("ai-analyzed-at").textContent = "Failed to load AI report.";
    });

  // ── Status Bar ────────────────────────────────────────────────────────
  function renderStatusBar(summary) {
    document.getElementById("count-critical").textContent = summary.critical;
    document.getElementById("count-warning").textContent = summary.warning;
    document.getElementById("count-safe").textContent = summary.safe;
    document.getElementById("ai-analyzed-at").textContent =
      "Analyzed: " + new Date(summary.analyzed_at).toLocaleString();
  }

  // ── Stock Health Grid ─────────────────────────────────────────────────
  function renderStockHealth(predictions) {
    const grid = document.getElementById("stock-health-grid");
    const nonSafe = predictions.filter(p => p.urgency !== "safe");
    const items = nonSafe.length ? nonSafe.slice(0, 12) : predictions.slice(0, 8);

    if (!items.length) {
      grid.innerHTML = '<p style="font-size:12px;color:var(--text-2);">No products in inventory.</p>';
      return;
    }

    grid.innerHTML = items.map(p => `
      <div class="stock-health-card" data-urgency="${p.urgency}">
        <div class="shc-name" title="${p.name}">${p.name}</div>
        <div class="shc-meta">${p.stock_qty} in stock · ${p.days_to_stockout !== null ? p.days_to_stockout + 'd left' : '∞'}</div>
        <div class="shc-urgency">${p.urgency}</div>
      </div>
    `).join("");

    document.getElementById("health-badge").textContent =
      nonSafe.length + " AT RISK";
  }

  // ── Demand Forecast Table ─────────────────────────────────────────────
  function renderDemandForecast(forecasts) {
    const tbody = document.getElementById("demand-tbody");
    const items = forecasts.slice(0, 10);
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="font-size:12px;color:var(--text-2);">No demand data.</td></tr>';
      return;
    }
    tbody.innerHTML = items.map(f => `
      <tr>
        <td style="font-weight:600; text-overflow:ellipsis; overflow:hidden; white-space:nowrap; max-width:180px;">${f.name}</td>
        <td style="text-align:right; font-family:var(--font-mono);">${f.daily_demand}</td>
        <td style="text-align:right; font-family:var(--font-mono);">${f.weekly_demand}</td>
        <td style="text-align:right; font-family:var(--font-mono);">${f.monthly_demand}</td>
        <td style="text-align:center;"><span class="badge ${f.method === 'ml_model' ? 'badge-ok' : 'badge-neutral'}" style="font-size:10px;padding:2px 6px;">${f.method === 'ml_model' ? 'ML Model' : 'Heuristic'}</span></td>
      </tr>
    `).join("");
  }

  // ── Anomalies ─────────────────────────────────────────────────────────
  function renderAnomalies(anomalies) {
    const list = document.getElementById("anomaly-list");
    const badge = document.getElementById("anomaly-badge");

    if (!anomalies.length) {
      list.innerHTML = '<li style="color:var(--success);font-size:12.5px;">✓ No anomalies detected. Everything looks clean.</li>';
      badge.textContent = "CLEAR";
      return;
    }

    badge.textContent = anomalies.length + " FOUND";
    list.innerHTML = anomalies.slice(0, 10).map(a => `
      <li>
        <span class="anomaly-dot anomaly-dot--${a.severity}"></span>
        <div class="anomaly-content">
          <strong>${a.product_name}</strong>
          <span class="badge badge-${a.severity === 'high' ? 'danger' : 'warning'}" style="font-size:9px;padding:1px 6px;margin-left:4px;">${a.type.replace('_', ' ')}</span>
          <div class="anomaly-detail">${a.detail}</div>
          <div class="anomaly-date">${a.date}</div>
        </div>
      </li>
    `).join("");
  }

  // ── Restock Queue ─────────────────────────────────────────────────────
  function renderRestockQueue(recs) {
    const tbody = document.getElementById("restock-tbody");
    const badge = document.getElementById("restock-badge");

    if (!recs.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="font-size:12px;color:var(--success);">✓ All products adequately stocked.</td></tr>';
      badge.textContent = "ALL GOOD";
      return;
    }

    badge.textContent = recs.length + " ITEMS";
    tbody.innerHTML = recs.slice(0, 15).map(r => `
      <tr>
        <td style="font-weight:600; text-overflow:ellipsis; overflow:hidden; white-space:nowrap; max-width:180px;">${r.name}</td>
        <td style="text-align:right; font-family:var(--font-mono);">${r.current_stock}</td>
        <td style="text-align:right; font-family:var(--font-mono); font-weight:700; color:var(--brass);">${r.recommended_qty}</td>
        <td style="text-align:right; font-family:var(--font-mono);">${currency}${r.estimated_cost.toLocaleString()}</td>
        <td style="text-align:right; font-family:var(--font-mono);">${r.days_to_stockout !== null ? r.days_to_stockout + 'd' : '—'}</td>
      </tr>
    `).join("");
  }

  // ── Dead Stock ────────────────────────────────────────────────────────
  function renderDeadStock(items) {
    const grid = document.getElementById("dead-stock-grid");
    const badge = document.getElementById("dead-badge");
    badge.textContent = items.length + " ITEMS";

    if (!items.length) {
      grid.innerHTML = '<li style="font-size:12.5px;color:var(--success);padding:10px 0;">✓ No dead stock found.</li>';
      return;
    }

    grid.innerHTML = items.slice(0, 8).map(d => `
      <li>
        <div>
          <div class="ds-title">${d.name}</div>
          <div class="ds-meta">${d.stock_qty} units · ${d.category}</div>
        </div>
        <span class="badge badge-danger ds-badge" title="Locked Capital">${currency}${d.stock_value.toLocaleString()}</span>
      </li>
    `).join("");
  }

  // ── Slow Moving ───────────────────────────────────────────────────────
  function renderSlowMoving(items) {
    const grid = document.getElementById("slow-stock-grid");
    const badge = document.getElementById("slow-badge");
    badge.textContent = items.length + " ITEMS";

    if (!items.length) {
      grid.innerHTML = '<li style="font-size:12.5px;color:var(--success);padding:10px 0;">✓ No slow-moving products detected.</li>';
      return;
    }

    grid.innerHTML = items.slice(0, 8).map(s => `
      <li>
        <div>
          <div class="ds-title">${s.name}</div>
          <div class="ds-meta">${s.units_sold} sold in ${s.days_checked}d · ${s.avg_daily}/day avg</div>
        </div>
        <span class="badge badge-warning ds-badge" title="Total Asset Value">${currency}${s.stock_value.toLocaleString()}</span>
      </li>
    `).join("");
  }

  // ── Purchase Orders ───────────────────────────────────────────────────
  function renderPurchaseOrders(pos) {
    const grid = document.getElementById("po-grid");
    const badge = document.getElementById("po-badge");

    if (!pos.length) {
      grid.innerHTML = '<p style="font-size:12px;color:var(--text-2);">No purchase orders yet. Click "Auto-Generate" to create PO drafts for items that need restocking.</p>';
      badge.textContent = "NONE";
      return;
    }

    badge.textContent = pos.length + " PO" + (pos.length > 1 ? "s" : "");
    grid.innerHTML = pos.map(po => `
      <div class="po-card">
        <div class="po-card-header">
          <span class="po-number">${po.po_number}</span>
          <span class="po-status po-status--${po.status}">${po.status}</span>
        </div>
        <div class="po-card-body">
          <div>${po.supplier_name}</div>
          <div style="font-weight:600;margin-top:4px;">${currency}${po.total_cost.toLocaleString()}</div>
        </div>
        <div class="po-card-footer">
          <a href="/ai/po/${po.id}/print" target="_blank" class="btn btn-sm btn-brass">🖨️ Print</a>
        </div>
      </div>
    `).join("");
  }

  // ── Warehouse Info ────────────────────────────────────────────────────
  function renderWarehouseInfo(data) {
    const el = document.getElementById("warehouse-info");

    if (!data.warehouses.length) {
      el.innerHTML = '<p style="font-size:12px;color:var(--text-2);">Single location mode. Add warehouses from Settings to enable multi-warehouse optimization.</p>';
      return;
    }

    let html = '<div style="margin-bottom:8px;">' +
      data.warehouses.map(w => `<span class="badge badge-neutral" style="margin-right:6px;font-size:11px;padding:3px 8px;">${w.name}${w.is_default ? ' ★' : ''}</span>`).join("") +
      '</div>';

    if (data.suggestions.length) {
      html += '<p style="font-size:12px;font-weight:600;margin:8px 0 4px;">Transfer Suggestions:</p>';
      html += data.suggestions.map(s =>
        `<div style="font-size:11px;padding:4px 0;border-bottom:1px solid var(--border-light, rgba(0,0,0,0.04));">
          Move <strong>${s.quantity}</strong> × ${s.product_name}: ${s.from_warehouse} → ${s.to_warehouse}
        </div>`
      ).join("");
    } else {
      html += '<p style="font-size:11px;color:var(--text-2);margin-top:4px;">' + data.message + '</p>';
    }
    el.innerHTML = html;
  }

  // ── Agent Log ─────────────────────────────────────────────────────────
  function renderAgentLog(logs) {
    const list = document.getElementById("agent-log");
    const icons = {
      nlp: "💬", po: "📋", transfer: "🔀", restock: "🔄",
      anomaly: "🔍", forecast: "📈"
    };

    if (!logs.length) {
      list.innerHTML = '<li style="color:var(--text-2);font-size:12px;">No agent activity recorded yet.</li>';
      return;
    }

    list.innerHTML = logs.slice(0, 25).map(l => `
      <li>
        <span class="agent-log-icon">${icons[l.action_type] || '🤖'}</span>
        <span class="agent-log-text">${l.summary}</span>
        <span class="agent-log-time">${new Date(l.created_at).toLocaleString()}</span>
      </li>
    `).join("");
  }

  // ── NLP Command Bar ───────────────────────────────────────────────────
  const nlpInput = document.getElementById("nlp-input");
  const nlpSend = document.getElementById("nlp-send-btn");
  const nlpResponse = document.getElementById("nlp-response");

  function sendNlpQuery() {
    const query = nlpInput.value.trim();
    if (!query) return;
    nlpInput.value = "";
    nlpResponse.classList.remove("is-visible");
    nlpResponse.innerHTML = "Thinking...";
    nlpResponse.classList.add("is-visible");

    fetch("/ai/nlp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    })
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(result => {
        let html = `<strong>🤖 AI:</strong> ${result.response}`;

        // Render data table if applicable
        if (result.data && Array.isArray(result.data) && result.data.length > 0) {
          const keys = Object.keys(result.data[0]).filter(k => !k.includes("product_id"));
          html += `<table class="ai-nlp-data-table"><thead><tr>${keys.map(k => `<th>${k.replace(/_/g, ' ')}</th>`).join('')}</tr></thead><tbody>`;
          result.data.slice(0, 10).forEach(row => {
            html += '<tr>' + keys.map(k => {
              let val = row[k];
              if (typeof val === 'number' && !Number.isInteger(val)) val = val.toFixed(2);
              return `<td>${val !== null && val !== undefined ? val : '—'}</td>`;
            }).join('') + '</tr>';
          });
          html += '</tbody></table>';
          if (result.data.length > 10) {
            html += `<p style="font-size:11px;color:var(--text-2);margin-top:4px;">Showing 10 of ${result.data.length} results.</p>`;
          }
        }

        nlpResponse.innerHTML = html;
        nlpResponse.classList.add("is-visible");
      })
      .catch(() => {
        nlpResponse.innerHTML = '<strong>Error:</strong> Could not process your query.';
        nlpResponse.classList.add("is-visible");
      });
  }

  if (nlpSend) nlpSend.addEventListener("click", sendNlpQuery);
  if (nlpInput) nlpInput.addEventListener("keydown", e => {
    if (e.key === "Enter") sendNlpQuery();
  });

  // ── Generate PO Button ────────────────────────────────────────────────
  const poBtn = document.getElementById("generate-po-btn");
  if (poBtn) {
    poBtn.addEventListener("click", () => {
      poBtn.disabled = true;
      poBtn.textContent = "Generating...";

      fetch("/ai/restock", { method: "POST" })
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(result => {
          poBtn.textContent = "🤖 Auto-Generate Purchase Orders";
          poBtn.disabled = false;
          if (result.po_ids && result.po_ids.length) {
            renderPurchaseOrders(result.po_ids.map(p => ({
              id: p.po_id,
              po_number: p.po_number,
              status: "draft",
              supplier_name: p.category + " Supplier",
              total_cost: p.total_cost,
            })));
            // Show success in NLP response
            nlpResponse.innerHTML = `<strong>🤖 AI:</strong> ${result.message}`;
            nlpResponse.classList.add("is-visible");
          } else {
            nlpResponse.innerHTML = `<strong>🤖 AI:</strong> ${result.message}`;
            nlpResponse.classList.add("is-visible");
          }
        })
        .catch(() => {
          poBtn.textContent = "🤖 Auto-Generate Purchase Orders";
          poBtn.disabled = false;
        });
    });
  }

  // ── Rotating NLP Command Suggestions ──────────────────────────────────
  const suggestions = [
    "show low stock",
    "sales today",
    "sales this week",
    "show dead stock",
    "show critical",
    "show anomalies",
    "demand forecast",
    "restock",
    "check health"
  ];
  let suggestionIndex = 0;
  const suggestionRow = document.getElementById("ai-nlp-suggestions");
  const suggestionLink = document.getElementById("suggestion-link");

  if (suggestionLink && suggestionRow) {
    suggestionLink.addEventListener("click", () => {
      nlpInput.value = suggestionLink.textContent.replace(/"/g, "");
      sendNlpQuery();
    });

    setInterval(() => {
      suggestionRow.style.opacity = 0;
      setTimeout(() => {
        suggestionIndex = (suggestionIndex + 1) % suggestions.length;
        suggestionLink.textContent = `"${suggestions[suggestionIndex]}"`;
        suggestionRow.style.opacity = 1;
      }, 300);
    }, 5000);
  }

})();
