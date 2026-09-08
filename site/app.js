/* A dependency-free board. All feed text is rendered through textContent. */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root && root.document) api.start(root.document);
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";
  const ZONE = "America/New_York";
  const MAX_AGE = 26 * 60 * 60 * 1000;
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const number = (value, digits = 1) => finite(value) ? value.toLocaleString("en-US", {minimumFractionDigits: digits, maximumFractionDigits: digits}) : "—";
  const percent = (value, signed = false) => finite(value) ? `${signed && value > 0 ? "+" : ""}${number(value * 100)}%` : "—";
  const odds = value => finite(value) ? `${value > 0 ? "+" : ""}${number(value, 0)}` : "—";
  const titleCase = value => String(value || "").replace(/[_-]/g, " ").replace(/\b\w/g, char => char.toUpperCase());
  const items = value => Array.isArray(value) ? value : [];
  function epoch(value) { return typeof value === "string" && value.trim() ? Date.parse(value) : NaN; }
  function dateKey(value = new Date()) {
    const date = value instanceof Date ? value : new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return new Intl.DateTimeFormat("en-CA", {timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit"}).format(date);
  }
  function dateLabel(value, options = {}) {
    const stamp = epoch(value);
    return Number.isFinite(stamp) ? new Intl.DateTimeFormat("en-US", {timeZone: ZONE, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short", ...options}).format(stamp) : "Time unavailable";
  }
  function health(board, now = new Date()) {
    const stamp = epoch(board.generated_at);
    const age = now.getTime() - stamp;
    if (board.schema_version !== 1) return {usable: false, kind: "error", title: "Board format unavailable", message: "The current board could not be read. No selections are being shown."};
    if (!Number.isFinite(age) || age > MAX_AGE || age < -5 * 60 * 1000) return {usable: false, kind: "warning", title: "Refresh overdue · picks paused", message: "This publication is over 26 hours old or has an invalid timestamp. Check back after a successful refresh."};
    if (board.status !== "ok") return {usable: false, kind: "error", title: "Data unavailable · picks paused", message: board.message || "The latest run could not verify the data required to publish selections."};
    if (board.date !== dateKey(now)) return {usable: false, kind: "warning", title: "Awaiting today's edition", message: "The last publication is from another Eastern calendar day. Today's selections will appear after the next successful run."};
    return {usable: true, kind: "ok", title: "Board refreshed", message: board.message || "Current publication loaded. All times are Eastern; prices are timestamped snapshots."};
  }
  function currentPicks(board, list, now = new Date(), today = true) {
    if (!health(board, now).usable) return [];
    return items(list).filter(pick => {
      const kickoff = epoch(pick.kickoff);
      const quoteAge = now.getTime() - epoch(pick.quote_time);
      return kickoff > now.getTime() && Number.isFinite(quoteAge) && quoteAge >= -5 * 60 * 1000 && quoteAge <= MAX_AGE &&
        (today ? dateKey(pick.kickoff) === dateKey(now) : dateKey(pick.kickoff) > dateKey(now));
    }).sort((a, b) => (finite(b.robust_ev) ? b.robust_ev : -Infinity) - (finite(a.robust_ev) ? a.robust_ev : -Infinity));
  }
  function dedupeResults(rows) {
    const seen = new Set();
    return items(rows).slice().sort((a, b) => (epoch(a.recorded_at) || Infinity) - (epoch(b.recorded_at) || Infinity)).filter(row => {
      if (!row.game_id || !row.candidate) return false;
      const key = `${row.candidate}:${row.game_id}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    }).sort((a, b) => (epoch(b.kickoff) || 0) - (epoch(a.kickoff) || 0));
  }
  function safeUrl(value) {
    if (typeof value !== "string") return null;
    try { const url = new URL(value, "https://example.invalid/"); return ["https:", "http:"].includes(url.protocol) ? value : null; } catch (_) { return null; }
  }
  function start(doc) {
    let board = null;
    const $ = id => doc.getElementById(id);
    function element(tag, text, cls) {
      const node = doc.createElement(tag);
      if (text !== undefined && text !== null) node.textContent = String(text);
      if (cls) node.className = cls;
      return node;
    }
    function link(text, url) {
      const a = element("a", text);
      a.href = safeUrl(url) || "#";
      if (/^https?:\/\//.test(a.getAttribute("href"))) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
      return a;
    }
    function empty(parent, title, message, table = false) {
      parent.replaceChildren();
      if (table) { parent.append(element("p", message || title, "table-empty")); return; }
      const box = element("div", null, "empty-state");
      const symbol = element("span", "—", "empty-symbol"); symbol.setAttribute("aria-hidden", "true");
      box.append(symbol, element("h3", title), element("p", message)); parent.append(box);
    }
    function cell(title, subtitle, cls) {
      const node = element("td", null, cls);
      node.append(element("span", title, "cell-title"));
      if (subtitle) node.append(element("span", subtitle, "cell-meta"));
      return node;
    }
    function table(parent, caption, columns, rows) {
      parent.replaceChildren();
      const node = element("table"); node.append(element("caption", caption));
      const head = element("thead"), tr = element("tr");
      columns.forEach(column => { const th = element("th", column.label || column, column.numeric ? "numeric" : ""); th.scope = "col"; tr.append(th); });
      head.append(tr); node.append(head);
      const body = element("tbody"); rows.forEach(cells => { const row = element("tr"); cells.forEach(value => row.append(value)); body.append(row); });
      node.append(body); parent.append(node);
    }
    function pickCard(pick, index) {
      const card = element("article", null, "pick-card");
      const top = element("div", null, "pick-top"); top.append(element("span", `SELECTION ${String(index + 1).padStart(2, "0")}`, "pick-rank"), element("span", titleCase(pick.candidate), "candidate-name"));
      const game = element("div", null, "pick-game");
      const heading = element("h3"); heading.append(doc.createTextNode(String(pick.away_team || "Away")), element("span", " at ", "versus"), doc.createTextNode(String(pick.home_team || "Home")));
      game.append(heading, element("div", dateLabel(pick.kickoff), "kickoff"));
      const price = element("div", null, "pick-price"), selection = element("div", `${titleCase(pick.side)} ${number(pick.line)}`, "pick-selection");
      selection.append(element("small", odds(pick.american_odds)));
      const ev = element("div", null, "pick-book"); ev.append(element("div", percent(pick.robust_ev, true), "pick-ev"), element("div", "Conservative modeled EV")); price.append(selection, ev);
      const metrics = element("div", null, "pick-metrics");
      [["Projected total", number(pick.projected_total)], ["Consensus total", number(pick.consensus_total)], ["Win probability", percent(pick.win_probability)]].forEach(([label, value]) => {const dl = element("dl"); dl.append(element("dt", label), element("dd", value)); metrics.append(dl);});
      const bottom = element("div", null, "pick-bottom");
      bottom.append(element("p", `${titleCase(pick.sportsbook)} · Quoted ${dateLabel(pick.quote_time)}`));
      bottom.append(element("p", `Modeled EV ${percent(pick.expected_value, true)} · Push ${percent(pick.push_probability)} · Paper stake ${percent(pick.paper_stake_fraction)} of bankroll`));
      if (items(pick.flags).length) { const flags = element("div", null, "flags"); pick.flags.forEach(flag => flags.append(element("span", titleCase(flag), "flag"))); bottom.append(flags); }
      card.append(top, game, price, metrics, bottom); return card;
    }
    function renderPicks(now) {
      const state = health(board, now);
      $("status-banner").className = `status-banner ${state.kind}`;
      $("status-title").textContent = state.title; $("status-message").textContent = state.message;
      $("updated-at").textContent = Number.isFinite(epoch(board.generated_at)) ? `Published ${dateLabel(board.generated_at)}` : "Publication time unavailable";
      const today = currentPicks(board, board.today_picks, now), upcoming = currentPicks(board, board.upcoming_picks, now, false);
      $("pick-count").textContent = String(today.length);
      if (today.length) $("today-picks").replaceChildren(...today.map(pickCard));
      else empty($("today-picks"), state.usable ? "No qualifying picks today" : "Selections paused", state.usable ? "No current pregame selection passes the publication rules. The model will continue to track future matchups and record its results." : state.message);
      $("upcoming-count").textContent = upcoming.length ? `${upcoming.length} future selections` : "";
      if (!upcoming.length) empty($("upcoming-picks"), "", state.usable ? "No future selections currently pass the publication rules." : "Watchlist paused until a current publication is available.", true);
      else table($("upcoming-picks"), "Upcoming experimental selections", ["Matchup", "Selection", "Projection", "Win probability", "Conservative EV", "Quote"], upcoming.map(p => [
        cell(`${p.away_team} at ${p.home_team}`, dateLabel(p.kickoff)), cell(`${titleCase(p.side)} ${number(p.line)} (${odds(p.american_odds)})`, titleCase(p.sportsbook)), cell(number(p.projected_total), titleCase(p.candidate)), cell(percent(p.win_probability), `Push ${percent(p.push_probability)}`), cell(percent(p.robust_ev, true), "Model estimate", finite(p.robust_ev) && p.robust_ev > 0 ? "positive" : ""), cell(dateLabel(p.quote_time), "Snapshot price")
      ]));
    }
    function renderCandidates() {
      const candidates = items(board.candidates);
      $("model-version").textContent = board.model_version ? `Version ${board.model_version}` : "";
      $("evidence-message").textContent = board.evidence_status === "research_only" ? "Research only. Historical performance and modeled EV do not establish a live betting edge. Candidate selection, price timing, and the independent forward record remain part of the evaluation." : "Profitability requires a credible, time-ordered evaluation and an independent forward record. Review the intervals and sample sizes below.";
      if (!candidates.length) empty($("candidate-table"), "", "Candidate evaluation metrics have not been published.", true);
      else table($("candidate-table"), "Historical candidate comparison", ["Candidate", {label: "Games", numeric: true}, {label: "MAE", numeric: true}, {label: "RMSE", numeric: true}, {label: "Bets", numeric: true}, {label: "ROI", numeric: true}, "95% ROI interval", {label: "Brier", numeric: true}], candidates.map(m => [
        cell(titleCase(m.candidate), m.status || "Evaluation status unavailable"), cell(number(m.games, 0), null, "numeric"), cell(number(m.mae, 2), null, "numeric"), cell(number(m.rmse, 2), null, "numeric"), cell(number(m.bets, 0), null, "numeric"), cell(percent(m.roi, true), null, "numeric"), cell(finite(m.roi_95_low) && finite(m.roi_95_high) ? `${percent(m.roi_95_low)} to ${percent(m.roi_95_high)}` : "Not estimated"), cell(number(m.brier, 3), null, "numeric")
      ]));
      const links = $("research-links"); links.replaceChildren();
      items(board.reports).forEach(report => { if (safeUrl(report.url)) links.append(link(`${report.name || "Research report"} ↗`, report.url)); });
      const forward = items(board.forecast_performance);
      $("forward-forecast-count").textContent = `(${forward.length} candidates)`;
      if (!forward.length) empty($("forward-forecast-table"), "", "No forward forecast evaluation published yet.", true);
      else table($("forward-forecast-table"), "Forward forecast performance including abstentions", ["Candidate", {label:"Settled",numeric:true}, {label:"Pending",numeric:true}, {label:"MAE",numeric:true}, {label:"RMSE",numeric:true}, {label:"Brier",numeric:true}, {label:"Log loss",numeric:true}], forward.map(m => [
        cell(titleCase(m.candidate), `${number(m.forecast_entries, 0)} forecasts · ${number(m.abstentions, 0)} abstentions`), cell(number(m.games, 0), null, "numeric"), cell(number(m.pending, 0), null, "numeric"), cell(number(m.mae, 2), null, "numeric"), cell(number(m.rmse, 2), null, "numeric"), cell(number(m.brier, 3), `${number(m.probability_scoring_games, 0)} scored`, "numeric"), cell(number(m.log_loss, 3), null, "numeric")
      ]));
      const candidatesSeen = [...new Set(items(board.forecasts).map(row => row.candidate).filter(Boolean))].sort();
      $("candidate-filter").replaceChildren(element("option", "All candidates")); $("candidate-filter").firstChild.value = "all";
      candidatesSeen.forEach(candidate => { const option = element("option", titleCase(candidate)); option.value = candidate; $("candidate-filter").append(option); });
      const primary = (board.performance || {}).candidate || "market_consensus_loo";
      if (candidatesSeen.includes(primary)) $("candidate-filter").value = primary;
      else if (candidatesSeen.length) $("candidate-filter").value = candidatesSeen[0];
      renderForecasts();
    }
    function renderForecasts() {
      const all = items(board.forecasts), candidate = $("candidate-filter").value;
      const rows = all.filter(row => candidate === "all" || row.candidate === candidate);
      $("forecast-count").textContent = `(${all.length})`;
      if (!rows.length) { empty($("forecast-table"), "", "No forecasts published for this selection.", true); return; }
      table($("forecast-table"), "All published forecasts; archived snapshots may be stale", ["Matchup", "Candidate", "Projection", "Market total", "Modeled EV", "Publication decision"], rows.map(row => [
        cell(`${row.away_team} at ${row.home_team}`, dateLabel(row.kickoff)), cell(titleCase(row.candidate)), cell(number(row.projected_total)), cell(number(row.line), `${titleCase(row.side)} ${odds(row.american_odds)}`), cell(percent(row.expected_value, true)), cell(row.eligible ? "Qualified at publication" : "No selection", items(row.flags).map(titleCase).join(" · "))
      ]));
    }
    function renderPerformance() {
      const p = board.performance || {}, settled = Number(p.wins || 0) + Number(p.losses || 0) + Number(p.pushes || 0);
      const stats = $("performance-stats"); stats.replaceChildren();
      [["Settled paper bets", number(settled, 0), `${number(p.wins, 0)} W · ${number(p.losses, 0)} L · ${number(p.pushes, 0)} P`], ["Profit / loss", settled ? `${finite(p.profit_units) && p.profit_units > 0 ? "+" : ""}${number(p.profit_units, 2)} u` : "—", "At recorded prices"], ["Realized ROI", settled ? percent(p.roi, true) : "—", "Profit divided by total stake"], ["95% ROI interval", settled && finite(p.roi_95_low) && finite(p.roi_95_high) ? `${percent(p.roi_95_low)} / ${percent(p.roi_95_high)}` : "—", "Uncertainty in forward returns"]].forEach(([label, value, detail]) => {
        const node = element("div", null, "stat"); node.append(element("span", label, "stat-label"), element("span", value, "stat-value"), element("span", detail, "stat-detail")); stats.append(node);
      });
      $("performance-note").textContent = `Primary candidate: ${titleCase(p.candidate || "market_consensus_loo")}. ${settled ? "Paper results use one recorded selection per candidate and game. Returns may differ from executable betting results." : "No settled forward record yet. A profitable live edge has not been demonstrated."}`;
      const rows = dedupeResults(board.results);
      if (!rows.length) { empty($("results-table"), "", "No forward ledger rows have been published yet.", true); return; }
      table($("results-table"), "Deduplicated forward paper selections", ["Matchup", "Candidate", "Recorded selection", "Result", "Profit"], rows.map(row => [
        cell(`${row.away_team} at ${row.home_team}`, dateLabel(row.kickoff)), cell(titleCase(row.candidate), row.recorded_at ? `Recorded ${dateLabel(row.recorded_at)}` : null), cell(`${titleCase(row.side)} ${number(row.line)} (${odds(row.american_odds)})`), cell(titleCase(row.result || "pending")), cell(finite(row.profit_units) ? `${row.profit_units > 0 ? "+" : ""}${number(row.profit_units, 2)} u` : "—", null, row.profit_units > 0 ? "positive" : row.profit_units < 0 ? "negative" : "")
      ]));
    }
    function renderTransparency() {
      $("limitations-list").replaceChildren(...items(board.limitations).map(text => element("li", text)));
      if (!items(board.limitations).length) $("limitations-list").append(element("li", "Projected win probabilities depend on model assumptions. Public source coverage, market timing, and sample size can limit the conclusions."));
      $("sources-list").replaceChildren();
      items(board.sources).forEach(source => { if (safeUrl(source.url)) { const li = element("li"); li.append(link(`${source.name} ↗`, source.url)); $("sources-list").append(li); } });
      if (!$("sources-list").children.length) $("sources-list").append(element("li", "No source references in this publication."));
      $("diagnostics-list").replaceChildren();
      Object.entries(board.diagnostics || {}).forEach(([key, value]) => $("diagnostics-list").append(element("dt", titleCase(key)), element("dd", typeof value === "object" ? JSON.stringify(value) : String(value))));
    }
    function render(data) {
      board = data;
      $("edition-date").textContent = new Intl.DateTimeFormat("en-US", {timeZone: ZONE, weekday: "long", month: "long", day: "numeric", year: "numeric"}).format(new Date());
      renderPicks(new Date()); renderCandidates(); renderPerformance(); renderTransparency();
    }
    $("candidate-filter").addEventListener("change", renderForecasts);
    fetch(`data/board.json?refresh=${Date.now()}`, {cache: "no-store"}).then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }).then(render).catch(() => render({schema_version: 1, generated_at: new Date().toISOString(), date: dateKey(), status: "unavailable", message: "The published data file could not be loaded. No selections are being shown. Try refreshing the page."}));
    setInterval(() => { if (board) renderPicks(new Date()); }, 60000);
  }
  return {dateKey, health, currentPicks, dedupeResults, safeUrl, percent, number, start};
});
