/* Distributor Intelligence Platform — front-end controller.
   All charts are hand-drawn on <canvas>; no charting library. Data comes from
   the JSON API. Author: Dimitres Kisimov, 2026. */
"use strict";

/* ------------------------------------------------------------------ helpers */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const PALETTE = { blue: "#2f6bff", green: "#1d9e6f", pink: "#ea4b71", amber: "#e8a33d" };

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
function eur(n) {
  const a = Math.abs(n);
  if (a >= 1e6) return "€" + (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return "€" + (n / 1e3).toFixed(0) + "k";
  return "€" + Math.round(n).toLocaleString();
}
function eurFull(n) { return "€" + Math.round(n).toLocaleString("en-US"); }
function pct(n, d = 1) { return (n * 100).toFixed(d) + "%"; }
function num(n) { return Math.round(n).toLocaleString("en-US"); }
function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}

/* HiDPI canvas: returns a 2d context sized to CSS pixels. */
function fitCanvas(canvas, cssHeight) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || canvas.parentElement.clientWidth;
  const h = cssHeight || canvas.clientHeight || 260;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  canvas.style.height = h + "px";
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

/* tooltip */
const tip = $("#tooltip");
function showTip(x, y, html) {
  tip.innerHTML = html;
  tip.style.opacity = "1";
  const pad = 14;
  let left = x + pad, top = y + pad;
  const r = tip.getBoundingClientRect();
  if (left + r.width > window.innerWidth) left = x - r.width - pad;
  if (top + r.height > window.innerHeight) top = y - r.height - pad;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
}
function hideTip() { tip.style.opacity = "0"; }

/* niceTicks for axes */
function niceTicks(min, max, count) {
  const range = max - min || 1;
  const raw = range / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const start = Math.floor(min / step) * step;
  const ticks = [];
  for (let v = start; v <= max + step * 0.5; v += step) ticks.push(v);
  return ticks;
}

/* ------------------------------------------------------------------ theme */
function initTheme() {
  const saved = localStorage.getItem("dip-theme") || "light";
  document.documentElement.setAttribute("data-theme", saved);
  $("#themeToggle").addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("dip-theme", next);
    renderAll(); // redraw canvases with new colors
  });
}

