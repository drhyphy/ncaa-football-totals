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
  const percent = (value, signed = false, digits = 1) => finite(value) ? `${signed && value > 0 ? "+" : ""}${number(value * 100, digits)}%` : "—";
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
  function weatherHealth(board, now = new Date()) {
    const parent = health(board, now);
    if (!parent.usable) return {usable: false, message: parent.message};
    const strategy = board.weather_strategy || {};
    const age = now.getTime() - epoch(strategy.as_of);
    if (strategy.status !== "ok") return {usable: false, message: strategy.message || "The weather strategy has no successful current publication."};
    if (!Number.isFinite(age) || age < -300000 || age > MAX_AGE || dateKey(strategy.as_of) !== dateKey(now)) return {usable: false, message: "Weather selections are paused until a current weather publication is available."};
    return {usable: true, message: strategy.message || "The fixed weather rule was checked. Prices remain unconfirmed until accepted by the sportsbook."};
  }
  function currentWeatherPicks(board, now = new Date()) {
    if (!weatherHealth(board, now).usable) return [];
    const seen = new Set();
    return items(board.weather_strategy.today_picks).filter(pick => {
      const age = now.getTime() - epoch(pick.quote_time);
      if (!pick.game_id || seen.has(String(pick.game_id)) || epoch(pick.kickoff) <= now.getTime() || dateKey(pick.kickoff) !== dateKey(now) || !Number.isFinite(age) || age < -300000 || age > 3600000 || pick.side !== "under") return false;
      if (!finite(pick.line) || pick.line <= 0 || !finite(pick.decimal_odds) || pick.decimal_odds < 1 + 100 / 110 - 1e-12) return false;
      seen.add(String(pick.game_id)); return true;
    }).sort((a, b) => epoch(a.kickoff) - epoch(b.kickoff));
  }
  function weatherMeasurements(pick) {
    const feature = pick.weather || {}, values = feature.weather || feature;
    return {wind_mph: values.wind_mph, temperature_f: values.temperature_f, relative_humidity_pct: values.relative_humidity_pct ?? values.relative_humidity_percent};
  }
  function weatherRevisionHealth(data, now = new Date()) {
    if (data?.schema_version !== "weather-revision-status-v1" || data.collection_only !== true || data.performance_evaluated !== false || data.active_policy_changed !== false) return {visible: false};
    const start = epoch(data.pilot_start), end = epoch(data.pilot_end), current = now.getTime();
    if (start !== epoch("2026-09-09T03:00:00Z") || end !== epoch("2026-09-16T03:00:00Z")) return {visible: false};
    if (current < start) return {visible: true, label: "Scheduled", message: "The seven-day collection window has not started."};
    if (current >= end) return {visible: true, label: "Ended", message: "The pilot window has ended; scheduled collection is stopped. Archived inputs remain separate from model performance evidence."};
    const age = current - epoch(data.generated_at), receiptAge = current - epoch(data.latest?.capture_completed_at);
    if (data.status === "attention") return {visible: true, label: "Active · needs attention", message: "A collection was partial or failed, or an archive manifest could not be validated. Available receipts are retained; missing observations are not filled retrospectively."};
    if (!Number.isFinite(age) || age < -300000 || age > 8 * 3600000 || (data.latest && (!Number.isFinite(receiptAge) || receiptAge < -300000 || receiptAge > 8 * 3600000))) return {visible: true, label: "Active · receipts stale", message: "No recent, valid collection receipt is available. Scheduled jobs can be delayed or missed."};
    if (!data.latest) return {visible: true, label: "Active · awaiting capture", message: "The pilot is awaiting its first archived collection. It produces research inputs, not new betting selections."};
    return {visible: true, label: "Active · receipts current", message: "Forecast revisions and newly received prices are being archived for a separately specified future study. Coverage does not establish a betting edge."};
  }
  function dedupeResults(rows) {
    const seen = new Set();
    return items(rows).slice().sort((a, b) => (epoch(a.recorded_at) || Infinity) - (epoch(b.recorded_at) || Infinity)).filter(row => {
      if (!row.game_id || !row.candidate) return false;
      const key = `${row.model_version || "legacy"}:${row.candidate}:${row.game_id}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    }).sort((a, b) => (epoch(b.kickoff) || 0) - (epoch(a.kickoff) || 0));
  }
  function currentHedges(board, now = new Date()) {
    if (!health(board, now).usable) return [];
    const scan = board.market_opportunities || {};
    return items(scan.arbitrages).filter(row => row.both_recently_observed === true && epoch(row.kickoff) > now.getTime() &&
      [row.over, row.under].every(quote => { const age = now.getTime() - epoch((quote || {}).observed_at); return Number.isFinite(age) && age >= -300000 && age <= 3600000; }));
  }
  function quoteLabel(pick) {
    return pick.freshness_basis === "provider_full_state_receipt" ? "Provider observed" : "Market updated";
  }
  function evidenceState(board) {
    const quarantined = board.historical_evidence_status === "quarantined_market_provenance";
    const replacement = board.historical_evidence_status === "replacement_development_only";
    return {hideMetrics: quarantined, replacement, showWarning: quarantined || board.prior_historical_evidence_quarantined === true};
  }
  function safeUrl(value) {
    if (typeof value !== "string") return null;
    try { const url = new URL(value, "https://example.invalid/"); return ["https:", "http:"].includes(url.protocol) ? value : null; } catch (_) { return null; }
  }
  function start(doc) {
    let board = null;
    let research = null;
    let weatherRevisions = null;
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
      const ev = element("div", null, "pick-book"); ev.append(element("div", percent(pick.robust_ev, true), "pick-ev"), element("div", "Stressed modeled EV")); price.append(selection, ev);
      const metrics = element("div", null, "pick-metrics");
      [["Projected total", number(pick.projected_total)], ["Consensus total", number(pick.consensus_total)], ["Win probability", percent(pick.win_probability)]].forEach(([label, value]) => {const dl = element("dl"); dl.append(element("dt", label), element("dd", value)); metrics.append(dl);});
      const bottom = element("div", null, "pick-bottom");
      bottom.append(element("p", `${titleCase(pick.sportsbook)} · ${quoteLabel(pick)} ${dateLabel(pick.quote_time)}`));
      if (pick.freshness_basis === "provider_full_state_receipt") bottom.append(element("p", pick.market_updated_at ? `Market last changed ${dateLabel(pick.market_updated_at)} · Price acceptance unconfirmed` : "Market change time unavailable · Price acceptance unconfirmed"));
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
      else table($("upcoming-picks"), "Upcoming experimental selections", ["Matchup", "Selection", "Projection", "Win probability", "Stressed EV", "Quote"], upcoming.map(p => [
        cell(`${p.away_team} at ${p.home_team}`, dateLabel(p.kickoff)), cell(`${titleCase(p.side)} ${number(p.line)} (${odds(p.american_odds)})`, titleCase(p.sportsbook)), cell(number(p.projected_total), titleCase(p.candidate)), cell(percent(p.win_probability), `Push ${percent(p.push_probability)}`), cell(percent(p.robust_ev, true), "Model estimate", finite(p.robust_ev) && p.robust_ev > 0 ? "positive" : ""), cell(dateLabel(p.quote_time), quoteLabel(p))
      ]));
      renderMarketChecks(now);
      renderWeather(now);
    }
    function weatherCard(pick, index) {
      const card = element("article", null, "pick-card weather-card");
      const top = element("div", null, "pick-top");
      top.append(element("span", `WEATHER ${String(index + 1).padStart(2, "0")}`, "pick-rank"), element("span", "Fixed under rule", "candidate-name"));
      const game = element("div", null, "pick-game"), heading = element("h3");
      heading.append(doc.createTextNode(String(pick.away_team || "Away")), element("span", " at ", "versus"), doc.createTextNode(String(pick.home_team || "Home")));
      game.append(heading, element("div", dateLabel(pick.kickoff), "kickoff"));
      const price = element("div", null, "pick-price"), selection = element("div", `Under ${number(pick.line)}`, "pick-selection");
      selection.append(element("small", odds(pick.american_odds)));
      const stake = element("div", null, "pick-book"); stake.append(element("div", "1 paper unit", "weather-stake"), element("div", titleCase(pick.sportsbook))); price.append(selection, stake);
      card.append(top, game, price);
      const weather = weatherMeasurements(pick), metrics = element("div", null, "pick-metrics");
      [["Forecast wind", finite(weather.wind_mph) ? `${number(weather.wind_mph)} mph` : null], ["Temperature", finite(weather.temperature_f) ? `${number(weather.temperature_f)}°F` : null], ["Relative humidity", finite(weather.relative_humidity_pct) ? `${number(weather.relative_humidity_pct)}%` : null]].forEach(([label, value]) => { if (value !== null) {const dl = element("dl"); dl.append(element("dt", label), element("dd", value)); metrics.append(dl);} });
      if (metrics.children.length) card.append(metrics);
      const bottom = element("div", null, "pick-bottom weather-card-bottom");
      bottom.append(element("p", `Quote observed ${dateLabel(pick.quote_time)} · ${number(pick.decimal_odds, 3)} decimal`), element("p", "Fixed rule selection · Individual win probability and EV are not estimated."), element("p", "Paper only · Price acceptance unconfirmed · Display expires after one hour."));
      card.append(bottom); return card;
    }
    function renderWeather(now) {
      const strategy = board.weather_strategy || {}, evidence = strategy.evidence || {}, state = weatherHealth(board, now), picks = currentWeatherPicks(board, now);
      const interval = finite(evidence.roi_95_low) && finite(evidence.roi_95_high) ? `${percent(evidence.roi_95_low, true)} to ${percent(evidence.roi_95_high, true)}` : "not estimated";
      $("weather-evidence").textContent = finite(evidence.bets) && evidence.bets > 0 ? `2024–25 development replication: ${number(evidence.bets, 0)} hypothetical bets · ${number(evidence.wins, 0)} wins / ${number(evidence.losses, 0)} losses · ${percent(evidence.roi, true)} ROI · 95% week bootstrap interval ${interval}. All prices assumed ${odds(evidence.price_assumption)}; exact historical quote times are unverified.` : "Historical weather evidence has not been published. No profitable edge is asserted.";
      const robustness = research?.weather_shadow?.statistical_robustness;
      const matches = robustness && ["bets", "wins", "losses", "roi"].every(key => finite(evidence[key]) && Math.abs(evidence[key] - robustness.published_result?.[key]) < 1e-10);
      const family = items(robustness?.all_covered_week_cluster_t?.multiplicity_sensitivity).find(row => row.hypothetical_family_size === 4);
      const sensitivity = family?.bonferroni_95_family_interval;
      const precisePercent = value => `${value > 0 ? "+" : ""}${number(value * 100, 2)}%`;
      $("weather-robustness-note").textContent = matches && items(sensitivity).length === 2 && sensitivity.every(finite) ? `The four-candidate sensitivity interval is ${precisePercent(sensitivity[0])} to ${precisePercent(sensitivity[1])}${sensitivity[0] <= 0 && sensitivity[1] >= 0 ? ", crossing zero" : ""}; the full earlier research search count is unknown.` : "The full earlier research search count is unknown; the positive historical interval does not establish a future edge.";
      const weatherReplay = research?.weather_shadow?.archived_2026_replay, primaryReplay = weatherReplay?.cohorts?.two_book_the_odds_api, secondaryReplay = weatherReplay?.cohorts?.single_draftkings_espn_sensitivity;
      $("weather-2026-note").hidden = !primaryReplay;
      if (primaryReplay) $("weather-2026-note").textContent = `2026 replay, reconstructed after games: ${number(primaryReplay.rule?.bets, 0)} qualifying bets in ${number(primaryReplay.settled_price_eligible_covered_games, 0)} covered two-book games (${number(primaryReplay.calendar_week_blocks, 0)} calendar week${primaryReplay.calendar_week_blocks === 1 ? "" : "s"}). ${primaryReplay.rule?.bets === 0 ? "ROI is unavailable; this provides no strategy performance evidence." : "This is retrospective evidence, excluded from the forward record."}${secondaryReplay ? ` The overlapping one-book sensitivity has ${number(secondaryReplay.rule?.bets, 0)} qualifying bets; cohorts are not pooled.` : ""}`;
      $("weather-2026-links").replaceChildren(...items(weatherReplay?.links).filter(row => safeUrl(row.url)).map(row => link(`${row.name} ↗`, row.url)));
      renderNoaaWeather();
      $("weather-pick-count").textContent = String(picks.length);
      $("weather-as-of").textContent = Number.isFinite(epoch(strategy.as_of)) ? `Checked ${dateLabel(strategy.as_of)}` : "";
      $("weather-status").textContent = `${state.message}${state.usable ? ` ${number(strategy.forecast_count, 0)} games inspected · ${number(strategy.qualifying_count, 0)} qualifying selections at publication.` : ""}`;
      if (picks.length) $("weather-picks").replaceChildren(...picks.map(weatherCard));
      else empty($("weather-picks"), state.usable ? "No current weather selections" : "Weather selections paused", state.usable ? "No same-day pregame selection has a qualifying price observation from the past hour. The separate forward record remains below." : state.message);
      const p = strategy.performance || {}, settled = Number(p.wins || 0) + Number(p.losses || 0) + Number(p.pushes || 0), stats = $("weather-performance-stats");
      stats.replaceChildren();
      [["Settled paper bets", number(settled, 0), `${number(p.wins, 0)} W · ${number(p.losses, 0)} L · ${number(p.pushes, 0)} P`], ["Pending selections", number(p.pending, 0), "First qualifying entry per game"], ["Profit / loss", settled ? `${finite(p.profit_units) && p.profit_units > 0 ? "+" : ""}${number(p.profit_units, 2)} u` : "—", settled ? `Realized ROI ${percent(p.roi, true)}` : "No settled forward return"], ["95% ROI interval", settled && finite(p.roi_95_low) && finite(p.roi_95_high) ? `${percent(p.roi_95_low)} / ${percent(p.roi_95_high)}` : "—", "Forward record uncertainty"]].forEach(([label, value, detail]) => {const node = element("div", null, "stat"); node.append(element("span", label, "stat-label"), element("span", value, "stat-value"), element("span", detail, "stat-detail")); stats.append(node);});
      $("weather-performance-note").textContent = `${strategy.version || "Weather strategy"} · Separate ledger, one unit risked per paper selection. ${settled ? "Historical results above are excluded from this record." : "No settled forward evidence yet."} No actual bets are placed.`;
      const rows = dedupeResults(items(strategy.results).map(row => ({...row, candidate: row.candidate || strategy.version || "weather_under", model_version: row.model_version || strategy.version})));
      if (!rows.length) empty($("weather-results-table"), "", "No weather paper selections have been recorded yet.", true);
      else table($("weather-results-table"), "Separate weather strategy forward paper ledger", ["Matchup", "Recorded selection", "Recorded at", "Result", "Profit"], rows.map(row => [cell(`${row.away_team} at ${row.home_team}`, dateLabel(row.kickoff)), cell(`Under ${number(row.line)} (${odds(row.american_odds)})`, titleCase(row.sportsbook)), cell(dateLabel(row.recorded_at)), cell(titleCase(row.result || "pending")), cell(finite(row.profit_units) ? `${row.profit_units > 0 ? "+" : ""}${number(row.profit_units, 2)} u` : "—", null, row.profit_units > 0 ? "positive" : row.profit_units < 0 ? "negative" : "")]));
    }
    function renderNoaaWeather() {
      const study = research?.weather_shadow?.original_noaa_2021_2023;
      const available = study?.status === "separate_reused_development_forecast_source_replication" && study?.price_assumption === -110 && study?.no_live_policy_changes === true && study?.credible_executable_edge_established === false && study?.combined_with_other_weather_studies === false;
      $("noaa-weather-evidence").hidden = !available;
      for (const id of ["noaa-weather-note", "noaa-weather-coverage", "noaa-weather-years", "noaa-weather-uncertainty", "noaa-weather-sources", "noaa-weather-links"]) $(id).replaceChildren();
      if (!available) return;
      const pooled = study.pooled || {}, rule = pooled.weather_rule || {}, coverage = study.coverage || {};
      const ci = values => items(values).length === 2 && values.every(finite) ? `${percent(values[0], true, 2)} to ${percent(values[1], true, 2)}` : "unavailable";
      const pp = value => finite(value) ? `${number(value * 100, 2)} percentage points` : "unavailable";
      $("noaa-weather-note").textContent = `Original NOAA 2021–23: ${number(rule.bets, 0)} hypothetical Unders · ${number(rule.wins, 0)} W / ${number(rule.losses, 0)} L / ${number(rule.pushes, 0)} P · ${percent(rule.roi, true, 2)} ROI · descriptive 95% week interval ${ci(pooled.weather_rule_roi_95_week_bootstrap)}. This older-period test did not confirm a profitable edge. Prices assume −110; archive times are availability proxies. These reused-data results are separate from the 2024–25 study and change no live policy.`;
      $("noaa-weather-coverage").textContent = `${number(coverage.repaired_market_games, 0)} repaired market games → ${number(coverage.planned_games, 0)} planned → ${number(coverage.weather_available_games, 0)} with weather → ${number(coverage.weather_available_with_final_valid_market_games, 0)} with valid final scores and reference totals. ${number(coverage.selected_games, 0)} selected; ${number(coverage.nonselected_games, 0)} nonselected. Every exclusion is retained in the source report; groups are not combined with other weather studies.`;
      const years = [["Pooled", pooled], ...Object.entries(study.by_season || {}).sort(([a], [b]) => a.localeCompare(b))];
      table($("noaa-weather-years"), "Separate NOAA development results for every planned year", ["Period", {label:"Covered",numeric:true}, "Rule W–L–P", {label:"Rule ROI",numeric:true}, "95% week interval", "99% week interval", {label:"All-Under ROI",numeric:true}], years.map(([label, row]) => [cell(label), cell(number(row.games, 0), null, "numeric"), cell(`${number(row.weather_rule?.wins, 0)}–${number(row.weather_rule?.losses, 0)}–${number(row.weather_rule?.pushes, 0)}`, `${number(row.weather_rule?.bets, 0)} hypothetical bets`), cell(percent(row.weather_rule?.roi, true, 2), null, "numeric"), cell(ci(row.weather_rule_roi_95_week_bootstrap)), cell(ci(row.weather_rule_roi_99_week_bootstrap)), cell(percent(row.all_under_same_weather_coverage?.roi, true, 2), null, "numeric")]));
      $("noaa-weather-uncertainty").textContent = `Pooled selected-minus-all-Under return: ${pp(pooled.rule_minus_all_under_roi)}; paired-week 95% interval ${ci(pooled.rule_minus_all_under_roi_95_paired_week_bootstrap)}, 99% ${ci(pooled.rule_minus_all_under_roi_99_paired_week_bootstrap)}. Active-week cluster-t intervals: 95% ${ci(pooled.active_week_cluster_t?.interval_95)}, 99% ${ci(pooled.active_week_cluster_t?.interval_99)} across ${number(pooled.active_week_cluster_t?.active_weeks, 0)} active weeks. Leave-one-week-out ROI: ${ci(pooled.leave_one_week_out_roi_range)}. ${number(pooled.bootstrap_draws_with_no_rule_bets, 0)} of ${number(pooled.bootstrap_draws, 0)} bootstrap draws had no selected stakes. Intervals are descriptive; the full earlier search count is unknown. ${study.timing_limitation || "Archive times are availability proxies, not certified publication receipts."}`;
      table($("noaa-weather-sources"), "Every repaired market source in the separate NOAA replication", ["Market source", {label:"Covered",numeric:true}, "Rule W–L–P", {label:"Rule ROI",numeric:true}, {label:"Nonselected Under ROI",numeric:true}], Object.entries(study.by_market_source || {}).sort(([a], [b]) => a.localeCompare(b)).map(([source, row]) => [cell(titleCase(source)), cell(number(row.games, 0), null, "numeric"), cell(`${number(row.weather_rule?.wins, 0)}–${number(row.weather_rule?.losses, 0)}–${number(row.weather_rule?.pushes, 0)}`, `${number(row.weather_rule?.bets, 0)} hypothetical bets`), cell(percent(row.weather_rule?.roi, true, 2), null, "numeric"), cell(percent(row.nonselected_games?.roi, true, 2), `${number(row.nonselected_games?.bets, 0)} nonselected`, "numeric")]));
      items(study.links).forEach(report => { if (safeUrl(report.url)) $("noaa-weather-links").append(link(`${report.name || "NOAA report"} ↗`, report.url)); });
    }
    function renderMarketChecks(now) {
      const scan = board.market_opportunities || {}, rows = currentHedges(board, now);
      $("market-checks-summary").textContent = scan.as_of ? `${number(scan.games_with_two_books, 0)} games with two books · ${number(scan.hedge_pairs_evaluated, 0)} pairs checked · ${dateLabel(scan.as_of)}` : "Cross-book scan has not been published.";
      const columns = ["Matchup", "Over leg", "Under leg", "Quoted payoff range", "Execution"];
      const mapped = row => [cell(row.matchup || "Matchup unavailable", dateLabel(row.kickoff)), cell(`${row.over?.book || "Unknown book"} · Over ${number(row.over?.line)}`, `${number(row.over?.decimal_odds, 3)} decimal · ${percent(row.over_stake_fraction)} stake`), cell(`${row.under?.book || "Unknown book"} · Under ${number(row.under?.line)}`, `${number(row.under?.decimal_odds, 3)} decimal · ${percent(row.under_stake_fraction)} stake`), cell(`${percent(row.worst_case_roi, true)} to ${percent(row.best_case_roi, true)}`, "Conditional mathematical scenario"), cell("Unconfirmed", "No bets placed")];
      if (!rows.length) empty($("market-checks-table"), "", "No current positive-floor combination is available to display. Comparisons expire one hour after the quotes were observed.", true);
      else table($("market-checks-table"), "Unconfirmed positive-floor quote combinations; execution and settlement assumptions apply", columns, rows.map(mapped));
      const other = health(board, now).usable ? items(scan.hedges).filter(row => epoch(row.kickoff) > now.getTime()).slice(0,5) : [];
      $("market-checks-details").hidden = other.length === 0;
      if (other.length) table($("market-hedges-table"), "Other quoted hedge scenarios, which can lose money", columns, other.map(mapped));
      else $("market-hedges-table").replaceChildren();
    }
    function renderCandidates() {
      const candidates = items(board.candidates);
      const evidence = evidenceState(board), quarantined = evidence.hideMetrics;
      $("historical-alert").hidden = !evidence.showWarning;
      $("historical-alert-title").textContent = evidence.replacement ? "Earlier historical evidence remains quarantined" : "Historical evidence quarantined";
      $("historical-alert-copy").textContent = evidence.replacement ? "The previous archive included in-play odds and its results remain withdrawn. The comparison below uses replacement pregame-provider data. Prices are assumed at −110 and exact closing times are unverified; profitability remains unproven." : "The previous market archive includes in-play odds. Historical model comparisons and betting returns are withdrawn pending clean pregame data. Current projections remain experimental.";
      $("model-version").textContent = board.model_version ? `Version ${board.model_version}` : "";
      $("evidence-message").textContent = quarantined ? "Historical market provenance failed an audit: some archived totals are live in-game values. Earlier error scores and hypothetical ROI cannot establish a pregame edge and are withheld here. Forward records remain separate." : evidence.replacement ? "Replacement-data development results. These years have already been used in research; they are not an untouched test. The stated intervals are descriptive and do not account for all previous model searches. Review annual results and the independent forward record." : board.evidence_status === "research_only" ? "Research only. Historical performance and modeled EV do not establish a live betting edge. Candidate selection, price timing, and the independent forward record remain part of the evaluation." : "Profitability requires a credible, time-ordered evaluation and an independent forward record. Review the intervals and sample sizes below.";
      if (quarantined) empty($("candidate-table"), "", "Historical metrics withheld: rebuilding the comparison against verified pregame market data. Prior reports remain available as quarantined research artifacts.", true);
      else if (!candidates.length) empty($("candidate-table"), "", "Candidate evaluation metrics have not been published.", true);
      else table($("candidate-table"), "Historical development candidate comparison", ["Candidate", {label: "Games", numeric: true}, {label: "MAE", numeric: true}, {label: "RMSE", numeric: true}, {label: "Signals", numeric: true}, {label: "ROI at −110", numeric: true}, "Descriptive 95% interval", {label: "Brier", numeric: true}], candidates.map(m => [
        cell(titleCase(m.candidate), m.status || "Evaluation status unavailable"), cell(number(m.games, 0), null, "numeric"), cell(number(m.mae, 2), null, "numeric"), cell(number(m.rmse, 2), null, "numeric"), cell(number(m.bets, 0), null, "numeric"), cell(percent(m.roi, true), null, "numeric"), cell(finite(m.roi_95_low) && finite(m.roi_95_high) ? `${percent(m.roi_95_low)} to ${percent(m.roi_95_high)}` : "Not estimated"), cell(number(m.brier, 3), null, "numeric")
      ]));
      const links = $("research-links"); links.replaceChildren();
      items(board.reports).forEach(report => { if (safeUrl(report.url)) links.append(link(`${report.name || "Research report"} ↗`, report.url)); });
      const forward = items(board.forecast_performance);
      $("forward-forecast-count").textContent = `(${forward.length} candidate versions)`;
      if (!forward.length) empty($("forward-forecast-table"), "", "No forward forecast evaluation published yet.", true);
      else table($("forward-forecast-table"), "Forward forecast performance including abstentions", ["Candidate", {label:"Settled",numeric:true}, {label:"Pending",numeric:true}, {label:"MAE",numeric:true}, {label:"RMSE",numeric:true}, {label:"Brier",numeric:true}, {label:"Log loss",numeric:true}], forward.map(m => [
        cell(titleCase(m.candidate), `${m.model_version || "Legacy version"} · ${number(m.forecast_entries, 0)} forecasts · ${number(m.abstentions, 0)} abstentions`), cell(number(m.games, 0), null, "numeric"), cell(number(m.pending, 0), null, "numeric"), cell(number(m.mae, 2), null, "numeric"), cell(number(m.rmse, 2), null, "numeric"), cell(number(m.brier, 3), `${number(m.probability_scoring_games, 0)} scored`, "numeric"), cell(number(m.log_loss, 3), null, "numeric")
      ]));
      const candidatesSeen = [...new Set(items(board.forecasts).map(row => row.candidate).filter(Boolean))].sort();
      $("candidate-filter").replaceChildren(element("option", "All candidates")); $("candidate-filter").firstChild.value = "all";
      candidatesSeen.forEach(candidate => { const option = element("option", titleCase(candidate)); option.value = candidate; $("candidate-filter").append(option); });
      const primary = (board.performance || {}).candidate || "opponent_adjusted_ridge";
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
        cell(`${row.away_team} at ${row.home_team}`, dateLabel(row.kickoff)), cell(titleCase(row.candidate), row.probability_basis), cell(number(row.projected_total)), cell(number(row.line), `${titleCase(row.side)} ${odds(row.american_odds)}`), cell(percent(row.expected_value, true)), cell(row.eligible ? "Qualified at publication" : "No selection", items(row.flags).map(titleCase).join(" · "))
      ]));
    }
    function renderCalibrationStudy() {
      const study = research?.calibration_research;
      const available = study?.status === "reused_historical_development_only" && study?.configuration_count === 6 && study?.roi_evaluated === false && study?.credible_new_betting_edge === false && study?.live_policy_changed === false;
      $("calibration-study").hidden = !available;
      $("calibration-study-links").replaceChildren();
      if (!available) { $("calibration-study-note").textContent = ""; return; }
      const selected = study.selected_configuration === "opponent_adjusted_ridge:raw" ? "raw opponent-adjusted ridge" : titleCase(study.selected_configuration);
      const comparison = study.all_ridge_variants_worse_than_raw_market_2025 ? " All three ridge variants scored worse than the raw market reference on 2025 market log loss." : " All six 2025 configurations are reported together.";
      $("calibration-study-note").textContent = `Six fixed configurations tested probability scores on reused historical data. The 2022–2024 selection chose ${selected}.${comparison} No ROI test was run and no new edge was established; no live candidate was added, and the four-policy forward study is unchanged.`;
      items(study.links).forEach(report => { if (safeUrl(report.url)) $("calibration-study-links").append(link(`${report.name || "Calibration report"} ↗`, report.url)); });
    }
    function renderOrdinaryStudy() {
      const study = research?.ordinary_model_research;
      const available = study?.status === "reused_development_point_prediction_study" && study?.configuration_count === 4 && study?.predictor_count === 58 && study?.live_policy_changes === false && study?.roi_evaluated === false && study?.probabilities_evaluated === false && study?.credible_executable_edge_established === false;
      $("ordinary-study").hidden = !available;
      for (const id of ["ordinary-study-note", "ordinary-study-coverage", "ordinary-study-table", "ordinary-study-years", "ordinary-study-sources", "ordinary-study-links"]) $(id).replaceChildren();
      if (!available) return;
      const names = {market_only:"Market reference",opponent_adjusted_ridge:"Existing opponent-adjusted ridge",ordinary_ridge:"Ordinary-stat ridge",ordinary_hgb:"Ordinary-stat tree"}, order = items(study.candidate_order);
      const selection = study.selection_2021_2024 || {}, last = study.reused_2025 || {}, provenance = study.feature_provenance || {};
      const outcome = study.new_models_higher_mse_than_both_references_in_both_periods ? " Both new models had higher MSE than both references in each period; this study did not justify replacing the current model." : "";
      $("ordinary-study-note").textContent = `Two models using 58 prior-game and context predictors were compared with the market reference and existing ridge: four fixed configurations. Selection on 2021–2024 MSE chose ${names[study.selected_on_2021_2024] || titleCase(study.selected_on_2021_2024)}.${outcome} All four 2025 results remain a reused-development check. No probabilities, ROI test or live policy changes were produced.`;
      $("ordinary-study-coverage").textContent = `${number(provenance.counts?.games, 0)} repaired feature rows · ${number(selection.games, 0)} common 2021–2024 selection games · ${number(last.games, 0)} common 2025 check games. Prior observations use a kickoff-plus-six-hours availability proxy and a weekly cutoff; original publication timing remains unverified.`;
      table($("ordinary-study-table"), "All four fixed ordinary-stat configurations in both evaluation periods", ["Configuration", {label:"2021–24 MSE",numeric:true}, {label:"2025 MSE",numeric:true}, {label:"2025 RMSE",numeric:true}, {label:"2025 MAE",numeric:true}], order.map(name => {const a=selection.configurations?.[name] || {}, b=last.configurations?.[name] || {}; return [cell(names[name] || titleCase(name), name === study.selected_on_2021_2024 ? "Selected using 2021–2024 only" : null), cell(number(a.mse, 3), null, "numeric"), cell(number(b.mse, 3), null, "numeric"), cell(number(b.rmse, 3), null, "numeric"), cell(number(b.mae, 3), null, "numeric")];}));
      table($("ordinary-study-years"), "Every annual ordinary-stat MSE comparison on identical games", ["Year", {label:"Games",numeric:true}, ...order.map(name=>({label:`${names[name] || titleCase(name)} MSE`,numeric:true}))], Object.entries(study.by_season || {}).sort(([a],[b])=>a.localeCompare(b)).map(([year,row])=>[cell(year),cell(number(row.games,0),null,"numeric"),...order.map(name=>cell(number(row.configurations?.[name]?.mse,3),null,"numeric"))]));
      $("ordinary-study-sources").textContent = `All-period source counts: ${Object.entries(study.by_market_source || {}).map(([source,row])=>`${titleCase(source)} ${number(row.games,0)}`).join("; ")}. ${study.by_market_source_scope || "Source and era effects may be entangled."} Detailed scores for every source and period are linked below; no favorable source is selected.`;
      items(study.links).forEach(report => { if (safeUrl(report.url)) $("ordinary-study-links").append(link(`${report.name || "Ordinary-stat report"} ↗`, report.url)); });
    }
    function renderArchivedReplay() {
      const replay = research?.archived_2026_scoring_replay, cohorts = Object.entries(replay?.cohorts || {});
      const available = replay?.prospective_model_performance === false && replay?.exact_0630_replay === false && cohorts.length > 0;
      $("archived-replay").hidden = !available;
      if (!available) return;
      const snapshotMap = new Map(), rows = [], order = ["opponent_adjusted_ridge", "opponent_adjusted_structural", "market_price_reference"];
      cohorts.forEach(([cohort, values]) => {
        items(values.snapshots).forEach(row => snapshotMap.set(row.source_sha256 || row.observed_at, row));
        Object.entries(values.positions || {}).sort(([a], [b]) => order.indexOf(a) - order.indexOf(b)).forEach(([candidate, m]) => rows.push([
          cell(titleCase(candidate), `${m.model_version || replay.version} · ${titleCase(cohort)}`), cell(number(m.bets, 0), null, "numeric"), cell(`${number(m.wins, 0)}–${number(m.losses, 0)}–${number(m.pushes, 0)}`), cell(finite(m.profit_units) ? `${m.profit_units > 0 ? "+" : ""}${number(m.profit_units, 2)} u` : "—", null, "numeric"), cell(percent(m.roi, true, 2), "Recorded-price reconstruction", "numeric"), cell(number(m.pending, 0), null, "numeric")
        ]));
      });
      const snapshots = [...snapshotMap.values()].sort((a, b) => epoch(a.observed_at) - epoch(b.observed_at));
      $("archived-replay-summary").textContent = `${snapshots.length} actual archive snapshots · Reconstructed ${dateLabel(replay.evaluated_at)} · Hypothetical one-unit stakes at recorded prices; acceptance unverified.`;
      table($("archived-replay-table"), "Retrospective 2026 reconstruction, excluded from prospective results", ["Candidate", {label:"Settled",numeric:true}, "W–L–P", {label:"Profit",numeric:true}, {label:"ROI",numeric:true}, {label:"Pending",numeric:true}], rows);
      $("archived-replay-links").replaceChildren(...items(replay.links).filter(row => safeUrl(row.url)).map(row => link(`${row.name} ↗`, row.url)));
      $("archived-replay-times").replaceChildren(...snapshots.map(row => element("li", `${dateLabel(row.observed_at, {second:"2-digit"})} · ${number(row.games, 0)} games in snapshot`)));
    }
    function renderPerformance() {
      const p = board.performance || {}, settled = Number(p.wins || 0) + Number(p.losses || 0) + Number(p.pushes || 0);
      const stats = $("performance-stats"); stats.replaceChildren();
      [["Settled paper bets", number(settled, 0), `${number(p.wins, 0)} W · ${number(p.losses, 0)} L · ${number(p.pushes, 0)} P`], ["Profit / loss", settled ? `${finite(p.profit_units) && p.profit_units > 0 ? "+" : ""}${number(p.profit_units, 2)} u` : "—", "One unit per paper selection"], ["Realized ROI", settled ? percent(p.roi, true) : "—", "Return on flat one-unit stakes"], ["95% ROI interval", settled && finite(p.roi_95_low) && finite(p.roi_95_high) ? `${percent(p.roi_95_low)} / ${percent(p.roi_95_high)}` : "—", "Uncertainty in forward returns"]].forEach(([label, value, detail]) => {
        const node = element("div", null, "stat"); node.append(element("span", label, "stat-label"), element("span", value, "stat-value"), element("span", detail, "stat-detail")); stats.append(node);
      });
      $("performance-note").textContent = `Primary candidate: ${titleCase(p.candidate || "opponent_adjusted_ridge")} · ${p.model_version || board.model_version || "Version unavailable"}. ${settled ? "Paper results use one recorded selection per version, candidate and game. Returns may differ from executable betting results." : "No settled forward record yet. A profitable live edge has not been demonstrated."}`;
      const rows = dedupeResults(board.results);
      if (!rows.length) { empty($("results-table"), "", "No forward ledger rows have been published yet.", true); return; }
      table($("results-table"), "Deduplicated forward paper selections", ["Matchup", "Candidate", "Recorded selection", "Result", "Profit"], rows.map(row => [
        cell(`${row.away_team} at ${row.home_team}`, dateLabel(row.kickoff)), cell(titleCase(row.candidate), `${row.model_version || "Legacy version"}${row.recorded_at ? ` · Recorded ${dateLabel(row.recorded_at)}` : ""}`), cell(`${titleCase(row.side)} ${number(row.line)} (${odds(row.american_odds)})`), cell(titleCase(row.result || "pending")), cell(finite(row.profit_units) ? `${row.profit_units > 0 ? "+" : ""}${number(row.profit_units, 2)} u` : "—", null, row.profit_units > 0 ? "positive" : row.profit_units < 0 ? "negative" : "")
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
    function renderWeatherRevisions(now) {
      const state = weatherRevisionHealth(weatherRevisions, now);
      $("weather-revision-pilot").hidden = !state.visible;
      if (!state.visible) return;
      $("weather-revision-label").textContent = state.label;
      $("weather-revision-message").textContent = state.message;
      const latest = weatherRevisions.latest, counts = latest?.counts || {};
      $("weather-revision-counts").textContent = `${number(weatherRevisions.archived_runs, 0)} archived collection runs · ${number(weatherRevisions.partial_or_failed_runs, 0)} partial or failed runs · ${number(weatherRevisions.invalid_manifests, 0)} invalid manifests. ${latest ? `Latest receipt: ${dateLabel(latest.capture_completed_at)} · ${titleCase(latest.status)}. Latest cohort: ${number(counts.cohort_games, 0)} games · ${number(counts.weather_available_games, 0)} with weather · ${number(counts.two_book_games, 0)} with two books · ${number(counts.paired_games, 0)} with weather and at least one same-book quote pair${finite(counts.paired_two_book_games) ? ` · ${number(counts.paired_two_book_games, 0)} with weather and both books` : ""} · ${number(counts.failed_requests, 0)} failed requests.` : "No archived receipt yet."} Repeated collections are observations, not independent games or bets.`;
      $("weather-revision-links").replaceChildren(link("Public status JSON", "data/weather-revisions.json"));
      items(weatherRevisions.links).filter(item => safeUrl(item.url)).forEach(item => $("weather-revision-links").append(link(`${item.name} ↗`, item.url)));
    }
    function render(data) {
      board = data;
      $("edition-date").textContent = new Intl.DateTimeFormat("en-US", {timeZone: ZONE, weekday: "long", month: "long", day: "numeric", year: "numeric"}).format(new Date());
      renderPicks(new Date()); renderCandidates(); renderCalibrationStudy(); renderOrdinaryStudy(); renderArchivedReplay(); renderPerformance(); renderTransparency();
    }
    $("candidate-filter").addEventListener("change", renderForecasts);
    fetch(`data/board.json?refresh=${Date.now()}`, {cache: "no-store"}).then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }).then(render).catch(() => render({schema_version: 1, generated_at: new Date().toISOString(), date: dateKey(), status: "unavailable", message: "The published data file could not be loaded. No selections are being shown. Try refreshing the page."}));
    fetch(`data/research.json?refresh=${Date.now()}`, {cache: "no-store"}).then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }).then(data => {research = data; if (board) {renderWeather(new Date()); renderCalibrationStudy(); renderOrdinaryStudy(); renderArchivedReplay();}}).catch(() => {});
    fetch(`data/weather-revisions.json?refresh=${Date.now()}`, {cache: "no-store"}).then(response => { if (!response.ok) throw new Error(`HTTP ${response.status}`); return response.json(); }).then(data => {weatherRevisions = data; renderWeatherRevisions(new Date());}).catch(() => {});
    setInterval(() => { if (board) renderPicks(new Date()); if (weatherRevisions) renderWeatherRevisions(new Date()); }, 60000);
  }
  return {dateKey, health, currentPicks, currentWeatherPicks, weatherHealth, weatherMeasurements, weatherRevisionHealth, currentHedges, quoteLabel, evidenceState, dedupeResults, safeUrl, percent, number, start};
});
