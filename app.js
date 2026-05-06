const POLL_INTERVAL_MS = 60_000;
const DEFAULT_LOCAL_API_BASE = "http://127.0.0.1:8000";
const ASSET_COLORS = {
  gold: "#f0b34a",
  oil: "#2a3b39",
  eurusd: "#79aef8",
};
const TITLE_STOP_WORDS = new Set([
  "the",
  "and",
  "for",
  "with",
  "from",
  "into",
  "after",
  "before",
  "over",
  "under",
  "amid",
  "says",
  "blockage",
  "say",
  "will",
  "this",
  "that",
  "have",
  "has",
  "was",
  "were",
  "are",
  "your",
  "their",
  "about",
  "market",
  "markets",
  "prices",
  "price",
  "news",
  "headline",
  "breaking",
  "reuters",
  "bloomberg",
  "cnbc",
]);
const IS_FILE_MODE = window.location.protocol === "file:";
const API_BASE_URL = resolveApiBaseUrl();

const state = {
  audioContext: null,
  audioArmed: false,
  voiceEnabled: JSON.parse(localStorage.getItem("voiceEnabled") ?? "true"),
  seenAlertIds: new Set(JSON.parse(localStorage.getItem("seenAlertIds") ?? "[]")),
  booted: false,
  activeAsset: "all",
  snapshot: null,
};

const elements = {
  markets: document.getElementById("markets"),
  alerts: document.getElementById("alerts"),
  news: document.getElementById("news"),
  calendar: document.getElementById("calendar"),
  sources: document.getElementById("sources"),
  warnings: document.getElementById("warnings"),
  liveStatus: document.getElementById("liveStatus"),
  lastUpdated: document.getElementById("lastUpdated"),
  trustedLabel: document.getElementById("trustedLabel"),
  alertCount: document.getElementById("alertCount"),
  headlineCount: document.getElementById("headlineCount"),
  calendarCount: document.getElementById("calendarCount"),
  sourceHealth: document.getElementById("sourceHealth"),
  refreshButton: document.getElementById("refreshButton"),
  armAudioButton: document.getElementById("armAudioButton"),
  voiceButton: document.getElementById("voiceButton"),
  notificationButton: document.getElementById("notificationButton"),
  assetFilters: document.getElementById("assetFilters"),
  heroBreakingCount: document.getElementById("heroBreakingCount"),
  heroHeadlineCount: document.getElementById("heroHeadlineCount"),
  heroSourceCount: document.getElementById("heroSourceCount"),
};

function escapeHtml(value = "") {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function resolveApiBaseUrl() {
  const params = new URLSearchParams(window.location.search);
  const queryValue = params.get("api");
  if (queryValue) {
    return queryValue.replace(/\/+$/, "");
  }

  const storedValue = localStorage.getItem("radarApiBaseUrl");
  if (storedValue) {
    return storedValue.replace(/\/+$/, "");
  }

  if (IS_FILE_MODE) {
    return DEFAULT_LOCAL_API_BASE;
  }

  return window.location.origin.replace(/\/+$/, "");
}

function buildApiUrl(path, force = false) {
  const suffix = force ? "?force=1" : "";
  return `${API_BASE_URL}${path}${suffix}`;
}

function numberFormat(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "N/A";
  }
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value));
}

function relativeTime(value) {
  if (!value) {
    return "Unknown time";
  }

  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return value;
  }

  const diffSeconds = Math.round((time.getTime() - Date.now()) / 1000);
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const units = [
    ["day", 86_400],
    ["hour", 3_600],
    ["minute", 60],
  ];

  for (const [unit, size] of units) {
    if (Math.abs(diffSeconds) >= size || unit === "minute") {
      return formatter.format(Math.round(diffSeconds / size), unit);
    }
  }

  return "just now";
}

