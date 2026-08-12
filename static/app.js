/* Distributor Intelligence Platform — front-end controller.
   All charts are hand-drawn on <canvas>; no charting library. Data comes from
   the JSON API. Chart colors are read from the CSS design tokens at draw time,
   so light and dark are both first-class (prefers-color-scheme + the manual
   toggle) and every redraw follows the active theme.
   Author: Dimitres Kisimov, 2026. */
"use strict";

/* ------------------------------------------------------------------ helpers */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const REDUCED_MOTION = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
/* validated categorical slots 1..8 (see style.css tokens); never cycled —
   anything past slot 8 falls to the de-emphasis gray */
function viz(n) { return cssVar("--viz-" + n); }
function slotColor(i) { return i < 8 ? viz(i + 1) : cssVar("--mark-muted"); }

function eur(n) {
  const a = Math.abs(n);
  if (a >= 1e6) return "€" + (n / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return "€" + (n / 1e3).toFixed(0) + "k";
  return "€" + (Math.round(n) || 0).toLocaleString(); // `|| 0` normalises -0
}
function eurFull(n) { return "€" + (Math.round(n) || 0).toLocaleString("en-US"); }
function pct(n, d = 1) { return (n * 100).toFixed(d) + "%"; }
function num(n) { return Math.round(n).toLocaleString("en-US"); }
function esc(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
/* KPI typography: same text, with the currency symbol and magnitude/unit
   suffix set small — the digits carry the tile. */
function kpiHTML(s) {
  const m = /^([+\-−]?)(€?)([\d.,]+)(.*)$/.exec(String(s));
  if (!m) return esc(s);
  const sign = m[1], cur = m[2], digits = m[3], rest = m[4].trim();
  return (sign ? esc(sign) : "")
    + (cur ? '<span class="u">' + esc(cur) + "</span>" : "")
    + esc(digits)
    + (rest ? '<span class="u">' + esc(rest) + "</span>" : "");
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
const CHART_FONT = '11px system-ui, "Segoe UI", sans-serif';
const CHART_FONT_BOLD = 'bold 11px system-ui, "Segoe UI", sans-serif';

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
function systemPrefersDark() {
  return !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
function isDark() {
  const t = document.documentElement.getAttribute("data-theme");
  return t ? t === "dark" : systemPrefersDark();
}
function initTheme() {
  // The head snippet already applied any stored preference before first paint;
  // with no stored choice the page follows prefers-color-scheme.
  $("#themeToggle").addEventListener("click", () => {
    const next = isDark() ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("dip-theme", next); } catch (e) { /* private mode */ }
    renderAll(); // redraw canvases with the new tokens
  });
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (!document.documentElement.getAttribute("data-theme")) renderAll();
    });
  }
}