/* ------------------------------------------------------------------ nav */
function initNav() {
  $$(".nav-item").forEach((item) => {
    item.addEventListener("click", () => {
      const t = document.getElementById(item.dataset.target);
      if (t) t.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
  const secs = $$(".nav-item").map((i) => document.getElementById(i.dataset.target)).filter(Boolean);
  const obs = new IntersectionObserver(
    (entries) => {
      entries.forEach((e) => {
        if (e.isIntersecting) {
          $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.target === e.target.id));
        }
      });
    },
    { rootMargin: "-45% 0px -50% 0px" }
  );
  secs.forEach((s) => obs.observe(s));
}

/* ------------------------------------------------------------------ state */
const STATE = { kpis: null, plan: null, forecast: null, margin: null, abc: null, rfm: null, revenue: null, routes: null, assort: null, crosssell: null, crossProduct: "", crossRecs: null, kpiDrilldown: null, kpiDrillOpen: null, breakdownDim: "region", routeMode: "optimized", maxChange: 0.15, planSeq: 0, drillCell: null, drillSegment: null, pinned: null };

/* ------------------------------------------------------------------ KPIs */
function renderKPIs() {
  const k = STATE.kpis, p = STATE.plan;
  $("#kpi-revenue").textContent = eur(k.revenue);
  $("#kpi-margin").textContent = pct(k.gross_margin_pct);
  $("#kpi-margin-sub").textContent = eur(k.gross_margin) + " gross margin";
  $("#kpi-yoy").textContent = (k.yoy >= 0 ? "+" : "") + pct(k.yoy);
  $("#kpi-yoy-sub").className = "delta " + (k.yoy >= 0 ? "up" : "down");
  $("#kpi-yoy-sub").textContent = "last 12 vs prior 12";
  $("#kpi-uplift").textContent = eur(p.expected_uplift_eur);
  $("#kpi-uplift-sub").textContent = pct(p.expected_uplift_pct) + " of annual gross margin";
  $("#kpi-otif").textContent = pct(k.otif);
}

/* ------------------------------------------------------------ forecast chart */
function renderForecast() {
  const f = STATE.forecast;
  if (!f) return;
  const canvas = $("#forecastChart");
  const { ctx, w, h } = fitCanvas(canvas, 300);
  const padL = 52, padR = 14, padT = 14, padB = 28;
  const hist = f.history, fc = f.forecast, lo = f.lower, up = f.upper;
  const n = hist.length + fc.length;
  const allVals = hist.concat(up, lo, fc);
  const ymax = Math.max(...allVals) * 1.08, ymin = Math.min(0, ...allVals);
  const X = (i) => padL + (i / (n - 1)) * (w - padL - padR);
  const Y = (v) => padT + (1 - (v - ymin) / (ymax - ymin)) * (h - padT - padB);
  const line = cssVar("--line"), muted = cssVar("--muted");

  // gridlines + y labels
  ctx.font = "11px -apple-system, Segoe UI, sans-serif";
  ctx.textBaseline = "middle";
  niceTicks(ymin, ymax, 5).forEach((t) => {
    if (t < ymin) return;
    ctx.strokeStyle = line; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, Y(t)); ctx.lineTo(w - padR, Y(t)); ctx.stroke();
    ctx.fillStyle = muted; ctx.textAlign = "right";
    ctx.fillText(eur(t), padL - 8, Y(t));
  });

  // forecast band
  ctx.beginPath();
  for (let i = 0; i < fc.length; i++) { const x = X(hist.length + i); const y = Y(up[i]); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
  for (let i = fc.length - 1; i >= 0; i--) ctx.lineTo(X(hist.length + i), Y(lo[i]));
  ctx.closePath();
  ctx.fillStyle = "rgba(29,158,111,0.16)"; ctx.fill();

  // actual line
  ctx.lineWidth = 2.4; ctx.lineJoin = "round";
  ctx.strokeStyle = PALETTE.blue; ctx.beginPath();
  hist.forEach((v, i) => (i === 0 ? ctx.moveTo(X(i), Y(v)) : ctx.lineTo(X(i), Y(v))));
  ctx.stroke();

  // connector + forecast line (dashed)
  ctx.strokeStyle = PALETTE.green; ctx.setLineDash([6, 5]); ctx.beginPath();
  ctx.moveTo(X(hist.length - 1), Y(hist[hist.length - 1]));
  fc.forEach((v, i) => ctx.lineTo(X(hist.length + i), Y(v)));
  ctx.stroke(); ctx.setLineDash([]);

  // dots
  const pts = [];
  hist.forEach((v, i) => { pts.push({ x: X(i), y: Y(v), v, label: f.history_months[i], type: "actual" }); });
  fc.forEach((v, i) => { pts.push({ x: X(hist.length + i), y: Y(v), v, label: f.forecast_months[i], type: "forecast", lo: lo[i], up: up[i] }); });
  pts.forEach((p) => {
    ctx.fillStyle = p.type === "actual" ? PALETTE.blue : PALETTE.green;
    ctx.beginPath(); ctx.arc(p.x, p.y, 2.6, 0, 7); ctx.fill();
  });

  // x labels (every 3rd)
  ctx.fillStyle = muted; ctx.textAlign = "center"; ctx.textBaseline = "top";
  pts.forEach((p, i) => { if (i % 3 === 0) ctx.fillText(p.label.slice(2), p.x, h - padB + 8); });

  // hover
  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    let best = null, bd = 1e9;
    pts.forEach((p) => { const d = Math.abs(p.x - mx); if (d < bd) { bd = d; best = p; } });
    if (best && bd < 26) {
      let html = `<b>${best.label}</b><br>${best.type === "actual" ? "Actual" : "Forecast"}: ${eurFull(best.v)}`;
      if (best.type === "forecast") html += `<br>Band: ${eur(best.lo)}–${eur(best.up)}`;
      showTip(e.clientX, e.clientY, html);
    } else hideTip();
  };
  canvas.onmouseleave = hideTip;

  $("#forecast-pill").textContent = "MASE " + (f.mase ?? "—");
}

/* ------------------------------------------------------------ breakdown bars */
function renderBreakdown() {
  const rb = STATE.revenue;
  if (!rb) return;
  const rows = rb[STATE.breakdownDim];
  const max = Math.max(...rows.map((r) => r.revenue));
  const colors = [PALETTE.blue, PALETTE.green, PALETTE.amber, PALETTE.pink, "#7b61ff", "#00b3b3", "#e8a33d", "#5a6b8c"];
  $("#breakdownBars").innerHTML = rows
    .map(
      (r, i) => `
      <div class="bar-row">
        <span title="${r.label}">${r.label}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(r.revenue / max) * 100}%;background:${colors[i % colors.length]}"></div></div>
        <span class="bar-val">${eur(r.revenue)}</span>
      </div>`
    )
    .join("");
}

/* ------------------------------------------------------------ margin waterfall */
function renderMargin() {
  const m = STATE.margin;
  if (!m) return;
  const canvas = $("#marginChart");
  const { ctx, w, h } = fitCanvas(canvas, 280);
  const padL = 54, padR = 14, padT = 16, padB = 40;
  const items = [{ label: "Baseline", value: m.baseline, type: "total" }];
  m.steps.forEach((s) => items.push({ label: s.label, value: s.value, type: "delta" }));
  items.push({ label: "Current", value: m.current, type: "total" });

  // compute running tops
  let run = 0; const bars = [];
  items.forEach((it) => {
    if (it.type === "total") { bars.push({ ...it, base: 0, top: it.value }); run = it.value; }
    else { const base = run; run += it.value; bars.push({ ...it, base: Math.min(base, run), top: Math.max(base, run) }); }
  });
  const ymax = Math.max(...bars.map((b) => b.top)) * 1.1, ymin = 0;
  const bw = (w - padL - padR) / items.length * 0.6;
  const gap = (w - padL - padR) / items.length;
  const Y = (v) => padT + (1 - (v - ymin) / (ymax - ymin)) * (h - padT - padB);
  const line = cssVar("--line"), muted = cssVar("--muted"), ink = cssVar("--ink");

  ctx.font = "11px -apple-system, Segoe UI, sans-serif"; ctx.textBaseline = "middle";
  niceTicks(ymin, ymax, 4).forEach((t) => {
    ctx.strokeStyle = line; ctx.beginPath(); ctx.moveTo(padL, Y(t)); ctx.lineTo(w - padR, Y(t)); ctx.stroke();
    ctx.fillStyle = muted; ctx.textAlign = "right"; ctx.fillText(eur(t), padL - 8, Y(t));
  });

  bars.forEach((b, i) => {
    const x = padL + i * gap + (gap - bw) / 2;
    const yTop = Y(b.top), yBase = Y(b.base);
    let color = ink;
    if (b.type === "delta") color = b.value >= 0 ? PALETTE.green : PALETTE.pink;
    ctx.fillStyle = color;
    ctx.beginPath();
    const rr = 4, ht = Math.max(2, yBase - yTop);
    ctx.roundRect(x, yTop, bw, ht, rr); ctx.fill();
    // connector line
    if (i < bars.length - 1) {
      ctx.strokeStyle = muted; ctx.setLineDash([3, 3]);
      const yc = b.type === "total" ? Y(b.value) : Y(run === b.top ? b.top : b.top);
      ctx.beginPath(); ctx.moveTo(x + bw, Y(b.type === "total" ? b.value : (b.value >= 0 ? b.top : b.base)));
      ctx.lineTo(x + gap, Y(b.type === "total" ? b.value : (b.value >= 0 ? b.top : b.base))); ctx.stroke();
      ctx.setLineDash([]); void yc;
    }
    // label + value
    ctx.fillStyle = muted; ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.fillText(b.label, x + bw / 2, h - padB + 8);
    ctx.fillStyle = color; ctx.textBaseline = "bottom"; ctx.font = "bold 11px -apple-system, Segoe UI, sans-serif";
    const vtxt = (b.type === "delta" && b.value >= 0 ? "+" : "") + eur(b.value);
    ctx.fillText(vtxt, x + bw / 2, yTop - 4);
    ctx.font = "11px -apple-system, Segoe UI, sans-serif"; ctx.textBaseline = "middle";
  });
}

/* ------------------------------------------------------------ ABC-XYZ heatmap */
function renderHeat() {
  const az = STATE.abc;
  if (!az) return;
  const grid = az.grid;
  const maxRev = Math.max(...Object.values(grid).map((g) => g.revenue));
  const abc = ["A", "B", "C"], xyz = ["X", "Y", "Z"];
  const el = $("#heat");
  let html = `<div class="corner"></div>` + xyz.map((x) => `<div class="h-col">${x}</div>`).join("");
  abc.forEach((a) => {
    html += `<div class="h-row">${a}</div>`;
    xyz.forEach((x) => {
      const cell = grid[a + x];
      const t = maxRev ? cell.revenue / maxRev : 0;
      // blue→green scale by revenue intensity
      const alpha = 0.18 + t * 0.82;
      const bg = `color-mix(in srgb, ${PALETTE.blue} ${Math.round(alpha * 100)}%, ${PALETTE.green} ${Math.round((1 - alpha) * 40)}%)`;
      html += `<div class="cell" role="button" tabindex="0" data-cell="${a + x}" style="background:${bg}" data-tip="${a + x}: ${cell.count} SKUs · ${eurFull(cell.revenue)} — click for the SKU list">
        <span class="c-name">${a + x}</span>
        <span class="c-count">${cell.count}</span>
        <span class="c-rev">${eur(cell.revenue)}</span>
      </div>`;
    });
  });
  el.innerHTML = html;
  $$("#heat .cell").forEach((c) => {
    c.addEventListener("mousemove", (e) => showTip(e.clientX, e.clientY, c.dataset.tip));
    c.addEventListener("mouseleave", hideTip);
    c.addEventListener("click", () => toggleSkuDrill(c.dataset.cell));
    c.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleSkuDrill(c.dataset.cell); } });
  });
  renderSkuDrill(); // re-apply an open drill-down after a data reload/redraw
}