function formatAgeFromMinutes(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return null;
  }

  const diffMinutes = Math.round(Number(value));
  const absMinutes = Math.abs(diffMinutes);

  if (absMinutes <= 60) {
    return diffMinutes >= 0 ? `${absMinutes} min ago` : `in ${absMinutes} min`;
  }

  const absHours = Math.max(1, Math.floor(absMinutes / 60));
  return diffMinutes >= 0
    ? `${absHours} hr${absHours === 1 ? "" : "s"} ago`
    : `in ${absHours} hr${absHours === 1 ? "" : "s"}`;
}

function minutesSince(value) {
  if (!value) {
    return null;
  }

  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return null;
  }

  return formatAgeFromMinutes((Date.now() - time.getTime()) / 60_000);
}

function formatTimeRemaining(value) {
  if (!value) {
    return null;
  }

  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return null;
  }

  const diffMinutes = Math.round((time.getTime() - Date.now()) / 60_000);
  const absMinutes = Math.abs(diffMinutes);

  if (diffMinutes <= 0) {
    if (absMinutes <= 2) {
      return "Expected now";
    }
    if (absMinutes <= 60) {
      return `${absMinutes} min late`;
    }
    const lateHours = Math.max(1, Math.floor(absMinutes / 60));
    return `${lateHours} hr${lateHours === 1 ? "" : "s"} late`;
  }

  if (diffMinutes <= 60) {
    return `${diffMinutes} min left`;
  }

  if (diffMinutes < 1_440) {
    const hours = Math.floor(diffMinutes / 60);
    const minutes = diffMinutes % 60;
    if (minutes && hours < 8) {
      return `${hours} hr ${minutes} min left`;
    }
    return `${hours} hr${hours === 1 ? "" : "s"} left`;
  }

  const days = Math.ceil(diffMinutes / 1_440);
  return `${days} day${days === 1 ? "" : "s"} left`;
}

function biasLabel(bias) {
  if (bias === "up") {
    return "Up";
  }
  if (bias === "down") {
    return "Down";
  }
  return "Mixed";
}

function assetLabel(assetKey) {
  const labels = {
    gold: "Gold",
    oil: "Oil",
    eurusd: "EUR/USD",
  };
  return labels[assetKey] || assetKey;
}

function itemMatchesActiveAsset(item) {
  if (state.activeAsset === "all") {
    return true;
  }
  const impact = item.assetImpacts?.[state.activeAsset];
  return Boolean(impact && impact.confidence > 0);
}

function renderAssetFilters(data) {
  const filters = [
    { key: "all", label: "All Markets" },
    ...Object.keys(data.assets || {}).map((assetKey) => ({
      key: assetKey,
      label: data.assets[assetKey].label,
    })),
  ];

  elements.assetFilters.innerHTML = filters
    .map(
      (filter) => `
        <button
          class="filter-button ${state.activeAsset === filter.key ? "is-active" : ""}"
          data-asset="${escapeHtml(filter.key)}"
        >
          ${escapeHtml(filter.label)}
        </button>
      `
    )
    .join("");

  elements.assetFilters.querySelectorAll("[data-asset]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeAsset = button.getAttribute("data-asset") || "all";
      if (state.snapshot) {
        renderSnapshot(state.snapshot);
      }
    });
  });
}

function renderImpactBadges(item) {
  const assets = Object.entries(item.assetImpacts || {}).filter(([, impact]) => impact.confidence > 0);
  return assets
    .map(
      ([assetKey, impact]) => `
        <span class="impact-badge impact-badge--${impact.bias}">
          ${escapeHtml(assetLabel(assetKey))} ${escapeHtml(biasLabel(impact.bias))}
        </span>
        <span class="chip">${escapeHtml(
          impact.zone ? impact.zone.label : `${impact.moveLowPct}% - ${impact.moveHighPct}%`
        )}</span>
      `
    )
    .join("");
}

