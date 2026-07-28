(function () {
  const currency = window.CURRENCY || "";
  let trendChart, categoryChart;

  function money(n) {
    return currency + Number(n || 0).toFixed(2);
  }

  function load(days) {
    fetch(`/reports/data?days=${days}`)
      .then((r) => r.json())
      .then((data) => {
        document.getElementById("stat-revenue").textContent = money(data.totals.revenue);
        document.getElementById("stat-orders").textContent = data.totals.orders;
        document.getElementById("stat-avg").textContent = money(data.totals.avg_order);

        const labels = data.trend.map((r) =>
          new Date(r.d).toLocaleDateString(undefined, { month: "short", day: "numeric" })
        );
        const values = data.trend.map((r) => r.total);

        if (trendChart) trendChart.destroy();
        trendChart = new Chart(document.getElementById("trendChart"), {
          type: "bar",
          data: {
            labels,
            datasets: [{ data: values, backgroundColor: "#a9824f", borderRadius: 3, maxBarThickness: 22 }],
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              y: { grid: { color: "#efece2" }, ticks: { font: { family: "IBM Plex Mono", size: 11 } } },
              x: { grid: { display: false }, ticks: { font: { family: "IBM Plex Mono", size: 11 } } },
            },
          },
        });

        const tbody = document.getElementById("top-products-body");
        tbody.innerHTML = "";
        if (data.top_products.length === 0) {
          tbody.innerHTML = `<tr><td colspan="3"><div class="empty-row">No sales in this period yet.</div></td></tr>`;
        } else {
          data.top_products.forEach((p) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `<td class="row-name">${p.product_name}</td><td class="num">${p.qty}</td><td class="num">${money(p.revenue)}</td>`;
            tbody.appendChild(tr);
          });
        }

        if (categoryChart) categoryChart.destroy();
        categoryChart = new Chart(document.getElementById("categoryChart"), {
          type: "doughnut",
          data: {
            labels: data.categories.map((c) => c.category || "Uncategorised"),
            datasets: [
              {
                data: data.categories.map((c) => c.revenue),
                backgroundColor: ["#a9824f", "#12203a", "#2f6f4e", "#b56a1e", "#5c6572", "#8a90a0"],
                borderWidth: 0,
              },
            ],
          },
          options: {
            plugins: { legend: { position: "bottom", labels: { font: { family: "Inter", size: 11 }, boxWidth: 10 } } },
          },
        });
      });
  }

  document.getElementById("range-select").addEventListener("change", (e) => load(e.target.value));
  load(30);
})();