/* ---------------------------------------------- drill-downs (names, not tiles)
   The API already ships per-SKU and per-customer rows with abc-xyz / rfm;
   clicking a heatmap cell or a segment bar renders them, so "CZ is dead
   weight" becomes an actual SKU list and "call the at-risk accounts" becomes
   actual customer names. */
function toggleSkuDrill(cell) {
  STATE.drillCell = STATE.drillCell === cell ? null : cell;
  renderSkuDrill();
}

function renderSkuDrill() {
  const box = $("#skuDrill");
  const cell = STATE.drillCell;
  $$("#heat .cell").forEach((c) => c.classList.toggle("sel", c.dataset.cell === cell));
  if (!cell || !STATE.abc) { box.hidden = true; return; }
  const rows = STATE.abc.per_sku.filter((s) => s.cell === cell).sort((a, b) => b.revenue - a.revenue);
  const revSum = rows.reduce((t, s) => t + s.revenue, 0);
  $("#skuDrillTitle").textContent = `${cell} — ${rows.length} SKU${rows.length === 1 ? "" : "s"} · ${eur(revSum)} revenue (24 mo)`;
  $("#skuDrillTable").innerHTML =
    `<tr><th>SKU</th><th>Name</th><th>Category</th><th class="num">Revenue</th><th class="num" title="Coefficient of variation of monthly units — higher = less stable demand">CV</th></tr>` +
    rows
      .map(
        (s) => `<tr>
          <td>${s.sku_id}</td>
          <td>${s.name}</td>
          <td>${s.category}</td>
          <td class="num">${eurFull(s.revenue)}</td>
          <td class="num">${s.cv.toFixed(2)}</td>
        </tr>`
      )
      .join("");
  box.hidden = false;
}

function toggleCustDrill(segment) {
  STATE.drillSegment = STATE.drillSegment === segment ? null : segment;
  renderCustDrill();
}

function renderCustDrill() {
  const box = $("#custDrill");
  const seg = STATE.drillSegment;
  $$("#rfmBars .bar-row").forEach((r) => r.classList.toggle("sel", r.dataset.segment === seg));
  if (!seg || !STATE.rfm) { box.hidden = true; return; }
  const rows = STATE.rfm.per_customer.filter((c) => c.segment === seg).sort((a, b) => b.monetary - a.monetary);
  const monSum = rows.reduce((t, c) => t + c.monetary, 0);
  $("#custDrillTitle").textContent = `${seg} — ${rows.length} customer${rows.length === 1 ? "" : "s"} · ${eur(monSum)}`;
  $("#custDrillTable").innerHTML =
    `<tr><th>Customer</th><th title="Recency · Frequency · Monetary quintile scores (5 = best)">R·F·M</th><th>Last order</th><th class="num">Orders</th><th class="num">Value</th></tr>` +
    rows
      .map(
        (c) => `<tr>
          <td>${c.name} <span class="dim">${c.customer_id}</span></td>
          <td>${c.r}·${c.f}·${c.m}</td>
          <td>${c.recency_months === 0 ? "this month" : c.recency_months + " mo ago"}</td>
          <td class="num">${c.frequency}</td>
          <td class="num">${eurFull(c.monetary)}</td>
        </tr>`
      )
      .join("");
  box.hidden = false;
}

function initDrills() {
  $("#skuDrillClose").addEventListener("click", () => { STATE.drillCell = null; renderSkuDrill(); });
  $("#custDrillClose").addEventListener("click", () => { STATE.drillSegment = null; renderCustDrill(); });
}

/* ---------------------------------------------- KPI headline drill-downs
   Clicking the Revenue tile opens revenue by (region x channel) segment;
   clicking the Gross-margin tile opens margin by category. The rows partition
   the same ledger the tiles are computed from, so they sum to the headline
   exactly — the server (and the exported Drill-downs sheet) quote the same
   numbers. */
function toggleKpiDrill(which) {
  STATE.kpiDrillOpen = STATE.kpiDrillOpen === which ? null : which;
  renderKpiDrill();
}

function renderKpiDrill() {
  const box = $("#kpiDrillCard");
  const which = STATE.kpiDrillOpen;
  const dd = STATE.kpiDrilldown;
  $("#kpiRevenueTile").classList.toggle("sel", which === "revenue");
  $("#kpiMarginTile").classList.toggle("sel", which === "margin");
  if (!which || !dd) { box.hidden = true; return; }
  if (which === "revenue") {
    const d = dd.revenue_by_segment;
    $("#kpiDrillTitle").textContent = `Revenue by segment (region × channel) — ${eurFull(d.total_revenue)} total`;
    $("#kpiDrillNote").textContent = "the 15 segments partition the ledger and sum to the headline exactly";
    $("#kpiDrillTable").innerHTML =
      `<tr><th>Region</th><th>Channel</th><th class="num">Revenue</th><th class="num">Share</th></tr>` +
      d.rows.map((r) => `<tr>
          <td>${esc(r.region)}</td>
          <td>${esc(r.channel)}</td>
          <td class="num">${eurFull(r.revenue)}</td>
          <td class="num">${pct(r.share)}</td>
        </tr>`).join("") +
      `<tr><td><b>Total</b></td><td></td><td class="num"><b>${eurFull(d.total_revenue)}</b></td><td class="num">100.0%</td></tr>`;
  } else {
    const d = dd.margin_by_category;
    $("#kpiDrillTitle").textContent = `Gross margin by category — ${eurFull(d.total_gross_margin)} total`;
    $("#kpiDrillNote").textContent = "category margins sum to the headline gross margin exactly";
    $("#kpiDrillTable").innerHTML =
      `<tr><th>Category</th><th class="num">Revenue</th><th class="num">COGS</th><th class="num">Gross margin</th><th class="num">Margin %</th><th class="num">Share of margin</th></tr>` +
      d.rows.map((r) => `<tr>
          <td>${esc(r.category)}</td>
          <td class="num">${eurFull(r.revenue)}</td>
          <td class="num">${eurFull(r.cogs)}</td>
          <td class="num">${eurFull(r.gross_margin)}</td>
          <td class="num">${pct(r.margin_pct)}</td>
          <td class="num">${pct(r.share_of_margin)}</td>
        </tr>`).join("") +
      `<tr><td><b>Total</b></td><td class="num"><b>${eurFull(d.total_revenue)}</b></td><td class="num"><b>${eurFull(d.total_cogs)}</b></td><td class="num"><b>${eurFull(d.total_gross_margin)}</b></td><td></td><td class="num">100.0%</td></tr>`;
  }
  box.hidden = false;
}