function buildSparklineGeometry(series, width = 240, height = 116, padding = 8) {
  if (!Array.isArray(series) || series.length < 2) {
    return null;
  }

  const minValue = Math.min(...series);
  const maxValue = Math.max(...series);
  const range = maxValue - minValue || 1;
  const innerWidth = width - padding * 2;
  const innerHeight = height - padding * 2;
  const points = series.map((value, index) => {
    const x = padding + (innerWidth * index) / Math.max(series.length - 1, 1);
    const y = padding + innerHeight - ((value - minValue) / range) * innerHeight;
    return { x, y };
  });
  const polyline = points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
  const area = [
    `M ${points[0].x.toFixed(2)} ${(height - padding).toFixed(2)}`,
    ...points.map((point) => `L ${point.x.toFixed(2)} ${point.y.toFixed(2)}`),
    `L ${points[points.length - 1].x.toFixed(2)} ${(height - padding).toFixed(2)}`,
    "Z",
  ].join(" ");

  return {
    area,
    polyline,
    latestPoint: points[points.length - 1],
    latestValue: series[series.length - 1],
    minValue,
    maxValue,
  };
}

function renderInlineChartBox(asset) {
  const geometry = buildSparklineGeometry(asset.series || []);
  const lineColor = ASSET_COLORS[asset.asset] || "#f0b34a";
  const priceDigits = asset.priceDigits || 2;
  const trendValue = Number(asset.changePct);
  const trendLabel = Number.isFinite(trendValue)
    ? `${trendValue >= 0 ? "+" : ""}${numberFormat(trendValue, 2)}% session`
    : "Live proxy trend";

  if (!geometry) {
    return `
      <div class="chart-box">
        <div class="chart-box__title">
          <span>Fast Chart</span>
          <span>${escapeHtml(asset.proxyLabel)}</span>
        </div>
        <div class="empty-state">Waiting for enough price points to draw the chart.</div>
      </div>
    `;
  }

  return `
    <div class="chart-box">
      <div class="chart-box__title">
        <span>Fast Chart</span>
        <span>${escapeHtml(trendLabel)}</span>
      </div>
      <div class="chart-box__widget chart-box__widget--native">
        <svg class="sparkline" viewBox="0 0 240 116" preserveAspectRatio="none" aria-hidden="true">
          <defs>
            <linearGradient id="spark-fill-${escapeHtml(asset.asset)}" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="${escapeHtml(lineColor)}" stop-opacity="0.34"></stop>
              <stop offset="100%" stop-color="${escapeHtml(lineColor)}" stop-opacity="0.03"></stop>
            </linearGradient>
          </defs>
          <path d="${escapeHtml(geometry.area)}" fill="url(#spark-fill-${escapeHtml(asset.asset)})"></path>
          <polyline
            points="${escapeHtml(geometry.polyline)}"
            fill="none"
            stroke="${escapeHtml(lineColor)}"
            stroke-width="2.75"
            stroke-linejoin="round"
            stroke-linecap="round"
          ></polyline>
          <circle
            cx="${escapeHtml(geometry.latestPoint.x.toFixed(2))}"
            cy="${escapeHtml(geometry.latestPoint.y.toFixed(2))}"
            r="3.5"
            fill="${escapeHtml(lineColor)}"
            stroke="rgba(9, 15, 25, 0.95)"
            stroke-width="1.4"
          ></circle>
        </svg>
      </div>
      <div class="chart-box__meta">
        <span>Low ${escapeHtml(numberFormat(geometry.minValue, priceDigits))}</span>
        <span>Last ${escapeHtml(numberFormat(geometry.latestValue, priceDigits))}</span>
        <span>High ${escapeHtml(numberFormat(geometry.maxValue, priceDigits))}</span>
      </div>
    </div>
  `;
}

function relatedAssetKeys(item) {
  return Object.entries(item.assetImpacts || {})
    .filter(([, impact]) => impact.confidence > 0)
    .map(([assetKey]) => assetKey);
}

function titleKeywords(value) {
  return _normalizeKeywordText(value)
    .split(" ")
    .filter((token) => token.length >= 3 && !TITLE_STOP_WORDS.has(token));
}

