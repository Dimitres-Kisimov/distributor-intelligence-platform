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
const STATE = { kpis: null, forecast: null, margin: null, abc: null, rfm: null, revenue: null, routes: null, assort: null, breakdownDim: "region", routeMode: "optimized" };

/* ------------------------------------------------------------------ KPIs */
function renderKPIs() {
  const k = STATE.kpis, p = window.__INITIAL__.plan;
  $("#kpi-revenue").textContent = eur(k.revenue);
  $("#kpi-margin").textContent = pct(k.gross_margin_pct);
  $("#kpi-margin-sub").textContent = eur(k.gross_margin) + " gross margin";
  $("#kpi-yoy").textContent = (k.yoy >= 0 ? "+" : "") + pct(k.yoy);
  $("#kpi-yoy-sub").className = "delta " + (k.yoy >= 0 ? "up" : "down");
  $("#kpi-yoy-sub").textContent = "last 12 vs prior 12";
  $("#kpi-uplift").textContent = eur(p.expected_uplift_eur);
  $("#kpi-uplift-sub").textContent = pct(p.expected_uplift_pct) + " of gross margin";
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
      html += `<div class="cell" style="background:${bg}" data-tip="${a + x}: ${cell.count} SKUs · ${eurFull(cell.revenue)}">
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
      (s) => `<div class="bar-row">
        <span title="${s.segment}">${s.segment}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(s.monetary / max) * 100}%;background:${cmap[s.segment] || PALETTE.blue}"></div></div>
        <span class="bar-val">${s.count} · ${eur(s.monetary)}</span>
      </div>`
    )
    .join("");
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

  // stats
  const km = STATE.routeMode === "optimized" ? r.optimized_km : r.baseline_km;
  $("#route-km").textContent = num(km) + " km";
  $("#route-km-l").textContent = STATE.routeMode === "optimized" ? "km (OR-Tools)" : "km (baseline)";
  $("#route-saved").textContent = num(r.km_saved) + " km";
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
    STATE.assort = await getJSON("/api/optimize/assortment?budget=" + Math.round(budget));
    renderAssort();
  }, 130);
}

/* ------------------------------------------------------------ actions */
function renderActions() {
  const p = window.__INITIAL__.plan;
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
}

/* ------------------------------------------------------------ boot */
async function boot() {
  initTheme();
  initNav();
  STATE.kpis = window.__INITIAL__.kpis;
  renderKPIs();
  renderActions();

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
  $("#budgetSlider").addEventListener("input", (e) => onBudget(+e.target.value));

  // load everything in parallel
  const [forecast, margin, abc, rfm, revenue, routes, assort] = await Promise.all([
    getJSON("/api/forecast"),
    getJSON("/api/margin-bridge"),
    getJSON("/api/abc-xyz"),
    getJSON("/api/rfm"),
    getJSON("/api/revenue"),
    getJSON("/api/optimize/routes"),
    getJSON("/api/optimize/assortment?budget=" + Math.round(0.4 * 26394)),
  ]);
  STATE.forecast = forecast; STATE.margin = margin; STATE.abc = abc;
  STATE.rfm = rfm; STATE.revenue = revenue; STATE.routes = routes; STATE.assort = assort;
  // sync slider to actual full capital
  $("#budgetSlider").value = Math.round((assort.budget / assort.full_capital) * 100);
  renderAll();

  window.addEventListener("resize", () => { clearTimeout(window.__rz); window.__rz = setTimeout(renderAll, 150); });
}

document.addEventListener("DOMContentLoaded", boot);