function initKpiDrill() {
  const wire = (id, which) => {
    const el = $(id);
    el.addEventListener("click", () => toggleKpiDrill(which));
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleKpiDrill(which); } });
  };
  wire("#kpiRevenueTile", "revenue");
  wire("#kpiMarginTile", "margin");
  $("#kpiDrillClose").addEventListener("click", () => { STATE.kpiDrillOpen = null; renderKpiDrill(); });
}

/* ------------------------------------------------------------ cross-sell
   Association rules (Apriori, adapted from the author's market-basket-analysis
   engine) mined server-side over the synthetic order baskets and cached per
   dataset. The card always restates the honesty note the API sends: synthetic
   demo data, and lift = co-occurrence, not a promise of uplift. */
function crossRows(rules) {
  if (!rules || !rules.length) {
    return `<tr><td colspan="7" class="dim">No association rules clear the thresholds here.</td></tr>`;
  }
  return rules.map((r) => `<tr>
      <td><b>${esc(r.antecedent)}</b> <span class="dim">${esc(r.antecedent_name)}</span></td>
      <td><b>${esc(r.consequent)}</b> <span class="dim">${esc(r.consequent_name)}</span></td>
      <td>${esc(r.consequent_category)}</td>
      <td class="num">${pct(r.support)}</td>
      <td class="num">${pct(r.confidence, 0)}</td>
      <td class="num">${r.lift.toFixed(2)}×</td>
      <td class="num">${r.support_count}${r.thin_support ? ' <span class="badge-thin" title="Backed by few baskets — metrics are unstable">thin</span>' : ""}</td>
    </tr>`).join("");
}

function renderCrosssell() {
  const cs = STATE.crosssell;
  if (!cs) return;
  const note = $("#crossNote"), pill = $("#crossPill"), table = $("#crossTable"), sel = $("#crossProduct");
  note.textContent = cs.note;
  if (!cs.available) {
    pill.textContent = "unavailable on imported data";
    sel.disabled = true;
    table.innerHTML = `<tr><td class="dim">${esc(cs.note)}</td></tr>`;
    return;
  }
  sel.disabled = false;
  // (re)build the product picker, keeping the current selection
  const current = STATE.crossProduct;
  sel.innerHTML = `<option value="">All products (top rules by lift)</option>` +
    cs.products.map((p) => `<option value="${esc(p.sku_id)}"${p.sku_id === current ? " selected" : ""}>${esc(p.sku_id)} — ${esc(p.name)} (${p.n_rules})</option>`).join("");
  const head = `<tr><th>If they buy</th><th>Also sells</th><th>Category</th><th class="num" title="Share of all baskets containing both SKUs">Support</th><th class="num" title="Share of the antecedent's baskets that also contain the consequent">Confidence</th><th class="num" title="Confidence vs the consequent's baseline rate — observed co-occurrence, not causation">Lift</th><th class="num">Baskets</th></tr>`;
  if (current && STATE.crossRecs && STATE.crossRecs.product === current) {
    pill.textContent = `${STATE.crossRecs.recommendations.length} rules · ${cs.n_baskets} baskets`;
    table.innerHTML = head + crossRows(STATE.crossRecs.recommendations);
  } else {
    pill.textContent = `${cs.n_rules} rules · ${cs.n_baskets} baskets`;
    table.innerHTML = head + crossRows(cs.rules);
  }
}

function initCrosssell() {
  $("#crossProduct").addEventListener("change", async (e) => {
    STATE.crossProduct = e.target.value;
    STATE.crossRecs = null;
    if (STATE.crossProduct) {
      try {
        STATE.crossRecs = await getJSON("/api/crosssell?product=" + encodeURIComponent(STATE.crossProduct) + "&top=10");
      } catch (err) {
        $("#crossPill").textContent = "load failed — pick a product to retry";
        return;
      }
    }
    renderCrosssell();
  });
}

/* ------------------------------------------------------------ RFM segments */
function renderRFM() {
  const rfm = STATE.rfm;
  if (!rfm) return;
  const segs = rfm.segments;
  const max = Math.max(...segs.map((s) => s.monetary));
  const cmap = { Champions: PALETTE.green, Loyal: PALETTE.blue, "New / Promising": PALETTE.amber, "At Risk (high value)": PALETTE.pink, "Needs Attention": "#7b61ff", Hibernating: "#5a6b8c" };
  $("#rfmBars").innerHTML = segs
    .map(
      (s) => `<div class="bar-row clickable" role="button" tabindex="0" data-segment="${s.segment}" title="${s.segment} — click for the customer list">
        <span>${s.segment}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(s.monetary / max) * 100}%;background:${cmap[s.segment] || PALETTE.blue}"></div></div>
        <span class="bar-val">${s.count} · ${eur(s.monetary)}</span>
      </div>`
    )
    .join("");
  $$("#rfmBars .bar-row").forEach((r) => {
    r.addEventListener("click", () => toggleCustDrill(r.dataset.segment));
    r.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleCustDrill(r.dataset.segment); } });
  });
  renderCustDrill(); // re-apply an open drill-down after a data reload/redraw
}