function _normalizeKeywordText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function relationScore(baseItem, candidate) {
  if (!candidate || candidate.id === baseItem.id || candidate.kind !== "news") {
    return -1;
  }

  let score = 0;
  const baseAssets = new Set(relatedAssetKeys(baseItem));
  const candidateAssets = relatedAssetKeys(candidate);
  const sharedAssets = candidateAssets.filter((assetKey) => baseAssets.has(assetKey));
  score += sharedAssets.length * 6;

  const baseThemes = new Set((baseItem.themes || []).map((theme) => String(theme).toLowerCase()));
  for (const theme of candidate.themes || []) {
    if (baseThemes.has(String(theme).toLowerCase())) {
      score += 5;
    }
  }

  const baseKeywords = new Set(titleKeywords(baseItem.title));
  for (const token of titleKeywords(candidate.title)) {
    if (baseKeywords.has(token)) {
      score += 1.5;
    }
  }

  if (state.activeAsset !== "all" && candidate.assetImpacts?.[state.activeAsset]?.confidence > 0) {
    score += 4;
  }
  if (candidate.breaking) {
    score += 2.5;
  }

  return score;
}

function getRelatedStories(item, limit = 3) {
  if (!state.snapshot?.news?.length || item.kind !== "news") {
    return [];
  }

  return state.snapshot.news
    .map((candidate) => ({ candidate, score: relationScore(item, candidate) }))
    .filter(({ score }) => score > 0)
    .sort((left, right) => {
      if (right.score !== left.score) {
        return right.score - left.score;
      }
      return (right.candidate.sortScore || 0) - (left.candidate.sortScore || 0);
    })
    .slice(0, limit)
    .map(({ candidate }) => candidate);
}

function renderRelatedStories(item) {
  if (item.kind !== "news" || !item.breaking) {
    return "";
  }

  const stories = getRelatedStories(item, 3);
  if (!stories.length) {
    return "";
  }

  return `
    <div class="related-block">
      <div class="related-block__label">Related Headlines</div>
      <div class="related-list">
        ${stories
          .map((story) => {
            const storyAge = story.ageMinutes !== null && story.ageMinutes !== undefined
              ? formatAgeFromMinutes(story.ageMinutes)
              : minutesSince(story.publishedAt) || story.publishedLabel;
            return `
              <a class="related-story" href="${escapeHtml(story.link || "#")}" target="_blank" rel="noreferrer">
                <div class="related-story__meta">
                  <span>${escapeHtml(story.source)}</span>
                  <span>${escapeHtml(storyAge || "Recent")}</span>
                  ${story.breaking ? '<span class="related-story__badge">Breaking</span>' : ""}
                </div>
                <div class="related-story__title">${escapeHtml(story.title)}</div>
              </a>
            `;
          })
          .join("")}
      </div>
    </div>
  `;
}

function renderMarketCard(asset) {
  const catalysts = asset.catalysts.length
    ? asset.catalysts
        .map(
          (catalyst) =>
            `<span class="chip">${escapeHtml(catalyst.source)}: ${escapeHtml(catalyst.title)}</span>`
        )
        .join("")
    : '<span class="chip">Waiting for catalysts</span>';

  const priceText = asset.price
    ? `${numberFormat(asset.price, asset.priceDigits || 2)} ${asset.currency || ""}`.trim()
    : "Waiting for live quote";
  const changeText =
    asset.changePct === null || asset.changePct === undefined
      ? asset.proxyLabel
      : `${asset.proxyLabel} • ${Number(asset.changePct) >= 0 ? "+" : ""}${numberFormat(asset.changePct, 2)}%`;
  const rangeText =
    asset.dayLow !== null && asset.dayLow !== undefined && asset.dayHigh !== null && asset.dayHigh !== undefined
      ? `Session range ${numberFormat(asset.dayLow, asset.priceDigits || 2)} - ${numberFormat(
          asset.dayHigh,
          asset.priceDigits || 2
        )}`
      : "Session range unavailable";
  const muted = state.activeAsset !== "all" && state.activeAsset !== asset.asset ? "is-muted" : "";

  return `
    <article class="market-card market-card--${asset.asset} ${muted}">
      <div class="market-card__top">
        <div>
          <div class="market-card__label">${escapeHtml(asset.proxyLabel)}</div>
          <h2>${escapeHtml(asset.label)}</h2>
        </div>
        <span class="pill pill--${asset.bias}">${escapeHtml(biasLabel(asset.bias))}</span>
      </div>

      <div class="market-card__body">
        <div>
          <div class="market-card__price">${escapeHtml(priceText)}</div>
          <div class="market-card__sub">${escapeHtml(changeText)}</div>
          <div class="meter"><span style="width: ${Math.min(asset.confidence || 0, 100)}%"></span></div>
          <p class="market-card__summary">${escapeHtml(asset.summary)}</p>
          <div class="market-card__move">${escapeHtml(asset.moveText)}</div>
          <div class="market-card__range">${escapeHtml(rangeText)}</div>
        </div>

        ${renderInlineChartBox(asset)}
      </div>

      <div class="chip-row market-card__chips">${catalysts}</div>
    </article>
  `;
}

