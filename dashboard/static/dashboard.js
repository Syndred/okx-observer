(() => {
  "use strict";

  const STAGES = [
    {
      key: "entry_confirmed",
      label: "回踩确认",
      eventField: "next_executable_at",
    },
    {
      key: "wait_first_pullback",
      label: "等待第一次回踩",
      eventField: "fifteen_minute_breakout_at",
    },
    {
      key: "mature_15m_coil",
      label: "15m 成熟密集",
      eventField: "fifteen_minute_window_end",
    },
    {
      key: "forming_15m_coil",
      label: "正在形成密集",
      eventField: "fifteen_minute_window_end",
    },
  ];
  const OBSERVATION_STAGES = new Set(["mature_15m_coil", "forming_15m_coil"]);

  const REASON_LABELS = new Map([
    ["first_pullback_held", "第一次回踩收回，等待执行"],
    ["fifteen_minute_breakout_waiting_pullback", "15m 突破，等待首次回踩"],
    ["fifteen_minute_coil_ready", "15m 已形成成熟密集"],
    ["fifteen_minute_coil_forming", "15m 正在收拢形成"],
    ["fifteen_minute_breakout_waiting_for_pullback", "15m 突破，等待首次回踩"],
    ["fifteen_minute_breakout_waiting_pullback", "15m 突破，等待首次回踩"],
  ]);
  const STAGE_CHIPS = new Map([
    ["entry_confirmed", "✅回踩确认"],
    ["wait_first_pullback", "⏳等回踩"],
    ["mature_15m_coil", "🌀成熟密集"],
    ["forming_15m_coil", "🌱正在收拢"],
  ]);
  const FOUR_HOUR_CHIPS = new Map([
    ["mature_coil", "🌀4H密集"],
    ["forming_coil", "🌱4H收拢"],
    ["trend_aligned", "📈4H同向"],
    ["daily_trend_only", "4H仅日线"],
    ["unknown", "❓4H不明"],
    ["opposite", "📉4H反向"],
    ["密集成熟", "🌀4H密集"],
    ["正在收拢", "🌱4H收拢"],
    ["高周期同向", "📈4H同向"],
    ["仅日线有趋势", "4H仅日线"],
    ["高周期不明确", "❓4H不明"],
    ["高周期反向", "📉4H反向"],
  ]);
  const RISK_CHIPS = new Map([
    ["均线已经发散", "⚠发散"],
    ["expanded_risk", "⚠发散"],
    ["高周期尚未确认", "⚠4H未确认"],
    ["higher_timeframe_unconfirmed", "⚠4H未确认"],
    ["日线方向不明确", "⚠日线不明"],
    ["daily_unknown", "⚠日线不明"],
    ["高周期方向相反", "⚠反向"],
    ["opposite", "⚠反向"],
    ["其他风险", "⚠风险"],
  ]);
  const PLAYBOOK_CHIPS = new Map([
    ["pullback_20", "🎯回踩20"],
    ["coil_retest", "📐密集回测"],
    ["回踩20", "🎯回踩20"],
    ["密集回测", "📐密集回测"],
  ]);
  const COUNTERTREND_CHIPS = new Map([
    ["bottoming_observation", "🔻抄底"],
    ["topping_observation", "🔺摸顶"],
    ["抄底观察", "🔻抄底"],
    ["摸顶观察", "🔺摸顶"],
  ]);
  const PROGRESS_STAGES = ["daily", "four_hour", "fifteen_minute"];
  const PROGRESS_LABELS = new Map([
    ["daily", "日线资格"],
    ["four_hour", "4H 趋势"],
    ["fifteen_minute", "15m 机会"],
  ]);
  const MAX_POLL_ERRORS = 3;

  let pollTimer = null;
  let pollInFlight = false;
  let pollErrorCount = 0;
  let hasDashboard = false;

  const get = (id) => document.getElementById(id);

  function formatBeijingClock(date) {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
      hourCycle: "h23",
    }).formatToParts(date);
    const values = Object.fromEntries(
      parts
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value]),
    );
    return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
  }

  function formatBackendEventTime(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?$/.test(String(value))) {
      return String(value).slice(0, 16);
    }
    const parsed = new Date(String(value));
    if (Number.isNaN(parsed.getTime())) return String(value);
    return formatBeijingClock(parsed);
  }

  function updateClock() {
    const clock = get("beijing-clock");
    if (!clock) return;
    const text = formatBeijingClock(new Date());
    clock.textContent = text;
    clock.setAttribute("datetime", text.replace(" ", "T") + "+08:00");
  }

  function safeValue(value) {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number") {
      if (!Number.isFinite(value)) return "—";
      return Number.isInteger(value) ? String(value) : value.toFixed(1);
    }
    return String(value);
  }

  function formatPrice(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    if (number === 0) return "0";
    const exponent = Math.floor(Math.log10(Math.abs(number)));
    const decimals = Math.min(8, Math.max(0, 5 - exponent));
    const rounded = Number(number.toFixed(decimals));
    if (rounded === 0) return String(Number(number.toPrecision(6)));
    return String(rounded);
  }

  function reasonText(reason) {
    if (!reason) return "未提供状态说明";
    return REASON_LABELS.get(String(reason).toLowerCase()) || "状态待确认";
  }

  function directionClass(direction) {
    const normalized = String(direction || "neutral").toLowerCase();
    if (normalized === "long" || normalized === "short") {
      return `direction-${normalized}`;
    }
    return "direction-neutral";
  }

  function appendText(parent, tagName, className, value, formatter = safeValue) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    node.textContent = formatter(value);
    parent.append(node);
    return node;
  }

  function riskLabelList(value) {
    if (Array.isArray(value)) return value.filter(Boolean).map(String);
    if (value === null || value === undefined || value === "") return [];
    const text = String(value).trim();
    if (!text) return [];
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) return parsed.filter(Boolean).map(String);
    } catch (_error) {
      // CSV readers may leave list diagnostics as a comma-separated string.
    }
    return text
      .replace(/^\[|\]$/g, "")
      .split(",")
      .map((item) => item.trim().replace(/^['"]|['"]$/g, ""))
      .filter(Boolean);
  }

  function removeContextRiskDuplicate(item) {
    const context = String(item.four_hour_context || "").trim().toLowerCase();
    const state = String(item.four_hour_state || "").trim().toLowerCase();
    const contextLabel = String(item.four_hour_context_label || "").trim();
    if (
      context !== "opposite"
      && state !== "opposite"
      && contextLabel !== "高周期反向"
    ) return;
    item.risk_labels_display = riskLabelList(item.risk_labels_display).filter(
      (label) => label !== "高周期方向相反" && label !== "opposite",
    );
  }

  function hasDisplayValue(value) {
    return value !== null && value !== undefined && value !== "";
  }

  function shouldRenderDailyBias(item) {
    const direction = String(item.direction || "").trim().toLowerCase();
    const daily = String(item.daily_bias || item.daily_direction || "")
      .trim()
      .toLowerCase();
    if (direction && daily) return direction !== daily;
    return String(item.direction_label || "") !== String(item.daily_bias_label || "");
  }

  function boundedReadiness(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.min(100, Math.max(0, Math.round(number)));
  }

  function shortInstrument(instrument) {
    const text = String(instrument || "").trim();
    return text.replace(/-USDT-SWAP$/i, "") || "—";
  }

  function stageChip(item, stage) {
    const key = String((item && item.stage) || (stage && stage.key) || "");
    return STAGE_CHIPS.get(key) || item.stage_label || (stage && stage.label) || "状态待确认";
  }

  function fourHourChip(item) {
    const code = String(item.four_hour_context || "").trim().toLowerCase();
    const label = String(item.four_hour_context_label || "").trim();
    return FOUR_HOUR_CHIPS.get(code) || FOUR_HOUR_CHIPS.get(label) || "";
  }

  function dailyChip(item) {
    if (!shouldRenderDailyBias(item)) return "";
    const label = String(item.daily_bias_label || "");
    if (label.includes("多")) return "日线多";
    if (label.includes("空")) return "日线空";
    return "日线不明";
  }

  function scoreValue(item) {
    const value =
      item.fifteen_minute_state_quality
      ?? item["15m_state_quality"]
      ?? item.fifteen_minute_coil_score;
    if (!hasDisplayValue(value)) return "";
    return `15m ${safeValue(value)}`;
  }

  function latestPriceValue(item) {
    if (hasDisplayValue(item.latest_price)) return item.latest_price;
    if (hasDisplayValue(item.current_close)) return item.current_close;
    return null;
  }

  function riskChips(item) {
    removeContextRiskDuplicate(item);
    return riskLabelList(item.risk_labels_display)
      .map((label) => RISK_CHIPS.get(label) || `⚠${label}`)
      .filter(Boolean);
  }

  function countertrendChip(item) {
    const code = String(item.countertrend_signal || "").trim();
    const label = String(item.countertrend_signal_label || "").trim();
    if (!code && !label) return "";
    return (
      COUNTERTREND_CHIPS.get(code)
      || COUNTERTREND_CHIPS.get(label)
      || label
    );
  }

  function playbookChip(item) {
    const code = String(item.playbook || "").trim();
    const label = String(item.playbook_label || "").trim();
    return PLAYBOOK_CHIPS.get(code) || PLAYBOOK_CHIPS.get(label) || "";
  }

  function plannedRrText(item) {
    const number = Number(item.planned_rr);
    if (!Number.isFinite(number) || number <= 0) return "";
    return `${number.toFixed(1)}R`;
  }

  function formatChange(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    const absolute = Math.abs(number);
    const decimals = absolute >= 10 ? 1 : 2;
    const formatted = number.toFixed(decimals);
    if (number > 0) return `+${formatted}%`;
    return `${formatted}%`;
  }

  function changeTone(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number === 0) return "is-flat";
    return number > 0 ? "is-up" : "is-down";
  }

  function appendChange(parent, item, className) {
    const change = appendText(
      parent,
      "strong",
      `${className} ${changeTone(item.change_24h_pct)}`,
      item.change_24h_pct,
      formatChange,
    );
    const symbol = shortInstrument(item.instrument);
    change.setAttribute("aria-label", `${symbol} 近24小时 ${formatChange(item.change_24h_pct)}`);
    return change;
  }

  function factsLine(item, stage) {
    const tags = [stageChip(item, stage)];
    const playbook = playbookChip(item);
    if (playbook) tags.push(playbook);
    const score = scoreValue(item);
    if (score) tags.push(score);
    const fourHour = fourHourChip(item);
    if (fourHour) tags.push(fourHour);
    const daily = dailyChip(item);
    if (daily) tags.push(daily);
    if (
      !OBSERVATION_STAGES.has(stage.key)
      && hasDisplayValue(item.initial_stop_price)
    ) {
      tags.push(`SL ${formatPrice(item.initial_stop_price)}`);
    }
    if (hasDisplayValue(item.target_price) || hasDisplayValue(item.odds_target_price)) {
      const target = hasDisplayValue(item.target_price)
        ? item.target_price
        : item.odds_target_price;
      tags.push(`TP ${formatPrice(target)}`);
    }
    return tags;
  }

  function flagsLine(item) {
    const tags = [];
    const countertrend = countertrendChip(item);
    if (countertrend) tags.push(countertrend);
    tags.push(...riskChips(item));
    return tags;
  }

  function cardTooltip(item, stage) {
    const parts = [
      item.stage_label || stage.label,
      item.direction_label,
      item.fifteen_minute_state_label,
      item.four_hour_context_label,
      item.entry_readiness_label,
      reasonText(item.reason),
    ];
    if (item.countertrend_signal_detail) {
      parts.push(item.countertrend_signal_detail);
    }
    const rr = plannedRrText(item);
    if (rr) parts.push(`计划赔率 ${rr}`);
    if (hasDisplayValue(item.target_price)) {
      parts.push(`目标 ${formatPrice(item.target_price)}`);
    }
    if (hasDisplayValue(item.change_24h_pct)) {
      parts.push(`24h ${formatChange(item.change_24h_pct)}`);
    }
    if (item.playbook_label) parts.push(item.playbook_label);
    if (!OBSERVATION_STAGES.has(stage.key)) {
      const breakoutAt =
        item.fifteen_minute_breakout_at || item.four_hour_breakout_at;
      if (breakoutAt) {
        parts.push(`突破时间 ${formatBackendEventTime(breakoutAt)}`);
      }
      if (hasDisplayValue(item.initial_stop_price)) {
        parts.push(`初始止损 ${formatPrice(item.initial_stop_price)}`);
      }
    }
    return parts.filter(Boolean).join(" · ");
  }

  function renderCard(item, stage) {
    const card = document.createElement("article");
    card.className = `candidate-card ${directionClass(item.direction)}`;
    card.setAttribute("title", cardTooltip(item, stage));
    card.setAttribute("aria-label", item.direction_label || "方向待定");

    const main = document.createElement("div");
    main.className = "candidate-main";

    const title = document.createElement("h4");
    title.className = "candidate-instrument";
    appendText(title, "span", "candidate-symbol", shortInstrument(item.instrument));
    title.setAttribute("title", safeValue(item.instrument));
    main.append(title);

    const price = latestPriceValue(item);
    const priceNode = appendText(
      main,
      "strong",
      "candidate-price",
      price === null ? "—" : price,
      price === null ? safeValue : formatPrice,
    );
    priceNode.setAttribute("aria-label", "最新价");
    appendChange(main, item, "candidate-change");

    const rrText = plannedRrText(item);
    const readiness = boundedReadiness(item.entry_readiness);
    const metric = appendText(
      main,
      "strong",
      rrText ? "readiness-value odds-value" : "readiness-value",
      rrText || String(readiness),
    );
    metric.setAttribute("aria-valuenow", rrText ? String(Number(item.planned_rr)) : String(readiness));
    metric.setAttribute(
      "aria-label",
      rrText
        ? `计划赔率 ${rrText}${item.odds_ok ? "，高赔率" : ""}`
        : `入场准备度 ${readiness}，${item.entry_readiness_label || "状态待确认"}`,
    );

    const bitgetUrl = safeValue(item.bitget_url) === "—" ? null : String(item.bitget_url);
    if (bitgetUrl) {
      const bitget = document.createElement("a");
      bitget.className = "bitget-button";
      bitget.href = bitgetUrl;
      bitget.setAttribute("target", "_blank");
      bitget.setAttribute("rel", "noopener noreferrer");
      bitget.setAttribute("aria-label", `在 Bitget 查看 ${shortInstrument(item.instrument)}`);
      bitget.textContent = "↗";
      main.append(bitget);
    }
    card.append(main);

    const tags = document.createElement("div");
    tags.className = "candidate-tags";
    const facts = document.createElement("div");
    facts.className = "candidate-tag-row";
    factsLine(item, stage).forEach((label) => {
      appendText(facts, "span", "info-tag", label);
    });
    tags.append(facts);
    const flags = flagsLine(item);
    if (flags.length) {
      const warnings = document.createElement("div");
      warnings.className = "candidate-tag-row candidate-tag-row--flags";
      flags.forEach((label) => {
        appendText(warnings, "span", "info-tag info-tag--flag", label);
      });
      tags.append(warnings);
    }
    card.append(tags);
    return card;
  }

  function stageMeta(item) {
    const key = String((item && item.stage) || "");
    return STAGES.find((stage) => stage.key === key) || STAGES[0];
  }

  function renderMoverCard(item) {
    const href = safeValue(item.bitget_url) === "—" ? null : String(item.bitget_url);
    const card = document.createElement(href ? "a" : "article");
    card.className = "mover-card";
    if (href) {
      card.href = href;
      card.setAttribute("target", "_blank");
      card.setAttribute("rel", "noopener noreferrer");
    }
    const row = document.createElement("div");
    row.className = "mover-row";
    const symbol = shortInstrument(item.instrument);
    appendText(row, "span", "mover-symbol", symbol);
    const price = latestPriceValue(item);
    if (price !== null) {
      const priceNode = appendText(row, "strong", "mover-price", price, formatPrice);
      priceNode.setAttribute("aria-label", "最新价");
    }
    appendChange(row, item, "mover-change");
    card.append(row);
    card.setAttribute(
      "title",
      [symbol, formatChange(item.change_24h_pct), price === null ? "" : formatPrice(price)]
        .filter(Boolean)
        .join(" · "),
    );
    return card;
  }

  function renderMomentumCard(item) {
    const card = renderMoverCard(item);
    card.classList.add("mover-card--stack");
    const meta = [
      item.status_label,
      hasDisplayValue(item.daily_streak) ? `日线${item.daily_streak}连` : "",
      hasDisplayValue(item.volume_ratio) ? `量${Number(item.volume_ratio).toFixed(1)}x` : "",
    ].filter(Boolean).join(" · ");
    if (meta) {
      appendText(card, "span", "mover-meta", meta);
    }
    return card;
  }

  function renderMomentum(momentum) {
    const section = get("momentum-section");
    const launchedCards = get("momentum-launched-cards");
    const watchCards = get("momentum-watch-cards");
    const launchedCount = get("momentum-launched-count");
    const watchCount = get("momentum-watch-count");
    if (!section || !launchedCards || !watchCards || !launchedCount || !watchCount) {
      return;
    }
    const board = momentum && typeof momentum === "object" ? momentum : {};
    const launched = Array.isArray(board.launched) ? board.launched : [];
    const watch = Array.isArray(board.watch) ? board.watch : [];
    launchedCards.replaceChildren();
    watchCards.replaceChildren();
    launched.forEach((item) => launchedCards.append(renderMomentumCard(item || {})));
    watch.forEach((item) => watchCards.append(renderMomentumCard(item || {})));
    launchedCount.textContent = String(launched.length);
    watchCount.textContent = String(watch.length);
    section.hidden = launched.length === 0 && watch.length === 0;
  }

  function renderMovers(movers) {
    const section = get("movers-section");
    const gainersCards = get("gainers-cards");
    const losersCards = get("losers-cards");
    const gainersCount = get("gainers-count");
    const losersCount = get("losers-count");
    if (!section || !gainersCards || !losersCards || !gainersCount || !losersCount) {
      return;
    }
    const board = movers && typeof movers === "object" ? movers : {};
    const gainers = Array.isArray(board.gainers) ? board.gainers : [];
    const losers = Array.isArray(board.losers) ? board.losers : [];
    gainersCards.replaceChildren();
    losersCards.replaceChildren();
    gainers.forEach((item) => gainersCards.append(renderMoverCard(item || {})));
    losers.forEach((item) => losersCards.append(renderMoverCard(item || {})));
    gainersCount.textContent = String(gainers.length);
    losersCount.textContent = String(losers.length);
    section.hidden = gainers.length === 0 && losers.length === 0;
  }

  function renderFeatured(items) {
    const section = get("featured-section");
    const cards = get("featured-cards");
    const count = get("featured-count");
    if (!section || !cards || !count) return;
    cards.replaceChildren();
    const records = Array.isArray(items) ? items : [];
    section.hidden = records.length === 0;
    records.forEach((item) => {
      const record = item || {};
      cards.append(renderCard(record, stageMeta(record)));
    });
    count.textContent = String(records.length);
  }

  function renderStage(stage, items) {
    const panel = document.querySelector(`[data-stage="${stage.key}"]`);
    if (!panel) return 0;
    const cards = panel.querySelector("[data-stage-cards]");
    const count = panel.querySelector("[data-stage-count]");
    cards.replaceChildren();
    const records = Array.isArray(items) ? items : [];
    panel.hidden = records.length === 0;
    records.forEach((item) => cards.append(renderCard(item || {}, stage)));
    count.textContent = String(records.length);
    return records.length;
  }

  function renderSummary(data, candidateCount) {
    const summary = data && data.summary ? data.summary : {};
    const status = String(summary.status || "report_unavailable");
    const statusLabels = {
      ready: "报告就绪",
      not_scanned: "尚未扫描",
      report_unavailable: "报告不可用",
    };
    get("summary-status").textContent = statusLabels[status] || status;
    get("summary-retrieved").textContent = summary.retrieved_at_shanghai
      ? summary.retrieved_at_shanghai
      : "—";
    get("summary-retrieved").setAttribute("title", "北京时间 UTC+8");
    get("summary-live").textContent = safeValue(summary.live_usdt_swaps);
    get("summary-eligible").textContent = safeValue(summary.eligible_trade_contracts);
    get("summary-candidates").textContent = String(candidateCount);
    get("summary-errors").textContent = `异常 ${safeValue(summary.error_count || 0)}`;
  }

  function hasMovers(data) {
    const movers = data && data.movers ? data.movers : {};
    const gainers = Array.isArray(movers.gainers) ? movers.gainers.length : 0;
    const losers = Array.isArray(movers.losers) ? movers.losers.length : 0;
    const momentum = data && data.momentum ? data.momentum : {};
    const launched = Array.isArray(momentum.launched) ? momentum.launched.length : 0;
    const watch = Array.isArray(momentum.watch) ? momentum.watch.length : 0;
    return gainers + losers + launched + watch > 0;
  }

  function renderEmptyState(data, candidateCount) {
    const summary = data && data.summary ? data.summary : {};
    const empty = get("empty-state");
    const heading = empty.querySelector("h2");
    const message = empty.querySelector("p");
    if (summary.status === "not_scanned") {
      heading.textContent = "等待第一份扫描报告";
      message.textContent = "点击“扫描”拉取最新状态。已有报告时，扫描失败会保留当前卡片。";
      empty.hidden = false;
      return;
    }
    if (summary.status === "report_unavailable") {
      heading.textContent = "报告暂不可用";
      message.textContent = "请重新扫描；如果仍然失败，检查 dashboard 服务日志。";
      empty.hidden = false;
      return;
    }
    if (summary.status === "ready") {
      heading.textContent = "当前没有符合条件的合约";
      message.textContent = "本轮扫描已完成，但暂时没有达到观察或执行条件的合约。稍后可重新扫描。";
      empty.hidden = candidateCount > 0 || hasMovers(data);
      return;
    }
    empty.hidden = candidateCount > 0;
  }

  function renderDashboard(data) {
    const stages = data && data.stages ? data.stages : {};
    let candidateCount = 0;
    STAGES.forEach((stage) => {
      candidateCount += renderStage(stage, stages[stage.key]);
    });
    renderMovers(data && data.movers);
    renderMomentum(data && data.momentum);
    renderFeatured(data && data.featured);

    renderSummary(data, candidateCount);
    renderEmptyState(data, candidateCount);
    hasDashboard = true;
    if (data && data.summary && data.summary.status === "ready") {
      hideError();
    }
  }

  function showError(message) {
    const panel = get("error-state");
    get("error-message").textContent = safeValue(message);
    panel.hidden = false;
  }

  function hideError() {
    get("error-state").hidden = true;
  }

  function renderProgress(stage) {
    const current = PROGRESS_STAGES.indexOf(stage);
    PROGRESS_STAGES.forEach((name, index) => {
      const node = get(`progress-${name.replace("_", "-")}`);
      node.classList.toggle("is-active", current === index);
      node.classList.toggle("is-complete", current >= 0 && index < current);
    });
    get("progress-stage-label").textContent = PROGRESS_LABELS.get(stage) || "准备中";
  }

  function renderScanStatus(snapshot, summary = null) {
    const state = snapshot && snapshot.state ? String(snapshot.state) : "idle";
    const scanState = get("scan-state");
    const progress = get("scan-progress");
    get("scan-started-at").textContent = formatBackendEventTime(snapshot && snapshot.started_at);
    get("scan-finished-at").textContent = formatBackendEventTime(snapshot && snapshot.finished_at);
    if (state === "running") {
      const stageLabel = PROGRESS_LABELS.get(snapshot.stage) || "准备中";
      scanState.textContent = `扫描中 · ${stageLabel}`;
      progress.hidden = false;
      renderProgress(snapshot.stage);
      return;
    }
    progress.hidden = true;
    if (state === "succeeded") {
      scanState.textContent = "扫描完成";
    } else if (state === "failed") {
      const error = safeValue(snapshot.error);
      scanState.textContent = error === "—"
        ? "扫描失败，保留旧报告"
        : `扫描失败 · ${error}`;
      showError(snapshot.error || "扫描失败，已保留现有报告卡片。");
    } else if (state === "idle" && summary && summary.status === "ready") {
      scanState.textContent = "报告就绪";
    } else {
      scanState.textContent = "等待报告";
    }
  }

  async function loadDashboard() {
    const response = await fetch("/api/dashboard", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`读取仪表盘失败（HTTP ${response.status}）`);
    const data = await response.json();
    renderDashboard(data);
    renderScanStatus(data.scan || {}, data.summary || null);
    if (data.scan && data.scan.state === "running") {
      startPolling();
    }
    return data;
  }

  function stopPolling() {
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function pollScanStatus() {
    if (pollInFlight) return;
    pollInFlight = true;
    try {
      const response = await fetch("/api/scan/status", { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`读取扫描状态失败（HTTP ${response.status}）`);
      const snapshot = await response.json();
      pollErrorCount = 0;
      if (snapshot.state === "running" || snapshot.state === "succeeded") {
        hideError();
      }
      renderScanStatus(snapshot);
      if (snapshot.state === "succeeded") {
        await loadDashboard();
        get("scan-now").disabled = false;
        stopPolling();
      } else if (snapshot.state === "failed") {
        stopPolling();
        get("scan-now").disabled = false;
      }
    } catch (error) {
      pollErrorCount += 1;
      const message = error instanceof Error ? error.message : "扫描状态暂时不可用。";
      if (pollErrorCount <= MAX_POLL_ERRORS) {
        get("scan-state").textContent = `状态暂时不可用，正在重试 ${pollErrorCount}/${MAX_POLL_ERRORS}…`;
        showError(`${message} 正在自动重试。`);
        return;
      }
      stopPolling();
      get("scan-now").disabled = false;
      showError(message);
    } finally {
      pollInFlight = false;
    }
  }

  function startPolling() {
    stopPolling();
    pollErrorCount = 0;
    pollTimer = window.setInterval(pollScanStatus, 2000);
    void pollScanStatus();
  }

  async function startScan() {
    const button = get("scan-now");
    button.disabled = true;
    hideError();
    get("scan-state").textContent = "提交扫描…";
    try {
      const response = await fetch("/api/scan", {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      if (response.status !== 202 && response.status !== 409) {
        throw new Error(`提交扫描失败（HTTP ${response.status}）`);
      }
      if (response.status === 202) {
        renderScanStatus(await response.json());
      } else {
        get("scan-state").textContent = "已有扫描进行中";
      }
      startPolling();
    } catch (error) {
      button.disabled = false;
      showError(error instanceof Error ? error.message : "扫描请求暂时不可用。");
      if (hasDashboard) get("scan-state").textContent = "等待报告";
    }
  }

  function boot() {
    updateClock();
    window.setInterval(updateClock, 1000);
    const scanButton = get("scan-now");
    scanButton.disabled = true;
    scanButton.addEventListener("click", startScan);
    loadDashboard().catch((error) => {
      showError(error instanceof Error ? error.message : "仪表盘暂时不可用。");
      renderEmptyState({ summary: { status: "not_scanned" } }, 0);
    }).finally(() => {
      if (pollTimer === null) scanButton.disabled = false;
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }
})();