/* ------------------------------------------------------------ route map */
function renderRoutes() {
  const r = STATE.routes;
  if (!r) return;
  const canvas = $("#routeChart");
  const { ctx, w, h } = fitCanvas(canvas, 260);
  const routes = STATE.routeMode === "optimized" ? r.routes : r.baseline_routes;
  // bounds from all stops
  const pts = [];
  routes.forEach((rt) => rt.stops.forEach((s) => pts.push(s)));
  pts.push({ x: r.depot.x, y: r.depot.y });
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const pad = 22;
  const sx = (x) => pad + ((x - minX) / (maxX - minX || 1)) * (w - 2 * pad);
  const sy = (y) => (h - pad) - ((y - minY) / (maxY - minY || 1)) * (h - 2 * pad);
  const routeColors = [PALETTE.blue, PALETTE.green, PALETTE.pink, PALETTE.amber, "#7b61ff", "#00b3b3", "#e8557f", "#3a7bd5"];

  // routes
  routes.forEach((rt, i) => {
    const c = routeColors[i % routeColors.length];
    ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.lineJoin = "round";
    ctx.beginPath();
    rt.stops.forEach((s, j) => { const x = sx(s.x), y = sy(s.y); j === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
    ctx.stroke();
    // customer dots
    rt.stops.forEach((s) => {
      if (s.id === "DEPOT") return;
      ctx.fillStyle = c; ctx.beginPath(); ctx.arc(sx(s.x), sy(s.y), 4, 0, 7); ctx.fill();
      ctx.strokeStyle = cssVar("--panel"); ctx.lineWidth = 1.5; ctx.stroke();
    });
  });
  // depot
  const dx = sx(r.depot.x), dy = sy(r.depot.y);
  ctx.fillStyle = cssVar("--ink");
  ctx.beginPath(); ctx.roundRect(dx - 6, dy - 6, 12, 12, 3); ctx.fill();
  ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.stroke();

  // stats — labels follow the selected mode so the three numbers share a frame
  const optimized = STATE.routeMode === "optimized";
  const km = optimized ? r.optimized_km : r.baseline_km;
  $("#route-km").textContent = num(km) + " km";
  $("#route-km-l").textContent = optimized ? "km (optimised)" : "km (current practice)";
  $("#route-saved").textContent = num(r.km_saved) + " km";
  $("#route-saved-l").textContent = optimized ? "km saved vs current practice" : "km saved if optimised";
  $("#route-veh").textContent = routes.length;

  // hover
  const allStops = [];
  routes.forEach((rt, i) => rt.stops.forEach((s) => { if (s.id !== "DEPOT") allStops.push({ ...s, veh: i, x2: sx(s.x), y2: sy(s.y) }); }));
  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let best = null, bd = 1e9;
    allStops.forEach((s) => { const d = Math.hypot(s.x2 - mx, s.y2 - my); if (d < bd) { bd = d; best = s; } });
    if (best && bd < 14) showTip(e.clientX, e.clientY, `<b>${best.name || best.id}</b><br>Vehicle ${best.veh + 1}`);
    else hideTip();
  };
  canvas.onmouseleave = hideTip;
}

/* ------------------------------------------------------------ assortment */
function renderAssort() {
  const a = STATE.assort;
  if (!a) return;
  $("#assort-count").textContent = a.milp.count;
  $("#assort-margin").textContent = eur(a.milp.margin);
  $("#assort-uplift").textContent = "+" + eurFull(a.uplift_vs_greedy);
  $("#assort-pill").textContent = "MILP · " + eur(a.milp.margin);
  $("#budgetLabel").textContent = eurFull(a.budget) + " / " + eurFull(a.full_capital);

  // before/after bar: full-range margin potential vs MILP vs greedy
  const canvas = $("#assortChart");
  const { ctx, w, h } = fitCanvas(canvas, 150);
  const rows = [
    { label: "MILP (optimal)", val: a.milp.margin, color: PALETTE.blue },
    { label: "Greedy baseline", val: a.greedy.margin, color: PALETTE.amber },
  ];
  const max = Math.max(rows[0].val, rows[1].val) * 1.15;
  const padL = 110, padR = 70, barH = 26, gap = 22, top = 18;
  const muted = cssVar("--muted");
  ctx.font = "12px -apple-system, Segoe UI, sans-serif"; ctx.textBaseline = "middle";
  rows.forEach((r, i) => {
    const y = top + i * (barH + gap);
    ctx.fillStyle = muted; ctx.textAlign = "left"; ctx.fillText(r.label, 0, y + barH / 2);
    ctx.fillStyle = cssVar("--panel-2"); ctx.beginPath(); ctx.roundRect(padL, y, w - padL - padR, barH, 6); ctx.fill();
    const bw = ((r.val / max) * (w - padL - padR));
    ctx.fillStyle = r.color; ctx.beginPath(); ctx.roundRect(padL, y, bw, barH, 6); ctx.fill();
    ctx.fillStyle = cssVar("--text"); ctx.textAlign = "left"; ctx.font = "bold 12px -apple-system, Segoe UI, sans-serif";
    ctx.fillText(eur(r.val), padL + bw + 8, y + barH / 2);
    ctx.font = "12px -apple-system, Segoe UI, sans-serif";
  });
}

let budgetTimer = null;
async function onBudget(pctVal) {
  const full = STATE.assort ? STATE.assort.full_capital : null;
  if (!full) return;
  const budget = (pctVal / 100) * full;
  $("#budgetLabel").textContent = eurFull(budget) + " / " + eurFull(full);
  clearTimeout(budgetTimer);
  budgetTimer = setTimeout(async () => {
    refreshPlan(); // headline uplift + action cards follow the same scenario
    try {
      STATE.assort = await getJSON("/api/optimize/assortment?budget=" + Math.round(budget));
      renderAssort();
    } catch (err) {
      $("#assort-pill").textContent = "load failed — move the slider to retry";
    }
  }, 130);
}

/* ---------------------------------------------------- plan (uplift + actions) */
function setPlanLoading(on) {
  $("#kpi-uplift").classList.toggle("loading", on);
  if (on) $("#actions-total").textContent = "recomputing…";
}

/* Re-run /api/prescribe for the scenario currently selected in the UI, so the
   headline uplift KPI, action cards, board summary and export links never show
   a different budget/guardrail than the assortment card. */
async function refreshPlan() {
  const full = STATE.assort ? STATE.assort.full_capital : null;
  const pctVal = +$("#budgetSlider").value;
  const budget = full ? Math.round((pctVal / 100) * full) : null;
  const seq = ++STATE.planSeq;
  setPlanLoading(true);
  try {
    let url = "/api/prescribe?max_change=" + STATE.maxChange;
    if (budget != null) url += "&budget=" + budget;
    const plan = await getJSON(url);
    if (seq !== STATE.planSeq) return; // a newer request superseded this one
    STATE.plan = plan;
    renderKPIs();
    renderActions();
    renderSummary();
    renderCompare();
    updateExportLinks();
    refreshWhy(); // the explanation follows the same scenario as the plan
  } catch (err) {
    if (seq === STATE.planSeq) $("#actions-total").textContent = "recompute failed — adjust a control to retry";
  } finally {
    if (seq === STATE.planSeq) $("#kpi-uplift").classList.remove("loading");
  }
}

/* ------------------------------------------------------------ board summary */
function renderSummary() {
  const p = STATE.plan;
  if (!p) return;
  const lv = p.levers || {};
  const top = p.cards && p.cards[0];
  const budgetTxt = p.budget != null && p.full_capital
    ? eur(p.budget) + " budget (" + Math.round((p.budget / p.full_capital) * 100) + "% of capital)"
    : "the current budget";
  const guardTxt = "±" + Math.round((p.max_change != null ? p.max_change : STATE.maxChange) * 100) + "% price guardrail";
  const band = p.next_month_band;
  const parts = [
    "Plan at " + budgetTxt + ", " + guardTxt + ": expected uplift " + eur(p.expected_uplift_eur) + "/yr" +
      (lv.pricing != null ? " (pricing " + eur(lv.pricing) + " · routing " + eur(lv.routing) + " · assortment " + eur(lv.assortment) + ")" : "") + ".",
    p.next_month_revenue != null
      ? "Next-month forecast " + eur(p.next_month_revenue) + (band ? " (band " + eur(band[0]) + "–" + eur(band[1]) + ")" : "") + "."
      : "",
    top ? "Top action: " + top.title + "." : "",
  ];
  $("#boardSummaryText").textContent = parts.filter(Boolean).join(" ");
}

function initSummaryCopy() {
  const btn = $("#copySummary");
  btn.addEventListener("click", async () => {
    const text = $("#boardSummaryText").textContent;
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch (err) {
      // clipboard API unavailable (http / permissions): fall back to execCommand
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { ok = document.execCommand("copy"); } catch (e2) { ok = false; }
      ta.remove();
    }
    btn.textContent = ok ? "Copied" : "Copy failed";
    setTimeout(() => { btn.textContent = "Copy"; }, 1600);
  });
}

/* ------------------------------------------------------ scenario compare */
/* Pin the plan currently on screen, then move the budget slider or guardrail:
   both scenarios stay visible side by side with a modelled delta. Routing is
   solved once at startup and is identical in both, so the delta is driven by
   pricing and assortment only — and is labelled that way. */
function planSnapshot(p) {
  return {
    budget: p.budget,
    full_capital: p.full_capital,
    max_change: p.max_change,
    expected_uplift_eur: p.expected_uplift_eur,
    levers: { pricing: p.levers.pricing, assortment: p.levers.assortment, routing: p.levers.routing },
  };
}

function scenarioDesc(p) {
  const share = p.full_capital ? Math.round((p.budget / p.full_capital) * 100) : null;
  return eur(p.budget) + " budget" + (share != null ? " (" + share + "%)" : "") + " · ±" + Math.round(p.max_change * 100) + "% prices";
}

function leverDesc(lv) {
  return "pricing " + eur(lv.pricing) + " · routing " + eur(lv.routing) + " · assortment " + eur(lv.assortment);
}

function renderCompare() {
  const bar = $("#compareBar");
  const pin = STATE.pinned, cur = STATE.plan;
  if (!pin || !cur) { bar.hidden = true; return; }
  $("#cmpPinnedScenario").textContent = scenarioDesc(pin);
  $("#cmpPinnedUplift").textContent = eur(pin.expected_uplift_eur) + " / yr";
  $("#cmpPinnedLevers").textContent = leverDesc(pin.levers);
  $("#cmpCurrentScenario").textContent = scenarioDesc(cur);
  $("#cmpCurrentUplift").textContent = eur(cur.expected_uplift_eur) + " / yr";
  $("#cmpCurrentLevers").textContent = leverDesc(cur.levers);
  const d = cur.expected_uplift_eur - pin.expected_uplift_eur;
  const dEl = $("#cmpDelta");
  dEl.textContent = (d >= 0 ? "+" : "−") + eur(Math.abs(d)) + " / yr";
  dEl.classList.toggle("up", d >= 0);
  dEl.classList.toggle("down", d < 0);
  const dp = cur.levers.pricing - pin.levers.pricing;
  const da = cur.levers.assortment - pin.levers.assortment;
  const sgn = (v) => (v >= 0 ? "+" : "−") + eur(Math.abs(v));
  $("#cmpDeltaLevers").textContent = "pricing " + sgn(dp) + " · assortment " + sgn(da);
  bar.hidden = false;
}

function initCompare() {
  $("#pinScenario").addEventListener("click", () => {
    if (!STATE.plan) return;
    STATE.pinned = planSnapshot(STATE.plan);
    renderCompare();
  });
  $("#unpinScenario").addEventListener("click", () => {
    STATE.pinned = null;
    renderCompare();
  });
}

/* ------------------------------------------ scenario A/B compare (server) */
/* Two *named* budget/guardrail scenarios compared in one server call
   (POST /api/scenario/compare). Deterministic; routing is solved once and
   shared, so the uplift delta is pricing + assortment. The same A/B table is
   written to the workbook's Scenarios sheet, so screen and export agree. */
function scGuardValue(segId) {
  const btn = document.querySelector("#" + segId + " button.active");
  return btn ? parseFloat(btn.dataset.mc) : 0.15;
}
function scBudgetEur(sliderId) {
  const full = STATE.assort ? STATE.assort.full_capital : null;
  if (!full) return null;
  return Math.round((+$(sliderId).value / 100) * full);
}
function scScenarioDesc(s) {
  return eur(s.budget) + " budget (" + Math.round(s.budget_pct_of_full * 100) + "%) · ±" +
    Math.round(s.max_change * 100) + "% prices";
}
function scLeverDesc(s) {
  return "pricing " + eur(s.kpis.pricing_uplift_eur) + " · routing " + eur(s.kpis.routing_uplift_eur) +
    " · assortment " + eur(s.kpis.assortment_uplift_eur);
}
function renderScenarioResult(res) {
  const a = res.scenario_a, b = res.scenario_b, d = res.deltas;
  $("#scResAScenario").textContent = a.name + " — " + scScenarioDesc(a);
  $("#scResAUplift").textContent = eur(a.kpis.expected_uplift_eur) + " / yr";
  $("#scResALevers").textContent = scLeverDesc(a);
  $("#scResBScenario").textContent = b.name + " — " + scScenarioDesc(b);
  $("#scResBUplift").textContent = eur(b.kpis.expected_uplift_eur) + " / yr";
  $("#scResBLevers").textContent = scLeverDesc(b);
  const du = d.expected_uplift_eur.abs;
  const dEl = $("#scResDelta");
  dEl.textContent = (du >= 0 ? "+" : "−") + eur(Math.abs(du)) + " / yr";
  dEl.classList.toggle("up", du >= 0);
  dEl.classList.toggle("down", du < 0);
  const sgn = (v) => (v >= 0 ? "+" : "−") + eur(Math.abs(v));
  $("#scResDeltaLevers").textContent = "pricing " + sgn(d.pricing_uplift_eur.abs) +
    " · assortment " + sgn(d.assortment_uplift_eur.abs);
  $("#scResNote").textContent = res.routing_identical
    ? "routing identical in both scenarios"
    : "routing differs between scenarios";
  $("#scenarioProvenance").textContent = res.provenance;
  $("#scenarioResult").hidden = false;
}
async function runScenarioCompare() {
  const btn = $("#runScenarioCompare");
  const ba = scBudgetEur("#scABudget"), bb = scBudgetEur("#scBBudget");
  if (ba == null || bb == null) return;
  const body = {
    scenario_a: { name: "A", budget: ba, max_change: scGuardValue("scAGuard") },
    scenario_b: { name: "B", budget: bb, max_change: scGuardValue("scBGuard") },
  };
  btn.disabled = true;
  btn.textContent = "Comparing…";
  try {
    const r = await fetch("/api/scenario/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const res = await r.json();
    if (!r.ok) throw new Error(res.error || "compare failed");
    renderScenarioResult(res);
  } catch (err) {
    $("#scenarioProvenance").textContent = "Compare failed: " + (err && err.message ? err.message : "network error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Compare A vs B";
  }
}
function initScenarioCompare() {
  $("#scABudget").addEventListener("input", () => { $("#scABudgetLabel").textContent = $("#scABudget").value + "%"; });
  $("#scBBudget").addEventListener("input", () => { $("#scBBudgetLabel").textContent = $("#scBBudget").value + "%"; });
  ["scAGuard", "scBGuard"].forEach((id) => {
    $$("#" + id + " button").forEach((b) =>
      b.addEventListener("click", () => {
        $$("#" + id + " button").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
      })
    );
  });
  $("#runScenarioCompare").addEventListener("click", runScenarioCompare);
}

/* ------------------------------------------------ import your data (Excel) */
/* POST the filled template; on success the server has swapped every engine
   onto the imported data, so a full reload is the honest refresh (banner,
   KPIs, charts and export links all re-render from the new state). */
function showImportError(msg, errors) {
  $("#importErrorMsg").textContent = msg;
  $("#importErrorList").innerHTML = (errors || [])
    .slice(0, 12)
    .map((e) => `<li>${esc(e)}</li>`)
    .join("") + ((errors || []).length > 12 ? `<li>… and ${errors.length - 12} more</li>` : "");
  $("#importError").hidden = false;
}

function initImport() {
  const btn = $("#importBtn"), input = $("#importFile");
  if (btn && input) {
    btn.addEventListener("click", () => input.click());
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      btn.disabled = true;
      btn.textContent = "Importing…";
      const form = new FormData();
      form.append("workbook", file);
      try {
        const r = await fetch("/api/import", { method: "POST", body: form });
        const body = await r.json();
        if (!r.ok) {
          showImportError(body.error || "Import failed.", body.errors);
          return;
        }
        location.reload();
      } catch (err) {
        showImportError("Import failed: " + (err && err.message ? err.message : "network error"), []);
      } finally {
        btn.disabled = false;
        btn.textContent = "Import Excel";
        input.value = ""; // allow re-selecting the same file after a fix
      }
    });
  }
  const dismiss = $("#importErrorClose");
  if (dismiss) dismiss.addEventListener("click", () => { $("#importError").hidden = true; });
  const reset = $("#resetData");
  if (reset) {
    reset.addEventListener("click", async () => {
      reset.disabled = true;
      try {
        await fetch("/api/reset", { method: "POST" });
        location.reload();
      } catch (err) {
        reset.disabled = false;
      }
    });
  }
}

/* ------------------------------------------------ why this plan? panel */
/* Renders the /api/explain structure — the same one the PDF's Why page and
   the workbook's Explanation sheet are generated from. Fetched lazily on
   first open, re-fetched when the scenario (budget/guardrail) changes. */
const WHY = { open: false, stale: true, seq: 0 };

function whyScenarioQuery() {
  const p = STATE.plan;
  let q = "?max_change=" + (p && p.max_change != null ? p.max_change : STATE.maxChange);
  if (p && p.budget != null) q += "&budget=" + Math.round(p.budget);
  return q;
}

function renderWhy(ex) {
  const h = ex.headline;
  const sens = ex.sensitivity;
  const lo = sens.budget_minus_10pct, hi = sens.budget_plus_10pct;
  const sgn = (v) => (v >= 0 ? "+" : "−") + eur(Math.abs(v));
  let html = `<div class="why-headline">Expected uplift ${eur(h.expected_uplift_eur)}/yr
    (${pct(h.expected_uplift_pct)} of annual gross margin) versus the do-nothing baseline —
    ${esc(h.baseline)}.</div>`;

  html += `<div class="why-sec-title">Which constraints bind</div>`;
  ex.binding_constraints.forEach((bc) => {
    html += `<div class="why-constraint">
      <span class="why-badge ${bc.binding ? "binding" : "slack"}">${bc.binding ? "binding" : "slack"}</span>
      <span>${esc(bc.detail)}</span></div>`;
  });

  html += `<div class="why-sec-title">What changes vs doing nothing</div>`;
  ex.levers.forEach((lv) => {
    html += `<div class="why-lever"><div class="why-lever-head">
      <span>${esc(lv.lever[0].toUpperCase() + lv.lever.slice(1))}</span>
      <span class="amt">${eur(lv.total_eur)}/yr</span></div>`;
    lv.moves.forEach((mv) => {
      html += `<div class="why-move"><span>${mv.sku_id ? "<b>" + esc(mv.sku_id) + "</b> — " : ""}${esc(mv.action)}</span>
        <span class="amt">${sgn(mv.contribution_eur)}</span></div>`;
    });
    html += `<div class="why-lever-note">${esc(lv.note)}</div></div>`;
  });

  html += `<div class="why-sec-title">How sensitive is the number</div>
    <div class="why-sens">At a 10% tighter budget (${eur(lo.budget)}) the uplift is
    ${eur(lo.expected_uplift_eur)} (${sgn(lo.delta_eur)}); at a 10% looser budget
    (${eur(hi.budget)}) it is ${eur(hi.expected_uplift_eur)} (${sgn(hi.delta_eur)}).
    ${esc(sens.note)}.</div>`;

  html += `<div class="why-sec-title">Caveats</div><ul class="why-caveats">` +
    ex.caveats.map((c) => `<li>${esc(c)}</li>`).join("") + `</ul>`;
  $("#whyContent").innerHTML = html;
}

async function refreshWhy() {
  if (!WHY.open) { WHY.stale = true; return; }
  const seq = ++WHY.seq;
  $("#whyLoading").hidden = false;
  try {
    const ex = await getJSON("/api/explain" + whyScenarioQuery());
    if (seq !== WHY.seq) return;
    WHY.stale = false;
    renderWhy(ex);
  } catch (err) {
    if (seq === WHY.seq) $("#whyContent").innerHTML = `<div class="why-loading">Could not load the explanation (${esc(err && err.message ? err.message : "network error")}).</div>`;
  } finally {
    if (seq === WHY.seq) $("#whyLoading").hidden = true;
  }
}

function initWhy() {
  const toggle = $("#whyToggle");
  toggle.addEventListener("click", () => {
    WHY.open = !WHY.open;
    $("#whyBody").hidden = !WHY.open;
    toggle.textContent = WHY.open ? "Hide" : "Show";
    toggle.setAttribute("aria-expanded", String(WHY.open));
    if (WHY.open && WHY.stale) refreshWhy();
  });
}

/* Exports carry the on-screen scenario so the deck matches the dashboard. */
function updateExportLinks() {
  const p = STATE.plan;
  if (!p || p.budget == null) return;
  const q = "?budget=" + Math.round(p.budget) + "&max_change=" + (p.max_change != null ? p.max_change : STATE.maxChange);
  $("#exportPdf").href = "/api/export/pdf" + q;
  $("#exportExcel").href = "/api/export/excel" + q;
}

/* ------------------------------------------------------------ actions */
function renderActions() {
  const p = STATE.plan;
  $("#actions-total").textContent = "Σ " + eur(p.expected_uplift_eur) + " / yr";
  const colors = { Pricing: PALETTE.blue, Assortment: PALETTE.green, Logistics: PALETTE.amber, Forecast: PALETTE.pink };
  $("#actions").innerHTML = p.cards
    .map(
      (c) => `<div class="action">
        <div class="rail" style="background:${colors[c.lever] || PALETTE.blue}"></div>
        <div>
          <div class="lever" style="color:${colors[c.lever] || PALETTE.blue}">${c.lever}</div>
          <div class="a-title">${c.title}</div>
          <div class="a-detail">${c.detail}</div>
        </div>
        <div class="a-impact"><div class="n">${c.impact_eur > 0 ? eur(c.impact_eur) : "—"}</div><div class="c">${c.confidence} confidence</div></div>
      </div>`
    )
    .join("");
}

/* ------------------------------------------------------------ render all */
function renderAll() {
  renderKPIs(); renderForecast(); renderBreakdown(); renderMargin();
  renderHeat(); renderRFM(); renderRoutes(); renderAssort(); renderActions();
  renderCrosssell(); renderKpiDrill();
}

/* ------------------------------------------------------------ focused views */
/* /routes and /assortment deep links land on the relevant card, highlighted. */
function applyFocusView() {
  const view = (window.__INITIAL__ && window.__INITIAL__.view) || "overview";
  const focusMap = { routes: { sec: "sec-routes", title: "Delivery routing" }, assortment: { sec: "sec-assortment", title: "Assortment optimiser" } };
  const f = focusMap[view];
  if (!f) return;
  const el = document.getElementById(f.sec);
  if (!el) return;
  document.title = f.title + " — Distributor Intelligence Platform";
  $$(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.target === f.sec));
  el.classList.add("focus-card");
  requestAnimationFrame(() => el.scrollIntoView({ behavior: "auto", block: "start" }));
}