function renderTimeLine(item) {
  if (item.kind === "news") {
    const age = item.ageMinutes !== null && item.ageMinutes !== undefined
      ? formatAgeFromMinutes(item.ageMinutes)
      : minutesSince(item.publishedAt);
    if (age) {
      return `${age} • ${item.publishedLabel}`;
    }
  }

  if (item.kind === "calendar" && item.eventStatus === "scheduled") {
    const remaining = formatTimeRemaining(item.scheduledAt);
    if (remaining) {
      return `${remaining} • ${item.publishedLabel}`;
    }
    if (item.timingNote) {
      return `${item.timingNote} • ${item.publishedLabel}`;
    }
    return `Scheduled • ${item.publishedLabel}`;
  }
  if (item.kind === "calendar" && item.eventStatus === "released") {
    return `Released • ${item.publishedLabel}`;
  }

  return item.publishedLabel || relativeTime(item.publishedAt);
}

function normalizeImpactLevel(level) {
  if (!level) {
    return "low";
  }
  if (level.includes("high")) {
    return "high";
  }
  if (level.includes("medium")) {
    return "medium";
  }
  return "low";
}

function renderCalendarImpact(item) {
  if (item.kind !== "calendar") {
    return "";
  }
  const level = normalizeImpactLevel(item.impactLevel || "");
  const label = level === "high" ? "High impact" : level === "medium" ? "Medium impact" : "Low impact";
  return `<span class="calendar-impact calendar-impact--${level}">${escapeHtml(label)}</span>`;
}

function renderCard(item) {
  const impactRow = renderImpactBadges(item);
  const breakingBadge = item.breaking
    ? '<span class="breaking-badge">Breaking</span>'
    : "";
  const calendarImpact = renderCalendarImpact(item);
  const calendarTone = item.kind === "calendar" ? `card--calendar-${normalizeImpactLevel(item.impactLevel || "")}` : "";

  return `
    <article class="card card--${item.severity} ${item.breaking ? "card--breaking" : ""} ${calendarTone}">
      <div class="card__top">
        <span class="card__source">${escapeHtml(item.source)}</span>
        <div class="card__badges">${calendarImpact}${breakingBadge}</div>
      </div>
      <div class="card__meta">
        <span class="card__time">${escapeHtml(renderTimeLine(item))}</span>
      </div>
      <a href="${escapeHtml(item.link || "#")}" target="_blank" rel="noreferrer">
        <h3 class="card__title">${escapeHtml(item.title)}</h3>
      </a>
      <p class="card__summary">${escapeHtml(item.summary || "")}</p>
      <div class="card__channel">${escapeHtml(item.channel || "")}</div>
      <div class="card__impact-row chip-row">${impactRow}</div>
      ${renderRelatedStories(item)}
    </article>
  `;
}

