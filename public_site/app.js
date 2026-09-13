(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const dateFormat = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const moneyFormat = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
  const shortMoney = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const money = (value, signed = false) => value === null ? "N/A" : (signed && value > 0 ? "+" : "") + moneyFormat.format(value);
  const tone = value => value > 0 ? "positive" : value < 0 ? "negative" : "";
  const date = value => dateFormat.format(new Date(value));
  const duration = ms => ms === null ? "N/A" : Math.floor(ms / 86400000) + "d " + Math.floor(ms % 86400000 / 3600000) + "h";
  const DEFAULT_PERIOD = "all";
  const state = { period: DEFAULT_PERIOD, view: "overview", page: 1, query: "", outcome: "all", sort: "newest", pageSize: 10 };
  let snapshot, periodTrades, report, plotPoints = [], hoveredIndex = null;
  let reportInfo = null;
  let monumentAnimated = false;

  function attachTilt(root) {
    root.querySelectorAll(".tilt").forEach(card => {
      if (card.dataset.tiltBound) return;
      card.dataset.tiltBound = "1";
      card.addEventListener("pointermove", event => {
        const rect = card.getBoundingClientRect();
        const px = (event.clientX - rect.left) / rect.width, py = (event.clientY - rect.top) / rect.height;
        card.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
        card.style.setProperty("--my", (py * 100).toFixed(1) + "%");
        const rotateY = (px - 0.5) * 10, rotateX = (0.5 - py) * 8;
        card.style.transform = "perspective(900px) rotateX(" + rotateX.toFixed(2) + "deg) rotateY(" + rotateY.toFixed(2) + "deg) translateZ(2px)";
      });
      card.addEventListener("pointerleave", () => { card.style.transform = ""; });
    });
  }

  function initInteractionLayer() {
    const glow = $("cursor-glow");
    const magnets = [...document.querySelectorAll("button:not(:disabled), .site-header nav a")];
    magnets.forEach(el => el.classList.add("magnetic"));
    let raf = null, lastEvent = null;
    function apply() {
      raf = null;
      if (!lastEvent) return;
      glow.style.setProperty("--cx", lastEvent.clientX + "px");
      glow.style.setProperty("--cy", lastEvent.clientY + "px");
      glow.classList.add("active");
      for (const el of magnets) {
        const rect = el.getBoundingClientRect();
        const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
        const dx = lastEvent.clientX - cx, dy = lastEvent.clientY - cy;
        const dist = Math.hypot(dx, dy);
        const radius = 48;
        if (dist < radius) {
          const strength = (1 - dist / radius) * 0.32;
          el.style.transform = "translate(" + (dx * strength).toFixed(1) + "px," + (dy * strength).toFixed(1) + "px)";
        } else if (el.style.transform) {
          el.style.transform = "";
        }
      }
    }
    window.addEventListener("pointermove", event => {
      lastEvent = event;
      if (raf === null) raf = requestAnimationFrame(apply);
    }, { passive: true });
    document.addEventListener("pointerleave", () => glow.classList.remove("active"));
  }

  function countUp(el, target, format, duration = 900) {
    if (el._countRaf) cancelAnimationFrame(el._countRaf);
    if (!Number.isFinite(target)) { return; }
    const startTime = performance.now();
    const step = now => {
      const t = Math.min(1, (now - startTime) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = format(target * eased);
      if (t < 1) el._countRaf = requestAnimationFrame(step);
    };
    el._countRaf = requestAnimationFrame(step);
  }

  function dataStart() {
    return reportInfo ? (reportInfo.evaluationStart || reportInfo.firstSession) + "T00:00:00Z" : (snapshot.trades[0]?.exitTime || snapshot.asOf);
  }

  function renderSource() {
    reportInfo = globalThis.ALPHA_REPORT_INFO || null;
    if (!reportInfo || reportInfo.kind !== "historical_backtest") {
      $("data-kind").textContent = "DEMO DATA";
      $("data-disclaimer").textContent = "Illustrative results only. These are not Alpha Quant's actual performance.";
      $("snapshot-label").textContent = "DEMO SNAPSHOT";
      $("method-data-title").textContent = "This version uses fictional data.";
      $("method-data-description").textContent = "Every result in this preview is an illustrative example.";
      reportInfo = null;
      return;
    }
    $("data-kind").textContent = "HISTORICAL BACKTEST";
    $("data-disclaimer").textContent = "Yahoo Finance historical prices. Simulated execution, not live investment returns.";
    $("snapshot-label").textContent = "BACKTEST SNAPSHOT";
    $("method-data-title").textContent = reportInfo.model === "kalman" ? "Kalman + OU historical simulation." : "Historical prices. Simulated trades.";
    $("method-data-description").textContent = reportInfo.source + ". Data: " + date(reportInfo.firstSession) + " - " + date(reportInfo.lastSession) + ". The first " + reportInfo.trainingBars + " synchronized sessions form the initial estimation window.";
    if (reportInfo.selection === "training_only_sector_screen") {
      $("method-data-description").textContent += " Sector pairs were selected using only the first year of data, before " + date(reportInfo.evaluationStart) + ". Reported performance covers the subsequent evaluation period. The historical universe is a preset list, not a reconstruction of past index membership.";
    } else if (["annual_walk_forward_sector_screen", "quarterly_walk_forward_sector_screen"].includes(reportInfo.selection)) {
      $("method-data-description").textContent += " Walk-forward sector selection repeats every " + reportInfo.walkForward.rebalanceSessions + " observed market sessions using only the preceding " + reportInfo.walkForward.trainingSessions + " sessions. Each selection takes effect on the next session after training. Evaluation begins " + date(reportInfo.evaluationStart) + ". Removed pairs cannot open new positions; existing positions keep their normal exit rules. The universe is a preset list, not a reconstruction of past index membership.";
    }
    const costs = reportInfo.execution;
    const sizing = reportInfo.positionSizing;
    const sizeDescription = sizing?.method === "inverse_residual_volatility"
      ? "Entry size targets " + money(sizing.target_spread_risk) + " of exposure to one past spread residual standard deviation, using " + sizing.volatilityBars + " preceding observations. Gross exposure is constrained to " + money(sizing.min_gross_notional) + " - " + money(sizing.max_gross_notional) + " at execution prices including slippage. This residual-level risk proxy is not daily volatility or a loss limit; the minimum notional can exceed the target risk. "
      : "Each pair starts with " + money(costs.gross_notional) + " gross exposure, sized using its estimated hedge ratio. ";
    const relationship = reportInfo.model === "kalman"
      ? "The Kalman relationship updates each session. OU estimates use exponentially weighted past residuals; new entries require a valid mean-reversion half-life. Position quantities stay fixed at entry, without daily rebalancing. If the OU fit becomes invalid, the last valid mean and scale remain available for exits while new entries are blocked. A model crossing can reflect changing estimates, not a profitable trade."
      : "The fitted spread model and position quantities stay fixed for each position.";
    const portfolio = reportInfo.portfolioConfig;
    const capitalDescription = portfolio
      ? " Shared starting equity is " + money(portfolio.initial_equity) + ", with a limit of " + portfolio.max_concurrent_pairs + " simultaneous pairs. Single-ticker gross exposure is capped at " + (portfolio.max_ticker_fraction * 100).toFixed(0) + "% of marked equity when admitting entries, without netting opposing legs. Entry gross is fully reserved as collateral; short proceeds cannot fund another position. Marked P&L and accrued borrow settle into available cash. Entries shrink to affordable size or are rejected below the minimum. Exits execute before new entries at the same open. This public report contains only closed results, not total marked portfolio equity or portfolio drawdown."
      : " There is no portfolio capital constraint or compounding.";
    $("execution-assumptions").textContent = sizeDescription + "Costs: " + costs.commission_bps + " bps commission and " + costs.slippage_bps + " bps adverse slippage on each leg at entry and exit, plus " + (costs.annual_borrow_rate * 100).toFixed(1) + "% annual borrow on the entry short notional, prorated by calendar days. Signals use completed daily closes and execute at the next synchronized session open. " + relationship + capitalDescription;
    if (portfolio && Number.isFinite(portfolio.risk_free_rate)) {
      $("execution-assumptions").textContent += " Positive unreserved cash earns an assumed " + (portfolio.risk_free_rate * 100).toFixed(1) + "% annual ACT/365 yield, credited at session close for the time the balance was held. Reserved collateral earns no interest. Cash interest is excluded from the closed-trade P&L shown here.";
    }
    if (reportInfo.riskManagement) {
      const risk = reportInfo.riskManagement;
      $("execution-assumptions").textContent += " Risk exits are triggered after " + risk.max_holding_sessions + " completed synchronized trading sessions (entry session included), or when the absolute closing spread Z-score exceeds " + risk.stop_z + ". They execute at the following session open, so gaps can increase losses.";
    }
    if (reportInfo.entryFilters) {
      const filters = reportInfo.entryFilters;
      $("execution-assumptions").textContent += " New entries require a past-only spread Hurst estimate below " + filters.max_hurst + ". Early profit-taking is requested when absolute Z is at most " + filters.early_profit_z + " and estimated net liquidation P&L at the close is positive after modeled costs. The actual exit is at the next open and may realize a loss.";
    }
  }

  function row(trade, detailed) {
    const first = trade.pair.split(" / ")[0];
    return '<tr><td><div class="pair-cell"><span class="pair-symbol" data-tone="' + (first.charCodeAt(0) % 4) + '">' + escape(first.slice(0, 2)) + '</span><span><span class="pair-name">' + escape(trade.pair) + '</span><span class="pair-type">Equity pair / simulation</span></span></div></td>' +
      '<td><span class="sector-tag">' + escape(trade.sector) + '</span></td>' +
      (detailed ? '<td>' + date(trade.entryTime) + '</td>' : '') + '<td>' + date(trade.exitTime) + '</td><td>' + duration(Date.parse(trade.exitTime) - Date.parse(trade.entryTime)) + '</td><td><span class="closed-badge">CLOSED</span></td><td class="number ' + tone(trade.netPnl) + '">' + money(trade.netPnl, true) + '</td></tr>';
  }

  function ratio(value, digits) {
    return value === null || !Number.isFinite(value) ? "N/A" : value.toFixed(digits);
  }

  function renderMetaStrip() {
    const startYear = new Date(dataStart()).getUTCFullYear();
    const endYear = new Date(snapshot.asOf).getUTCFullYear();
    $("hero-tagline").textContent = "SELECTED WORKS // " + startYear + (endYear !== startYear ? "—" + endYear : "");
  }

  function renderMonumentNumbers(returnOnCapitalPct) {
    // Deliberately not color-coded by sign: the monument figures stay ink-black regardless
    // of direction, letting the +/- prefix alone carry that signal (editorial restraint).
    const monuments = [
      { label: "Pure Trading Alpha", value: report.pnl, format: v => money(v, true), note: report.count + " completed simulations, net of modeled costs" },
      { label: "Realized Return on Capital", value: returnOnCapitalPct, format: v => (v > 0 ? "+" : "") + v.toFixed(3) + "%", note: "Closed-trade net P&L ÷ starting equity" },
    ];
    $("monument-numbers").innerHTML = monuments.map((m, i) => '<div class="monument"><div class="monument-label">' + m.label + '</div><div class="monument-value" data-monument="' + i + '">' + (Number.isFinite(m.value) ? m.format(m.value) : "N/A") + '</div><div class="monument-note">' + m.note + '</div></div>').join("");
    if (!monumentAnimated) {
      monumentAnimated = true;
      const nodes = $("monument-numbers").querySelectorAll("[data-monument]");
      monuments.forEach((m, i) => { if (nodes[i] && Number.isFinite(m.value)) countUp(nodes[i], m.value, m.format, 1200); });
    }
  }

  function renderHeroAndTearsheet() {
    const portfolio = reportInfo?.portfolioConfig;
    const initialEquity = portfolio?.initial_equity;
    const returnOnCapitalPct = initialEquity ? report.pnl / initialEquity * 100 : null;
    const drawdownPct = initialEquity ? report.drawdown / initialEquity * 100 : null;
    renderMetaStrip();
    renderMonumentNumbers(returnOnCapitalPct);
    // Secondary context only: the primary P&L and return figures already live in the monument numbers above.
    const cards = [
      { label: "Profit Factor", value: ratio(report.profitFactor, 2), className: "", note: "Gross profit ÷ gross loss across closed trades" },
      { label: "Max Drawdown", value: drawdownPct === null ? "N/A" : drawdownPct.toFixed(3) + "%", className: report.drawdown ? "negative" : "", note: "Closed-trade peak-to-trough, " + money(-report.drawdown) },
      { label: "Win Rate", value: report.winRate === null ? "N/A" : report.winRate.toFixed(1) + "%", className: "", note: report.wins + " profitable / " + report.count + " closed" },
    ];
    $("hero-cards").innerHTML = cards.map(card => '<div class="hero-card tilt"><div class="metric-label">' + escape(card.label) + '</div><div class="metric-value ' + card.className + '">' + card.value + '</div><div class="metric-note">' + escape(card.note) + '</div></div>').join("");
    attachTilt($("hero-cards"));
    const tearsheet = [
      ["Closed-Trade Sharpe", ratio(report.closedTradeSharpe, 4), "Mean ÷ sample deviation of closed-trade net P&L (N=" + report.count + "). Not an annualized, NAV-based Sharpe ratio."],
      ["Closed-Trade Sortino", ratio(report.closedTradeSortino, 4), "Mean net P&L ÷ downside deviation of losing trades only."],
      ["Profit / Max Drawdown", ratio(report.profitToDrawdown, 4), "Net realized P&L divided by the largest peak-to-trough drop in the closed-trade cumulative curve."],
      ["Risk / Reward Ratio", ratio(report.riskRewardRatio, 2), "Average winning trade ÷ absolute average losing trade."],
      ["Single Ticker Cap", portfolio ? (portfolio.max_ticker_fraction * 100).toFixed(1) + "%" : "N/A", "Maximum gross exposure to one symbol, as a fraction of marked portfolio equity."],
      ["Sector Cap", portfolio && Number.isFinite(portfolio.max_sector_fraction) ? (portfolio.max_sector_fraction * 100).toFixed(1) + "%" : "N/A", "Maximum combined gross exposure to pairs sharing one sector."],
      ["Position Limit", portfolio ? "Up to " + portfolio.max_concurrent_pairs + " simultaneous pairs" : "N/A", "Configured ceiling on concurrently open pair positions; actual peak usage is not published."],
      ["Starting Equity", initialEquity ? money(initialEquity) : "N/A", "Shared capital base the simulated portfolio begins with."],
    ];
    $("tearsheet").innerHTML = tearsheet.map(([label, value, info]) => '<div class="tilt"><dt>' + escape(label) + ' <span class="info-icon" title="' + escape(info) + '" aria-label="' + escape(info) + '">ⓘ</span></dt><dd>' + escape(value) + '</dd></div>').join("");
    attachTilt($("tearsheet"));
  }

  function renderOverview() {
    $("no-closed-results").hidden = report.count !== 0;
    renderHeroAndTearsheet();
    $("chart-total").textContent = money(report.pnl, true);
    $("chart-total").className = tone(report.pnl);
    $("chart-count").textContent = "Across " + report.count + " closed trades";
    const start = state.period === "all" ? dataStart() : new Date(Date.parse(snapshot.asOf) - Number(state.period) * 86400000).toISOString();
    $("chart-start").textContent = date(start);
    $("chart-end").textContent = date(snapshot.asOf);
    $("performance-chart").setAttribute("aria-label", "Cumulative realized P&L from " + date(start) + " to " + date(snapshot.asOf) + ": " + money(report.pnl) + ". Maximum realized drawdown: " + money(report.drawdown) + ". Individual results are in the Closed trades view.");
    const positiveMax = Math.max(1, ...report.months.map(month => month.pnl));
    const negativeMax = Math.max(1, ...report.months.map(month => -month.pnl));
    $("monthly-bars").innerHTML = report.months.length ? report.months.map(month => {
      const label = new Intl.DateTimeFormat("en-US", { month: "short", year: "2-digit", timeZone: "UTC" }).format(new Date(month.month + "-01T00:00:00Z"));
      const height = month.pnl >= 0 ? month.pnl / positiveMax * 65 : -month.pnl / negativeMax * 22;
      return '<div class="month-column" aria-label="' + label + ': ' + money(month.pnl) + '"><span class="month-value ' + tone(month.pnl) + '" title="' + money(month.pnl) + '">' + (month.pnl >= 0 ? "+" : "-") + shortMoney.format(Math.abs(month.pnl)) + '</span><div class="month-track" aria-hidden="true"><div class="month-bar ' + (month.pnl < 0 ? "loss" : "") + '" style="height:' + height.toFixed(2) + '%"></div></div><span class="month-label">' + label + '</span></div>';
    }).join("") : '<p class="metric-note">No completed trades in this period.</p>';
    const statistics = [
      ["Average winning trade", money(report.averageWin, true), tone(report.averageWin)],
      ["Average losing trade", money(report.averageLoss), tone(report.averageLoss)],
      ["Average holding time", duration(report.averageHoldMs), ""],
      ["Best closed trade", money(report.best, true), tone(report.best)],
      ["Worst closed trade", money(report.worst, true), tone(report.worst)]
    ];
    $("statistics").innerHTML = statistics.map(([label, value, className]) => '<div><dt>' + label + '</dt><dd class="' + className + '">' + value + '</dd></div>').join("");
    $("recent-trades").innerHTML = [...periodTrades].reverse().slice(0, 5).map(trade => row(trade, false)).join("") || '<tr><td colspan="5">No completed trades in this period.</td></tr>';
    requestAnimationFrame(drawChart);
  }

  function drawChart() {
    if (state.view !== "overview") return;
    const canvas = $("performance-chart");
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (!width || !height) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 3);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(ratio, ratio);
    const left = 58, right = width - 12, top = 18, bottom = height - 12;
    const values = report.curve.map(point => point.pnl);
    const lowest = Math.min(0, ...values), highest = Math.max(0, ...values);
    const range = highest - lowest || 100;
    const min = lowest - range * 0.08, max = highest + range * 0.12;
    const y = value => bottom - (value - min) / (max - min) * (bottom - top);
    const start = state.period === "all" ? Date.parse(dataStart()) : Date.parse(snapshot.asOf) - Number(state.period) * 86400000;
    const end = Date.parse(snapshot.asOf);
    const x = time => left + (time - start) / Math.max(1, end - start) * (right - left);
    ctx.font = '11px "JetBrains Mono", Consolas, monospace';
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (let i = 0; i <= 4; i++) {
      const value = min + (max - min) * i / 4;
      const lineY = y(value);
      ctx.strokeStyle = "rgba(0,0,0,.08)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(left, lineY); ctx.lineTo(right, lineY); ctx.stroke();
      ctx.fillStyle = "#8a8a86";
      ctx.fillText((value < 0 ? "-" : "") + "$" + shortMoney.format(Math.abs(value)), left - 12, lineY);
    }
    ctx.setLineDash([]);
    plotPoints = report.curve.map((point, index) => ({ x: x(index === 0 ? start : Date.parse(point.time)), y: y(point.pnl), pnl: point.pnl, time: index === 0 ? new Date(start).toISOString() : point.time }));
    const finalPoint = plotPoints[plotPoints.length - 1];
    const drawPoints = [...plotPoints, { ...finalPoint, x: right }];
    const path = () => { ctx.moveTo(drawPoints[0].x, drawPoints[0].y); for (let i = 1; i < drawPoints.length; i++) { ctx.lineTo(drawPoints[i].x, drawPoints[i - 1].y); ctx.lineTo(drawPoints[i].x, drawPoints[i].y); } };
    const fill = ctx.createLinearGradient(0, top, 0, bottom);
    fill.addColorStop(0, "#1e462022"); fill.addColorStop(1, "#1e462002");
    ctx.beginPath(); path(); ctx.lineTo(right, y(0)); ctx.lineTo(left, y(0)); ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
    ctx.beginPath(); path(); ctx.strokeStyle = "#1e4620"; ctx.lineWidth = 1.6; ctx.lineJoin = "round"; ctx.stroke();
    ctx.beginPath(); ctx.arc(finalPoint.x, finalPoint.y, 3.5, 0, Math.PI * 2); ctx.fillStyle = "#1e4620"; ctx.fill(); ctx.strokeStyle = "#f4f3f1"; ctx.lineWidth = 2; ctx.stroke();
    if (hoveredIndex !== null && plotPoints[hoveredIndex]) {
      const point = plotPoints[hoveredIndex];
      ctx.strokeStyle = "rgba(0,0,0,.2)"; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(point.x, top); ctx.lineTo(point.x, bottom); ctx.stroke(); ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(point.x, point.y, 3.5, 0, Math.PI * 2); ctx.fillStyle = "#1e4620"; ctx.fill();
      const tooltip = $("chart-tooltip");
      tooltip.innerHTML = date(point.time) + '<strong>' + money(point.pnl, true) + '</strong>';
      tooltip.hidden = false;
      tooltip.style.left = Math.max(0, Math.min(width - tooltip.offsetWidth, point.x + 12)) + "px";
      tooltip.style.top = Math.max(0, Math.min(height - tooltip.offsetHeight, point.y - 62)) + "px";
    }
  }

  function renderLedger() {
    const trades = AlphaResults.filterTrades(periodTrades, state.query, state.outcome, state.sort);
    const pages = Math.max(1, Math.ceil(trades.length / state.pageSize));
    state.page = Math.min(state.page, pages);
    const offset = (state.page - 1) * state.pageSize;
    $("ledger-count").textContent = trades.length;
    const sum = trades.reduce((total, trade) => total + trade.netPnl, 0);
    $("ledger-pnl").textContent = money(sum, true);
    $("ledger-pnl").className = tone(sum);
    $("all-trades").innerHTML = trades.slice(offset, offset + state.pageSize).map(trade => row(trade, true)).join("");
    $("empty-trades").hidden = trades.length !== 0;
    $("table-count").textContent = trades.length ? "Showing " + (offset + 1) + "-" + Math.min(offset + state.pageSize, trades.length) + " of " + trades.length + " closed trades" : "0 closed trades";
    $("page-count").textContent = state.page + " / " + pages;
    $("previous-page").disabled = state.page <= 1;
    $("next-page").disabled = state.page >= pages;
  }

  function refreshPeriod() {
    periodTrades = AlphaResults.selectPeriod(snapshot, state.period);
    report = AlphaResults.summarize(periodTrades);
    hoveredIndex = null;
    $("chart-tooltip").hidden = true;
    const start = state.period === "all" ? dataStart() : new Date(Date.parse(snapshot.asOf) - Number(state.period) * 86400000).toISOString();
    $("period-caption").textContent = date(start) + " - " + date(snapshot.asOf);
    document.querySelectorAll("[data-period]").forEach(button => {
      const isActive = button.dataset.period === state.period;
      button.setAttribute("aria-pressed", String(isActive));
      button.classList.toggle("active", isActive);
    });
    renderOverview();
    renderLedger();
  }

  function switchView() {
    const view = location.hash.slice(1);
    state.view = ["overview", "trades", "methodology"].includes(view) ? view : "overview";
    for (const name of ["overview", "trades", "methodology"]) $("view-" + name).hidden = name !== state.view;
    document.querySelectorAll("[data-view]").forEach(link => {
      if (link.dataset.view === state.view) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    });
    const copy = {
      overview: { title: "We are <em>Alpha Quant</em>", plain: "We are Alpha Quant", desc: "An independent quantitative arbitrage & statistical arbitrage research engine." },
      trades: { title: "Closed trade journal", plain: "Closed trade journal", desc: "Every completed simulation, including the losses." },
      methodology: { title: "The record, explained", plain: "The record, explained", desc: "Scope, calculations, and the limits of simulated results." },
    }[state.view];
    $("page-title").innerHTML = copy.title + '<span class="heading-dot">.</span>';
    $("page-description").textContent = copy.desc;
    document.title = "Alpha Quant | " + copy.plain;
    $("hero-tagline").hidden = state.view !== "overview";
    $("result-controls").hidden = state.view === "methodology";
    requestAnimationFrame(drawChart);
  }

  function initBackgroundScene() {
    const canvas = $("bg-scene");
    if (!canvas || typeof THREE === "undefined") return;
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch (error) {
      return;
    }
    renderer.setClearColor(0x000000, 0);
    const scene = new THREE.Scene();
    // Tamed, editorial look: fog dissolves the lattice toward the horizon so it reads as
    // ambient depth behind the type, never as a hard grid competing with the copy.
    scene.fog = new THREE.FogExp2(0xf4f3f1, 0.035);
    const camera = new THREE.PerspectiveCamera(62, innerWidth / Math.max(1, innerHeight), 0.1, 120);
    camera.position.set(0, 3.4, 13);
    const cols = 46, rows = 26, spacing = 1.1;
    const geometry = new THREE.PlaneGeometry(cols * spacing, rows * spacing, cols - 1, rows - 1);
    geometry.rotateX(-Math.PI / 2.15);
    geometry.translate(0, -1.5, -6);
    const basePositions = Float32Array.from(geometry.attributes.position.array);
    const lattice = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: 0x111111, wireframe: true, transparent: true, opacity: 0.08, depthWrite: false, fog: true }));
    scene.add(lattice);
    const pointsGeometry = new THREE.BufferGeometry();
    pointsGeometry.setAttribute("position", geometry.attributes.position.clone());
    const points = new THREE.Points(pointsGeometry, new THREE.PointsMaterial({ color: 0x3a3a38, size: 0.06, transparent: true, opacity: 0.25, sizeAttenuation: true, fog: true, depthWrite: false }));
    scene.add(points);
    // Independent full-frustum starfield: guarantees glowing particles are visible across the
    // entire viewport regardless of how the ground lattice below happens to project on screen.
    const starCount = 260;
    const starPositions = new Float32Array(starCount * 3);
    for (let i = 0; i < starCount; i++) {
      starPositions[i * 3] = (Math.random() - 0.5) * 46;
      starPositions[i * 3 + 1] = (Math.random() - 0.5) * 26 + 4;
      starPositions[i * 3 + 2] = Math.random() * -34 + 10;
    }
    const starGeometry = new THREE.BufferGeometry();
    starGeometry.setAttribute("position", new THREE.BufferAttribute(starPositions, 3));
    const stars = new THREE.Points(starGeometry, new THREE.PointsMaterial({ color: 0x4a4a48, size: 0.055, transparent: true, opacity: 0.18, sizeAttenuation: true, fog: true, depthWrite: false }));
    scene.add(stars);
    let targetRX = 0, targetRY = 0, curRX = 0, curRY = 0;
    let lastMove = null;
    const impulses = [];
    // Scrollytelling: camera eases from a top-down topology view to a low horizontal
    // tunnel view as the reader scrolls; scroll velocity briefly ripples the grid.
    const CAMERA_TOP_DOWN = { pos: [0, 3.4, 13], look: [0, -0.5, -6] };
    const CAMERA_TUNNEL = { pos: [0, 0.85, 5], look: [0, -0.15, -26] };
    let scrollTarget = 0, scrollEased = 0, scrollVelocity = 0, lastScrollY = window.scrollY;
    function onScroll() {
      const y = window.scrollY;
      scrollVelocity += y - lastScrollY;
      lastScrollY = y;
      const max = document.documentElement.scrollHeight - window.innerHeight;
      scrollTarget = max > 0 ? Math.min(1, Math.max(0, y / max)) : 0;
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    function pushImpulse(nx, ny) {
      if (impulses.length > 6) impulses.shift();
      impulses.push({ x: nx * cols * spacing * 0.5, y: -ny * rows * spacing * 0.5, start: performance.now() });
    }
    function onPointerMove(event) {
      const nx = (event.clientX / innerWidth) * 2 - 1, ny = (event.clientY / innerHeight) * 2 - 1;
      targetRY = nx * 0.16; targetRX = ny * 0.09;
      if (lastMove) {
        const dt = Math.max(1, performance.now() - lastMove.t);
        const speed = Math.hypot(event.clientX - lastMove.x, event.clientY - lastMove.y) / dt;
        if (speed > 1.7) pushImpulse(nx, ny);
      }
      lastMove = { x: event.clientX, y: event.clientY, t: performance.now() };
    }
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerdown", event => pushImpulse((event.clientX / innerWidth) * 2 - 1, (event.clientY / innerHeight) * 2 - 1), { passive: true });
    function resize() {
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
      renderer.setSize(innerWidth, innerHeight, false);
      camera.aspect = innerWidth / Math.max(1, innerHeight);
      camera.updateProjectionMatrix();
    }
    window.addEventListener("resize", resize);
    resize();
    let paused = document.hidden;
    document.addEventListener("visibilitychange", () => { paused = document.hidden; });
    const clock = new THREE.Clock();
    (function loop() {
      requestAnimationFrame(loop);
      if (paused) return;
      const t = clock.getElapsedTime();
      curRX += (targetRX - curRX) * 0.04;
      curRY += (targetRY - curRY) * 0.04;
      scrollEased += (scrollTarget - scrollEased) * 0.045;
      scrollVelocity *= 0.9;
      const ease = scrollEased < 0.5 ? 2 * scrollEased * scrollEased : 1 - Math.pow(-2 * scrollEased + 2, 2) / 2;
      const lerp3 = (a, b, f) => [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
      const camPos = lerp3(CAMERA_TOP_DOWN.pos, CAMERA_TUNNEL.pos, ease);
      const lookPos = lerp3(CAMERA_TOP_DOWN.look, CAMERA_TUNNEL.look, ease);
      camera.position.set(camPos[0] + curRY * 4, camPos[1] - curRX * 3, camPos[2]);
      camera.lookAt(lookPos[0], lookPos[1], lookPos[2]);
      const now = performance.now();
      const scrollWave = Math.max(-2.5, Math.min(2.5, scrollVelocity * 0.03));
      const meshPos = geometry.attributes.position, dotPos = pointsGeometry.attributes.position;
      for (let i = 0; i < meshPos.count; i++) {
        const bx = basePositions[i * 3], by = basePositions[i * 3 + 1], bz = basePositions[i * 3 + 2];
        let z = bz + Math.sin(bx * 0.35 + t * 0.6) * 0.22 + Math.cos(by * 0.4 + t * 0.5) * 0.18 + scrollWave * Math.sin(bx * 0.18 - t * 2.2);
        for (const impulse of impulses) {
          const age = (now - impulse.start) / 1000;
          if (age > 1.8) continue;
          const radius = age * 9;
          const distance = Math.hypot(bx - impulse.x, by - impulse.y);
          z += Math.exp(-((distance - radius) ** 2) / 3) * (1 - age / 1.8) * 1.3;
        }
        meshPos.setZ(i, z); dotPos.setZ(i, z);
      }
      meshPos.needsUpdate = true; dotPos.needsUpdate = true;
      for (let i = impulses.length - 1; i >= 0; i--) if (now - impulses[i].start > 1800) impulses.splice(i, 1);
      renderer.render(scene, camera);
    })();
  }

  function initTopology(trades) {
    const canvas = $("topology-canvas");
    const wrap = canvas ? canvas.parentElement : null;
    if (!canvas || !wrap || typeof THREE === "undefined" || typeof THREE.OrbitControls === "undefined") {
      $("topology-unavailable").hidden = false;
      return;
    }
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    } catch (error) {
      $("topology-unavailable").hidden = false;
      return;
    }
    // Grouped only from already-public closed-trade data (pair sector label, exit quarter, net P&L).
    // No signal, Z-score, or entry-threshold values are read here.
    const quarterOf = trade => { const d = new Date(trade.exitTime); return d.getUTCFullYear() + " Q" + (Math.floor(d.getUTCMonth() / 3) + 1); };
    const cells = new Map();
    for (const trade of trades) {
      const quarter = quarterOf(trade);
      const key = trade.sector + "|" + quarter;
      const cell = cells.get(key) || { sector: trade.sector, quarter, pnl: 0, count: 0 };
      cell.pnl += trade.netPnl; cell.count += 1;
      cells.set(key, cell);
    }
    const sectors = [...new Set(trades.map(trade => trade.sector))].sort();
    const orderedQuarters = [...new Set(trades.map(quarterOf))].sort();
    const maxAbs = Math.max(1, ...[...cells.values()].map(cell => Math.abs(cell.pnl)));
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    const group = new THREE.Group();
    const spacing = 1.35;
    const meshes = [];
    orderedQuarters.forEach((quarter, qi) => sectors.forEach((sector, si) => {
      const cell = cells.get(sector + "|" + quarter);
      const pnl = cell ? cell.pnl : 0;
      const height = 0.06 + Math.abs(pnl) / maxAbs * 2.4;
      const color = pnl > 0 ? 0x1e4620 : pnl < 0 ? 0x6b3232 : 0xb5b2ab;
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(0.92, height, 0.92),
        new THREE.MeshStandardMaterial({ color, metalness: 0.05, roughness: 0.85 })
      );
      mesh.position.set((qi - orderedQuarters.length / 2) * spacing, height / 2, (si - sectors.length / 2) * spacing);
      mesh.userData = { sector, quarter, pnl, count: cell ? cell.count : 0 };
      group.add(mesh);
      meshes.push(mesh);
    }));
    scene.add(group);
    const span = Math.max(orderedQuarters.length, sectors.length, 4) * spacing;
    const gridHelper = new THREE.GridHelper(span * 1.4, 16, 0x111111, 0x111111);
    gridHelper.material.transparent = true;
    gridHelper.material.opacity = 0.14;
    scene.add(gridHelper);
    scene.add(new THREE.AmbientLight(0xffffff, 0.95));
    const sun = new THREE.DirectionalLight(0xffffff, 0.4);
    sun.position.set(4, 6, 5);
    scene.add(sun);
    camera.position.set(span * 0.7, span * 0.62, span * 0.9);
    camera.lookAt(0, 0, 0);
    const controls = new THREE.OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 1.1;
    controls.minDistance = span * 0.4;
    controls.maxDistance = span * 2.4;
    controls.addEventListener("start", () => { controls.autoRotate = false; });
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hovered = null;
    function resize() {
      const width = wrap.clientWidth, height = wrap.clientHeight;
      if (!width || !height) return;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      renderer.setPixelRatio(ratio);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
    }
    function onPointerMove(event) {
      const rect = canvas.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObjects(meshes)[0];
      const tooltip = $("topology-tooltip");
      if (!hit) { hovered = null; tooltip.hidden = true; return; }
      hovered = hit.object;
      const info = hovered.userData;
      tooltip.innerHTML = escape(info.sector) + " &middot; " + info.quarter + '<strong class="' + tone(info.pnl) + '">' + money(info.pnl, true) + '</strong><span>' + info.count + ' closed trade' + (info.count === 1 ? "" : "s") + '</span>';
      tooltip.hidden = false;
      tooltip.style.left = Math.max(0, Math.min(wrap.clientWidth - tooltip.offsetWidth, event.clientX - rect.left + 14)) + "px";
      tooltip.style.top = Math.max(0, Math.min(wrap.clientHeight - tooltip.offsetHeight, event.clientY - rect.top - 54)) + "px";
    }
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerleave", () => { hovered = null; $("topology-tooltip").hidden = true; });
    window.addEventListener("resize", resize);
    resize();
    (function loop() {
      if (state.view !== "overview") { requestAnimationFrame(loop); return; }
      controls.update();
      renderer.render(scene, camera);
      requestAnimationFrame(loop);
    })();
  }

  function resetFilters() {
    state.query = ""; state.outcome = "all"; state.sort = "newest"; state.page = 1; state.period = DEFAULT_PERIOD;
    $("pair-search").value = ""; $("outcome-filter").value = "all"; $("sort-order").value = "newest";
    refreshPeriod();
  }

  try { initBackgroundScene(); } catch (backgroundError) { console.warn("Background scene unavailable.", backgroundError.message); }

  try {
    snapshot = AlphaResults.validateSnapshot(ALPHA_DEMO_DATA);
    renderSource();
    $("snapshot-date").textContent = date(snapshot.asOf);
    $("application").hidden = false;
    try { initInteractionLayer(); } catch (interactionError) { console.warn("Interaction layer unavailable.", interactionError.message); }
    try { initTopology(snapshot.trades); } catch (topologyError) {
      console.warn("3D topology unavailable.", topologyError.message);
      $("topology-unavailable").hidden = false;
    }
    // The initial render must always use the complete validated snapshot.
    // Hash navigation changes only the view; it never restores a cached reporting period.
    state.period = DEFAULT_PERIOD;
    switchView(); refreshPeriod();
    document.querySelectorAll("[data-period]").forEach(button => button.addEventListener("click", () => { state.period = button.dataset.period; state.page = 1; refreshPeriod(); }));
    $("pair-search").addEventListener("input", event => { state.query = event.target.value; state.page = 1; renderLedger(); });
    $("outcome-filter").addEventListener("change", event => { state.outcome = event.target.value; state.page = 1; renderLedger(); });
    $("sort-order").addEventListener("change", event => { state.sort = event.target.value; state.page = 1; renderLedger(); });
    $("clear-filters").addEventListener("click", resetFilters);
    $("empty-reset").addEventListener("click", resetFilters);
    $("corner-filter").addEventListener("click", () => {
      location.hash = "trades";
      requestAnimationFrame(() => $("pair-search").focus());
    });
    $("corner-tearsheet").addEventListener("click", () => {
      if (state.view !== "overview") location.hash = "overview";
      requestAnimationFrame(() => $("tearsheet-section").scrollIntoView({ behavior: "smooth", block: "start" }));
    });
    $("previous-page").addEventListener("click", () => { state.page--; renderLedger(); });
    $("next-page").addEventListener("click", () => { state.page++; renderLedger(); });
    window.addEventListener("hashchange", switchView);
    window.addEventListener("resize", () => { hoveredIndex = null; $("chart-tooltip").hidden = true; requestAnimationFrame(drawChart); });
    $("performance-chart").addEventListener("pointermove", event => {
      if (!plotPoints.length) return;
      const localX = event.clientX - event.currentTarget.getBoundingClientRect().left;
      hoveredIndex = plotPoints.reduce((best, point, index) => Math.abs(point.x - localX) < Math.abs(plotPoints[best].x - localX) ? index : best, 0);
      drawChart();
    });
    $("performance-chart").addEventListener("pointerleave", () => { hoveredIndex = null; $("chart-tooltip").hidden = true; drawChart(); });
  } catch (error) {
    $("application").hidden = true;
    $("fatal-error").hidden = false;
    console.error("Public results could not be initialized.", error.message);
  }
})();