/* ------------------------------------------------------------ boot */
async function loadData() {
  // no budget param: the server answers with its default (40% of full capital),
  // so the first render can never drift from the server-side definition.
  const [forecast, margin, abc, rfm, revenue, routes, assort, crosssell, kpiDrilldown] = await Promise.all([
    getJSON("/api/forecast"),
    getJSON("/api/margin-bridge"),
    getJSON("/api/abc-xyz"),
    getJSON("/api/rfm"),
    getJSON("/api/revenue"),
    getJSON("/api/optimize/routes"),
    getJSON("/api/optimize/assortment"),
    getJSON("/api/crosssell?top=50"),
    getJSON("/api/kpis/drilldown"),
  ]);
  STATE.forecast = forecast; STATE.margin = margin; STATE.abc = abc;
  STATE.rfm = rfm; STATE.revenue = revenue; STATE.routes = routes; STATE.assort = assort;
  STATE.crosssell = crosssell; STATE.kpiDrilldown = kpiDrilldown;
  // sync slider to the server-reported budget share
  $("#budgetSlider").value = Math.round((assort.budget / assort.full_capital) * 100);
  $("#loadError").hidden = true;
  renderAll();
}

function showLoadError(err) {
  $("#loadErrorMsg").textContent = "Could not load dashboard data (" + (err && err.message ? err.message : "network error") + "). The charts below may be empty.";
  $("#loadError").hidden = false;
}