/* ------------------------------------------------------------------ nav */
function initNav() {
  $$(".nav-item").forEach((item) => {
    item.addEventListener("click", () => {
      const t = document.getElementById(item.dataset.target);
      if (t) t.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" });
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
const STATE = { kpis: null, plan: null, forecast: null, margin: null, abc: null, rfm: null, revenue: null, routes: null, assort: null, crosssell: null, crossProduct: "", crossRecs: null, kpiDrilldown: null, kpiDrillOpen: null, inventory: null, reliability: null, breakdownDim: "region", routeMode: "optimized", maxChange: 0.15, planSeq: 0, drillCell: null, drillSegment: null, pinned: null };

/* ------------------------------------------------------------------ KPIs */
function renderKPIs() {
  const k = STATE.kpis, p = STATE.plan;
  $("#kpi-revenue").innerHTML = kpiHTML(eur(k.revenue));
  $("#kpi-margin").innerHTML = kpiHTML(pct(k.gross_margin_pct));
  $("#kpi-margin-sub").textContent = eur(k.gross_margin) + " gross margin";
  $("#kpi-yoy").innerHTML = kpiHTML((k.yoy >= 0 ? "+" : "") + pct(k.yoy));
  $("#kpi-yoy-sub").className = "delta " + (k.yoy >= 0 ? "up" : "down");
  $("#kpi-yoy-sub").textContent = "last 12 vs prior 12";
  $("#kpi-uplift").innerHTML = kpiHTML(eur(p.expected_uplift_eur));
  $("#kpi-uplift-sub").textContent = pct(p.expected_uplift_pct) + " of annual gross margin";
  $("#kpi-otif").innerHTML = kpiHTML(pct(k.otif));
  renderRevenueSpark();
}

/* 24-month revenue trend inside the Revenue tile: the series in the
   de-emphasis gray, the current month as an accent end-dot. */
function renderRevenueSpark() {
  const canvas = $("#kpiRevenueSpark");
  const series = STATE.kpis && STATE.kpis.revenue_series;
  if (!canvas || !series || series.length < 2) return;
  const { ctx, w, h } = fitCanvas(canvas, 26);
  const min = Math.min(...series), max = Math.max(...series);
  const X = (i) => 2 + (i / (series.length - 1)) * (w - 8);
  const Y = (v) => 3 + (1 - (v - min) / (max - min || 1)) * (h - 8);
  ctx.strokeStyle = cssVar("--mark-muted");
  ctx.lineWidth = 1.5; ctx.lineJoin = "round";
  ctx.beginPath();
  series.forEach((v, i) => (i === 0 ? ctx.moveTo(X(i), Y(v)) : ctx.lineTo(X(i), Y(v))));
  ctx.stroke();
  const lx = X(series.length - 1), ly = Y(series[series.length - 1]);
  ctx.fillStyle = viz(1);
  ctx.beginPath(); ctx.arc(lx, ly, 2.5, 0, 7); ctx.fill();
  ctx.strokeStyle = cssVar("--panel"); ctx.lineWidth = 1.5; ctx.stroke();
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
  const grid = cssVar("--grid"), muted = cssVar("--muted");
  const cActual = viz(1), cForecast = viz(2);

  // gridlines + y labels (hairline, recessive)
  ctx.font = CHART_FONT;
  ctx.textBaseline = "middle";
  niceTicks(ymin, ymax, 5).forEach((t) => {
    if (t < ymin) return;
    ctx.strokeStyle = grid; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(padL, Y(t)); ctx.lineTo(w - padR, Y(t)); ctx.stroke();
    ctx.fillStyle = muted; ctx.textAlign = "right";
    ctx.fillText(eur(t), padL - 8, Y(t));
  });

  // forecast band — a wash of the forecast hue, never a saturated block
  ctx.beginPath();
  for (let i = 0; i < fc.length; i++) { const x = X(hist.length + i); const y = Y(up[i]); i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
  for (let i = fc.length - 1; i >= 0; i--) ctx.lineTo(X(hist.length + i), Y(lo[i]));
  ctx.closePath();
  ctx.globalAlpha = 0.14; ctx.fillStyle = cForecast; ctx.fill(); ctx.globalAlpha = 1;

  // actual line
  ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
  ctx.strokeStyle = cActual; ctx.beginPath();
  hist.forEach((v, i) => (i === 0 ? ctx.moveTo(X(i), Y(v)) : ctx.lineTo(X(i), Y(v))));
  ctx.stroke();

  // connector + forecast line (dashed = projection)
  ctx.strokeStyle = cForecast; ctx.setLineDash([6, 5]); ctx.beginPath();
  ctx.moveTo(X(hist.length - 1), Y(hist[hist.length - 1]));
  fc.forEach((v, i) => ctx.lineTo(X(hist.length + i), Y(v)));
  ctx.stroke(); ctx.setLineDash([]);

  // dots with a surface ring so they stay legible where they cross the line
  const ring = cssVar("--panel");
  const pts = [];
  hist.forEach((v, i) => { pts.push({ x: X(i), y: Y(v), v, label: f.history_months[i], type: "actual" }); });
  fc.forEach((v, i) => { pts.push({ x: X(hist.length + i), y: Y(v), v, label: f.forecast_months[i], type: "forecast", lo: lo[i], up: up[i] }); });
  pts.forEach((p) => {
    ctx.fillStyle = p.type === "actual" ? cActual : cForecast;
    ctx.beginPath(); ctx.arc(p.x, p.y, 2.6, 0, 7); ctx.fill();
    ctx.strokeStyle = ring; ctx.lineWidth = 1.5; ctx.stroke();
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
      let html = `<b>${esc(best.label)}</b><br>${best.type === "actual" ? "Actual" : "Forecast"}: ${eurFull(best.v)}`;
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
  // one nominal series -> one hue; the row label carries identity
  $("#breakdownBars").innerHTML = rows
    .map(
      (r) => `
      <div class="bar-row">
        <span title="${esc(r.label)}">${esc(r.label)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(r.revenue / max) * 100}%"></div></div>
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
  const bw = Math.min(24, (w - padL - padR) / items.length * 0.6);
  const gap = (w - padL - padR) / items.length;
  const Y = (v) => padT + (1 - (v - ymin) / (ymax - ymin)) * (h - padT - padB);
  const grid = cssVar("--grid"), muted = cssVar("--muted"), ink = cssVar("--ink");
  const cUp = cssVar("--up"), cDown = cssVar("--down");
  const tUp = cssVar("--up-text"), tDown = cssVar("--down-text");

  ctx.font = CHART_FONT; ctx.textBaseline = "middle";
  niceTicks(ymin, ymax, 4).forEach((t) => {
    ctx.strokeStyle = grid; ctx.beginPath(); ctx.moveTo(padL, Y(t)); ctx.lineTo(w - padR, Y(t)); ctx.stroke();
    ctx.fillStyle = muted; ctx.textAlign = "right"; ctx.fillText(eur(t), padL - 8, Y(t));
  });

  bars.forEach((b, i) => {
    const x = padL + i * gap + (gap - bw) / 2;
    const yTop = Y(b.top), yBase = Y(b.base);
    let color = ink, labelColor = muted;
    if (b.type === "delta") {
      color = b.value >= 0 ? cUp : cDown;
      labelColor = b.value >= 0 ? tUp : tDown;
    }
    ctx.fillStyle = color;
    ctx.beginPath();
    const rr = 3, ht = Math.max(2, yBase - yTop);
    // rounded data-end, square at the running baseline
    const radii = b.type === "delta" && b.value < 0 ? [0, 0, rr, rr] : [rr, rr, 0, 0];
    ctx.roundRect(x, yTop, bw, ht, radii); ctx.fill();
    // hairline connector to the next bar
    if (i < bars.length - 1) {
      const yc = Y(b.type === "total" ? b.value : (b.value >= 0 ? b.top : b.base));
      ctx.strokeStyle = cssVar("--line-strong"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x + bw, yc); ctx.lineTo(x + gap, yc); ctx.stroke();
    }
    // label + value (values wear text tokens, not the mark color)
    ctx.fillStyle = muted; ctx.textAlign = "center"; ctx.textBaseline = "top";
    ctx.fillText(b.label, x + bw / 2, h - padB + 8);
    ctx.fillStyle = b.type === "total" ? cssVar("--text") : labelColor;
    ctx.textBaseline = "bottom"; ctx.font = CHART_FONT_BOLD;
    const vtxt = (b.type === "delta" && b.value >= 0 ? "+" : "") + eur(b.value);
    ctx.fillText(vtxt, x + bw / 2, yTop - 4);
    ctx.font = CHART_FONT; ctx.textBaseline = "middle";
  });
}

/* ------------------------------------------------------------ ABC-XYZ heatmap */
/* Sequential = one hue, light->dark by revenue; the anchor flips in dark mode
   so "near zero" always recedes toward the surface. Steps come from the
   documented blue ramp; the in-cell label picks ink or paper by contrast. */
const SEQ_LIGHT = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#5598e7", "#256abf", "#184f95"];
const SEQ_DARK = ["#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf", "#5598e7", "#9ec5f4"];

function relLum(hex) {
  const c = [1, 3, 5].map((i) => {
    let v = parseInt(hex.slice(i, i + 2), 16) / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrastRatio(a, b) {
  const la = relLum(a), lb = relLum(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}
function cellText(bg) {
  const dark = "#17191d", light = "#f5f7fa";
  return contrastRatio(bg, dark) >= contrastRatio(bg, light) ? dark : light;
}

function renderHeat() {
  const az = STATE.abc;
  if (!az) return;
  const grid = az.grid;
  const maxRev = Math.max(...Object.values(grid).map((g) => g.revenue));
  const abc = ["A", "B", "C"], xyz = ["X", "Y", "Z"];
  const ramp = isDark() ? SEQ_DARK : SEQ_LIGHT;
  const el = $("#heat");
  let html = `<div class="corner"></div>` + xyz.map((x) => `<div class="h-col">${x}</div>`).join("");
  abc.forEach((a) => {
    html += `<div class="h-row">${a}</div>`;
    xyz.forEach((x) => {
      const cell = grid[a + x];
      const t = maxRev ? cell.revenue / maxRev : 0;
      const bg = ramp[Math.round(t * (ramp.length - 1))];
      const fg = cellText(bg);
      html += `<div class="cell" role="button" tabindex="0" data-cell="${a + x}" style="background:${bg};color:${fg}" data-tip="${a + x}: ${cell.count} SKUs · ${eurFull(cell.revenue)} — click for the SKU list">
        <span class="c-name">${a + x}</span>
        <span class="c-count">${cell.count}</span>
        <span class="c-rev">${eur(cell.revenue)}</span>
      </div>`;
    });
  });
  el.innerHTML = html;
  $$("#heat .cell").forEach((c) => {
    c.addEventListener("mousemove", (e) => showTip(e.clientX, e.clientY, esc(c.dataset.tip)));
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
          <td class="mono">${esc(s.sku_id)}</td>
          <td>${esc(s.name)}</td>
          <td>${esc(s.category)}</td>
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
          <td>${esc(c.name)} <span class="dim mono">${esc(c.customer_id)}</span></td>
          <td class="mono">${c.r}·${c.f}·${c.m}</td>
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
      <td><b class="mono">${esc(r.antecedent)}</b> <span class="dim">${esc(r.antecedent_name)}</span></td>
      <td><b class="mono">${esc(r.consequent)}</b> <span class="dim">${esc(r.consequent_name)}</span></td>
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
  // one nominal series -> one hue; the segment name carries identity
  $("#rfmBars").innerHTML = segs
    .map(
      (s) => `<div class="bar-row clickable" role="button" tabindex="0" data-segment="${esc(s.segment)}" title="${esc(s.segment)} — click for the customer list">
        <span>${esc(s.segment)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(s.monetary / max) * 100}%"></div></div>
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
  const { ctx, w, h } = fitCanvas(canvas, 280);
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
  const ring = cssVar("--panel");

  // routes — fixed categorical slots, one per vehicle, never cycled
  routes.forEach((rt, i) => {
    const c = slotColor(i);
    ctx.strokeStyle = c; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.beginPath();
    rt.stops.forEach((s, j) => { const x = sx(s.x), y = sy(s.y); j === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); });
    ctx.stroke();
    // customer dots with a surface ring
    rt.stops.forEach((s) => {
      if (s.id === "DEPOT") return;
      ctx.fillStyle = c; ctx.beginPath(); ctx.arc(sx(s.x), sy(s.y), 4, 0, 7); ctx.fill();
      ctx.strokeStyle = ring; ctx.lineWidth = 2; ctx.stroke();
    });
  });
  // depot
  const dx = sx(r.depot.x), dy = sy(r.depot.y);
  ctx.fillStyle = cssVar("--ink");
  ctx.beginPath(); ctx.roundRect(dx - 6, dy - 6, 12, 12, 3); ctx.fill();
  ctx.strokeStyle = ring; ctx.lineWidth = 2; ctx.stroke();

  // per-vehicle legend — identity never rides on color alone
  $("#routeLegend").innerHTML = routes
    .map((rt, i) => {
      const stops = rt.stops.filter((s) => s.id !== "DEPOT").length;
      return `<span class="lg"><span class="sw" style="background:${slotColor(i)}"></span>Vehicle ${i + 1} · ${stops} stops</span>`;
    })
    .join("");

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
    if (best && bd < 14) showTip(e.clientX, e.clientY, `<b>${esc(best.name || best.id)}</b><br>Vehicle ${best.veh + 1}`);
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

  // emphasis form: the optimiser is the point, the heuristic is context
  const canvas = $("#assortChart");
  const { ctx, w, h } = fitCanvas(canvas, 150);
  const rows = [
    { label: "MILP (optimal)", val: a.milp.margin, color: viz(1) },
    { label: "Greedy baseline", val: a.greedy.margin, color: cssVar("--mark-muted") },
  ];
  const max = Math.max(rows[0].val, rows[1].val) * 1.15;
  const padL = 110, padR = 70, barH = 22, gap = 24, top = 20;
  const muted = cssVar("--muted");
  ctx.font = CHART_FONT; ctx.textBaseline = "middle";
  rows.forEach((r, i) => {
    const y = top + i * (barH + gap);
    ctx.fillStyle = muted; ctx.textAlign = "left"; ctx.fillText(r.label, 0, y + barH / 2);
    ctx.fillStyle = cssVar("--panel-2"); ctx.beginPath(); ctx.roundRect(padL, y, w - padL - padR, barH, 3); ctx.fill();
    const bw = ((r.val / max) * (w - padL - padR));
    // rounded data-end, square at the baseline
    ctx.fillStyle = r.color; ctx.beginPath(); ctx.roundRect(padL, y, bw, barH, [0, 3, 3, 0]); ctx.fill();
    ctx.fillStyle = cssVar("--text"); ctx.textAlign = "left"; ctx.font = CHART_FONT_BOLD;
    ctx.fillText(eur(r.val), padL + bw + 8, y + barH / 2);
    ctx.font = CHART_FONT;
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
  // each lever keeps its fixed categorical slot (identity, never re-ranked)
  const leverSlot = { Pricing: 1, Assortment: 3, Logistics: 2, Forecast: 5 };
  $("#actions").innerHTML = p.cards
    .map(
      (c) => {
        const v = "var(--viz-" + (leverSlot[c.lever] || 1) + ")";
        return `<div class="action">
        <div class="rail" style="background:${v}"></div>
        <div>
          <div class="lever"><span class="ldot" style="background:${v}"></span>${esc(c.lever)}</div>
          <div class="a-title">${esc(c.title)}</div>
          <div class="a-detail">${esc(c.detail)}</div>
        </div>
        <div class="a-impact"><div class="n">${c.impact_eur > 0 ? eur(c.impact_eur) : "—"}</div><div class="c">${esc(c.confidence)} confidence</div></div>
      </div>`;
      }
    )
    .join("");
}

/* ------------------------------------------------------ ST-02 · inventory */
/* Replenishment policy from /api/inventory: portfolio totals as stat tiles,
   the ABC-XYZ service-cell roll-up as a table, and the engine's own note and
   caveats verbatim — the honesty labels ship with the payload. */
const CELL_ORDER = ["AX", "AY", "AZ", "BX", "BY", "BZ", "CX", "CY", "CZ"];

function renderInventory() {
  const inv = STATE.inventory;
  if (!inv) return;
  const t = inv.totals;
  $("#invPill").textContent = num(t.n_skus) + " SKUs · targets from the ABC-XYZ matrix";
  $("#invInfo").title = inv.note;
  $("#invTiles").innerHTML = [
    { l: "Working capital", v: eur(t.working_capital_eur), s: "average inventory value" },
    { l: "Safety stock", v: eur(t.safety_stock_eur), s: "cycle stock " + eur(t.cycle_stock_eur) },
    { l: "Inventory turns", v: t.inventory_turns.toFixed(2) + "×", s: "annual COGS ÷ working capital" },
    { l: "Days of cover", v: t.days_of_cover.toFixed(1) + " d", s: "portfolio average" },
    { l: "Cycle service", v: pct(t.demand_weighted_service_level), s: "fill rate " + pct(t.demand_weighted_fill_rate) + " · demand-weighted" },
    { l: "Annual policy cost", v: eur(t.annual_inventory_cost_eur), s: "holding + ordering" },
  ].map((x) => `<div class="mini"><div class="m-label">${esc(x.l)}</div><div class="m-value">${kpiHTML(x.v)}</div><div class="m-sub">${esc(x.s)}</div></div>`).join("");

  const byCell = {};
  inv.by_cell.forEach((c) => { byCell[c.cell] = c; });
  $("#invCellTable").innerHTML =
    `<tr><th title="A/B/C = revenue tier · X/Y/Z = demand stability">Cell</th><th class="num">SKUs</th>` +
    `<th class="num" title="Cycle-service-level target this cell is planned to">Target CSL</th>` +
    `<th class="num" title="Expected demand-weighted fill rate under the policy">Fill rate</th>` +
    `<th class="num">Safety stock</th><th class="num">Working capital</th></tr>` +
    CELL_ORDER.filter((c) => byCell[c]).map((cname) => {
      const c = byCell[cname];
      return `<tr>
        <td class="mono">${esc(c.cell)}</td>
        <td class="num">${c.count}</td>
        <td class="num">${c.target_service_level != null ? pct(c.target_service_level, 0) : "—"}</td>
        <td class="num">${c.avg_fill_rate != null ? pct(c.avg_fill_rate) : "—"}</td>
        <td class="num">${eurFull(c.safety_stock_eur)}</td>
        <td class="num">${eurFull(c.working_capital_eur)}</td>
      </tr>`;
    }).join("");

  $("#invFoot").textContent = inv.note;
  const caveats = (inv.caveats || []).concat(inv.provenance ? [inv.provenance] : []);
  if (caveats.length) {
    $("#invCaveatList").innerHTML = caveats.map((c) => `<li>${esc(c)}</li>`).join("");
    $("#invCaveats").hidden = false;
  }
}

/* ---------------------------------------------------- ST-03 · reliability */
/* Supplier scorecards from /api/reliability. The letter grade is the encoding
   (color reinforces it); on-time rides a meter next to its figure; the
   safety-stock delta is signed and typeset like every other money column. */
function renderReliability() {
  const rel = STATE.reliability;
  if (!rel) return;
  const pill = $("#relPill"), table = $("#relTable");
  if (!rel.available) {
    pill.textContent = "unavailable on imported data";
    $("#relTiles").innerHTML = "";
    table.innerHTML = `<tr><td class="dim">${esc(rel.note)}</td></tr>`;
    $("#relFoot").textContent = rel.provenance || "";
    return;
  }
  const t = rel.totals, p = rel.params;
  pill.textContent = t.n_suppliers + " suppliers · " + num(t.n_receipts) + " receipts";
  $("#relInfo").title = rel.note;
  const sgnEur = (v) => (v >= 0 ? "+" : "−") + eur(Math.abs(v));
  $("#relTiles").innerHTML = [
    { l: "On-time (weighted)", v: pct(t.on_time_rate), s: "grace " + p.tolerance_days + " d" },
    { l: "Δ safety stock", v: sgnEur(t.delta_eur), s: "measured vs quoted lead times", cls: t.delta_eur > 0 ? "delta-pos" : "delta-neg" },
    { l: "Delay effect", v: sgnEur(t.delay_effect_eur), s: "average lead time vs vendor master" },
    { l: "Variability effect", v: sgnEur(t.variability_effect_eur), s: "lead-time wobble" },
    { l: "Extra holding cost", v: sgnEur(t.extra_holding_cost_eur), s: "at " + pct(p.holding_rate, 0) + "/yr holding" },
  ].map((x) => `<div class="mini"><div class="m-label">${esc(x.l)}</div><div class="m-value${x.cls ? " " + x.cls : ""}">${kpiHTML(x.v)}</div><div class="m-sub">${esc(x.s)}</div></div>`).join("");

  const bands = p.grade_bands;
  const gradeTitle = `on-time ≥ ${pct(bands.A, 0)} = A · ≥ ${pct(bands.B, 0)} = B · ≥ ${pct(bands.C, 0)} = C · below = D`;
  table.innerHTML =
    `<tr><th>Supplier</th><th class="mid" title="${esc(gradeTitle)}">Grade</th>` +
    `<th class="num" title="Share of receipts within the quoted lead time + ${p.tolerance_days}-day grace">On-time</th>` +
    `<th class="num" title="Average quoted lead time → average measured lead time, in days">Quoted → measured</th>` +
    `<th class="num" title="Mean delay vs the vendor master, in days">Delay</th>` +
    `<th class="num" title="Coefficient of variation of the measured lead time">CV</th>` +
    `<th class="num" title="Safety-stock consequence of measured vs quoted lead times at the same service targets — positive = more capital required">Δ safety stock</th>` +
    `<th class="num">Receipts</th></tr>` +
    rel.suppliers.map((s) => `<tr>
      <td>${esc(s.name)} <span class="dim mono">${esc(s.supplier_id)}</span></td>
      <td class="mid"><span class="grade g${esc(s.grade)}" title="${esc(gradeTitle)}">${esc(s.grade)}</span></td>
      <td class="num"><span class="meter"><span class="mt"><span class="mf" style="width:${Math.round(s.on_time_rate * 100)}%"></span></span>${pct(s.on_time_rate, 0)}</span></td>
      <td class="num">${s.avg_quoted_days.toFixed(1)} → ${s.avg_actual_days.toFixed(1)} d</td>
      <td class="num">${(s.mean_delay_days >= 0 ? "+" : "−") + Math.abs(s.mean_delay_days).toFixed(1)} d</td>
      <td class="num">${s.lead_time_cv.toFixed(2)}</td>
      <td class="num ${s.delta_eur > 0 ? "delta-pos" : "delta-neg"}">${sgnEur(s.delta_eur)}</td>
      <td class="num">${s.n_receipts}${s.thin_sample ? ` <span class="badge-thin" title="Fewer than ${p.thin_sample_receipts} receipts — measured statistics are unstable">thin</span>` : ""}</td>
    </tr>`).join("");

  $("#relFoot").textContent = rel.note;
  const caveats = (rel.caveats || []).concat(rel.provenance ? [rel.provenance] : []);
  if (caveats.length) {
    $("#relCaveatList").innerHTML = caveats.map((c) => `<li>${esc(c)}</li>`).join("");
    $("#relCaveats").hidden = false;
  }
}

/* ---------------------------------------------------- ST-06 · proof strip */
/* The reconciliation guard as a checklist object: one cell per cross-engine
   identity, verdict and counts straight from /api/reconcile (the same ledger
   the executive brief renders). Fetched after the main load — the first call
   computes the ledger server-side, so the strip fills in when it lands. */
function renderProof(rec) {
  const n = rec.identities.length;
  const nOk = rec.identities.filter((i) => i.ok).length;
  $("#proofCount").innerHTML = `${nOk}<span class="of">/${n}</span>`;
  $("#proofStrip").innerHTML = rec.identities
    .map((i) => `<span class="p-cell ${i.ok ? "ok" : "fail"}" role="listitem" tabindex="0" title="${esc(i.statement)}">${i.ok ? "✓" : "✕"}</span>`)
    .join("");
  $("#proofPill").textContent = rec.all_ok ? "no silent drift · seed " + rec.seed : "drift found · seed " + rec.seed;
  $("#proofMeta").innerHTML =
    `<b>${nOk}/${n}</b> cross-engine identities hold · <b>${rec.claims.length}</b> headline numbers traced to their engine fields · ` +
    (rec.readme_ok ? `all present in the README` : `${rec.readme_missing.length} missing from the README`) +
    ` · measured on the ${esc(rec.data_label)}`;
}

async function loadProof() {
  try {
    renderProof(await getJSON("/api/reconcile"));
  } catch (err) {
    $("#proofMeta").textContent = "Could not load the reconciliation ledger (" + (err && err.message ? err.message : "network error") + ") — the executive brief has the full ledger.";
  }
}

/* ------------------------------------------------------------ render all */
function renderAll() {
  renderKPIs(); renderForecast(); renderBreakdown(); renderMargin();
  renderHeat(); renderRFM(); renderRoutes(); renderAssort(); renderActions();
  renderCrosssell(); renderKpiDrill(); renderInventory(); renderReliability();
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
  const station = el.closest(".station");
  $$(".nav-item").forEach((n) => n.classList.toggle("active", station && n.dataset.target === station.id));
  el.classList.add("focus-card");
  requestAnimationFrame(() => el.scrollIntoView({ behavior: "auto", block: "start" }));
}

/* ------------------------------------------------------------ boot */
async function loadData() {
  // no budget param: the server answers with its default (40% of full capital),
  // so the first render can never drift from the server-side definition.
  const [forecast, margin, abc, rfm, revenue, routes, assort, crosssell, kpiDrilldown, inventory, reliability] = await Promise.all([
    getJSON("/api/forecast"),
    getJSON("/api/margin-bridge"),
    getJSON("/api/abc-xyz"),
    getJSON("/api/rfm"),
    getJSON("/api/revenue"),
    getJSON("/api/optimize/routes"),
    getJSON("/api/optimize/assortment"),
    getJSON("/api/crosssell?top=50"),
    getJSON("/api/kpis/drilldown"),
    getJSON("/api/inventory"),
    getJSON("/api/reliability"),
  ]);
  STATE.forecast = forecast; STATE.margin = margin; STATE.abc = abc;
  STATE.rfm = rfm; STATE.revenue = revenue; STATE.routes = routes; STATE.assort = assort;
  STATE.crosssell = crosssell; STATE.kpiDrilldown = kpiDrilldown;
  STATE.inventory = inventory; STATE.reliability = reliability;
  // sync slider to the server-reported budget share
  $("#budgetSlider").value = Math.round((assort.budget / assort.full_capital) * 100);
  $("#loadError").hidden = true;
  renderAll();
  loadProof(); // non-blocking: the strip fills in when the ledger lands
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