function renderWarnings(warnings) {
  if (!warnings.length) {
    elements.warnings.innerHTML = "";
    return;
  }

  elements.warnings.innerHTML = warnings
    .map((warning) => `<div class="warning">${escapeHtml(warning)}</div>`)
    .join("");
}

function renderSources(sources) {
  if (!sources.length) {
    elements.sources.innerHTML = '<div class="empty-state">No source checks yet.</div>';
    return;
  }

  const healthy = sources.filter((source) => source.healthy).length;
  elements.sourceHealth.textContent = `${healthy}/${sources.length} online`;
  elements.heroSourceCount.textContent = `${healthy}/${sources.length}`;

  elements.sources.innerHTML = sources
    .map((source) => {
      const note = source.healthy
        ? source.count !== undefined
          ? `${source.count} items`
          : source.note || "Online"
        : source.error || "Offline";
      return `
        <div class="source-item">
          <div class="source-item__name">${escapeHtml(source.label)}</div>
          <div>${escapeHtml(note)}</div>
        </div>
      `;
    })
    .join("");
}

function persistSeenAlerts() {
  const trimmed = Array.from(state.seenAlertIds).slice(-160);
  localStorage.setItem("seenAlertIds", JSON.stringify(trimmed));
}

function updateButtons() {
  elements.armAudioButton.textContent = state.audioArmed ? "Audio armed" : "Arm audio";
  elements.voiceButton.textContent = state.voiceEnabled ? "Voice on" : "Voice off";
  if (!("Notification" in window)) {
    elements.notificationButton.textContent = "Alerts unsupported";
  } else {
    elements.notificationButton.textContent =
      Notification.permission === "granted" ? "Alerts enabled" : "Browser alerts";
  }
}

async function armAudio() {
  if (!state.audioContext) {
    state.audioContext = new window.AudioContext();
  }
  if (state.audioContext.state === "suspended") {
    await state.audioContext.resume();
  }
  state.audioArmed = true;
  playAlarm(true);
  updateButtons();
}

function playAlarm(isTest = false) {
  if (!state.audioArmed || !state.audioContext) {
    return;
  }

  const context = state.audioContext;
  const startAt = context.currentTime + 0.02;
  const frequencies = isTest ? [520, 660] : [520, 760, 620];

  frequencies.forEach((frequency, index) => {
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "triangle";
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, startAt + index * 0.18);
    gain.gain.exponentialRampToValueAtTime(0.08, startAt + index * 0.18 + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, startAt + index * 0.18 + 0.12);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(startAt + index * 0.18);
    oscillator.stop(startAt + index * 0.18 + 0.14);
  });
}

function speakAlerts(alerts) {
  if (!state.voiceEnabled || !("speechSynthesis" in window) || !alerts.length) {
    return;
  }

  window.speechSynthesis.cancel();
  const speech = new SpeechSynthesisUtterance(
    alerts
      .slice(0, 2)
      .map((alert) => `${alert.source}. ${alert.title}.`)
      .join(" ")
  );
  speech.rate = 1;
  speech.pitch = 0.96;
  window.speechSynthesis.speak(speech);
}

function browserNotify(alerts) {
  if (!alerts.length || !("Notification" in window) || Notification.permission !== "granted") {
    return;
  }

  const top = alerts[0];
  new Notification("Macro Reaction Radar", {
    body: `${top.source}: ${top.title}`,
  });
}

function handleFreshAlerts(alerts) {
  if (!state.booted) {
    alerts.forEach((alert) => state.seenAlertIds.add(alert.id));
    persistSeenAlerts();
    state.booted = true;
    return;
  }

  const fresh = alerts.filter((alert) => !state.seenAlertIds.has(alert.id));
  if (!fresh.length) {
    return;
  }

  fresh.forEach((alert) => state.seenAlertIds.add(alert.id));
  persistSeenAlerts();

  const actionable = fresh.filter((alert) => alert.alertMode !== "watch");
  if (!actionable.length) {
    return;
  }

  playAlarm(false);
  speakAlerts(actionable);
  browserNotify(actionable);
}