async function boot() {
  initTheme();
  initNav();
  initSummaryCopy();
  initCompare();
  initScenarioCompare();
  initDrills();
  initKpiDrill();
  initCrosssell();
  initImport();
  initWhy();
  STATE.kpis = window.__INITIAL__.kpis;
  STATE.plan = window.__INITIAL__.plan;
  STATE.maxChange = STATE.plan && STATE.plan.max_change != null ? STATE.plan.max_change : 0.15;
  renderKPIs();
  renderActions();
  renderSummary();
  updateExportLinks();
  applyFocusView();

  // segment toggles
  $$("#breakdownSeg button").forEach((b) =>
    b.addEventListener("click", () => {
      $$("#breakdownSeg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      STATE.breakdownDim = b.dataset.dim;
      renderBreakdown();
    })
  );
  $$("#routeSeg button").forEach((b) =>
    b.addEventListener("click", () => {
      $$("#routeSeg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      STATE.routeMode = b.dataset.mode;
      renderRoutes();
    })
  );
  $$("#guardSeg button").forEach((b) =>
    b.addEventListener("click", () => {
      $$("#guardSeg button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      STATE.maxChange = parseFloat(b.dataset.mc);
      refreshPlan();
    })
  );
  $("#budgetSlider").addEventListener("input", (e) => onBudget(+e.target.value));
  $("#retryLoad").addEventListener("click", async () => {
    $("#loadError").hidden = true;
    try { await loadData(); } catch (err) { showLoadError(err); }
  });

  try {
    await loadData();
  } catch (err) {
    showLoadError(err);
  }

  window.addEventListener("resize", () => { clearTimeout(window.__rz); window.__rz = setTimeout(renderAll, 150); });
}

document.addEventListener("DOMContentLoaded", boot);