function renderSnapshot(data) {
  state.snapshot = data;
  renderAssetFilters(data);

  const assets = Object.values(data.assets || {});
  const filteredBreaking = (data.breakingNews || []).filter(itemMatchesActiveAsset);
  const filteredNews = (data.news || []).filter(itemMatchesActiveAsset);
  const filteredCalendar = (data.calendar || []).filter(itemMatchesActiveAsset);

  elements.markets.innerHTML = assets.length
    ? assets.map(renderMarketCard).join("")
    : '<div class="empty-state">No market summary available.</div>';

  elements.alerts.innerHTML = filteredBreaking.length
    ? filteredBreaking.map((item) => renderCard(item)).join("")
    : '<div class="empty-state">No breaking headlines for this filter right now.</div>';

  elements.news.innerHTML = filteredNews.length
    ? filteredNews.map((item) => renderCard(item)).join("")
    : '<div class="empty-state">No trusted headlines for this filter.</div>';

  elements.calendar.innerHTML = filteredCalendar.length
    ? filteredCalendar.map((item) => renderCard(item)).join("")
    : '<div class="empty-state">No calendar events for this filter.</div>';

  renderWarnings(data.warnings || []);
  renderSources(data.sources || []);

  elements.alertCount.textContent = `${filteredBreaking.length} breaking`;
  elements.headlineCount.textContent = `${filteredNews.length} headlines`;
  elements.calendarCount.textContent = `${filteredCalendar.length} events`;
  elements.heroBreakingCount.textContent = `${filteredBreaking.length}`;
  elements.heroHeadlineCount.textContent = `${filteredNews.length}`;

  elements.liveStatus.textContent = data.warnings.length ? "Partial feed" : "Live";
  elements.liveStatus.className = `pill pill--${data.warnings.length ? "mixed" : "up"}`;
  elements.lastUpdated.textContent = `Last scan ${relativeTime(data.generatedAt)}`;
  elements.trustedLabel.textContent = `Trusted publishers: ${(data.trustedPublishers || []).join(", ")}`;

  handleFreshAlerts(data.alerts || []);
}

async function loadSnapshot(force = false) {
  elements.liveStatus.textContent = "Scanning";
  elements.liveStatus.className = "pill pill--neutral";
  elements.lastUpdated.textContent = IS_FILE_MODE
    ? `Local file mode linked to ${API_BASE_URL}`
    : elements.lastUpdated.textContent;

  try {
    const response = await fetch(buildApiUrl("/api/monitor", force), {
      mode: "cors",
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    renderSnapshot(data);
  } catch (error) {
    elements.liveStatus.textContent = "Feed error";
    elements.liveStatus.className = "pill pill--down";
    const localFileHint = IS_FILE_MODE
      ? ` Open the backend with "python3 app.py" and keep it running on ${DEFAULT_LOCAL_API_BASE}.`
      : "";
    elements.warnings.innerHTML = `<div class="warning">${escapeHtml(
      `The radar could not load live data from ${API_BASE_URL}: ${error.message}.${localFileHint}`
    )}</div>`;
  }
}

async function requestBrowserAlerts() {
  if (!("Notification" in window)) {
    updateButtons();
    return;
  }
  await Notification.requestPermission();
  updateButtons();
}

function init() {
  updateButtons();
  if (IS_FILE_MODE) {
    elements.trustedLabel.textContent = `Local file mode via ${API_BASE_URL}`;
  }
  loadSnapshot(false);
  window.setInterval(() => loadSnapshot(false), POLL_INTERVAL_MS);

  elements.refreshButton.addEventListener("click", () => loadSnapshot(true));
  elements.armAudioButton.addEventListener("click", () => armAudio());
  elements.voiceButton.addEventListener("click", () => {
    state.voiceEnabled = !state.voiceEnabled;
    localStorage.setItem("voiceEnabled", JSON.stringify(state.voiceEnabled));
    updateButtons();
  });
  elements.notificationButton.addEventListener("click", () => requestBrowserAlerts());
}

init();
