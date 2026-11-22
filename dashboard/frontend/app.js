// Warehouse Monitor – Dashboard-Oberfläche
//
// Messwerte und KI-Bericht werden nur als Text ins DOM geschrieben
// (textContent, createElement), nie als HTML-Zeichenkette: Text aus dem
// Modell oder aus den Daten kann so kein HTML/JavaScript einschleusen.

const LOCALE = "de-DE";

function fmt(value, digits = 2) {
  if (value === null || value === undefined) return "—";
  return Number(value).toLocaleString(LOCALE, { maximumFractionDigits: digits });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ── Diagramme ───────────────────────────────────────
const CHART_CONFIG = (label, color) => ({
  type: "line",
  data: {
    labels: [],
    datasets: [
      {
        label,
        data: [],
        borderColor: color,
        borderWidth: 1.5,
        pointRadius: 0,
        tension: 0.4,
        fill: false,
      },
    ],
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    locale: LOCALE,
    plugins: { legend: { display: false } },
    scales: {
      x: { display: false },
      y: {
        ticks: { color: "#6b6b80", font: { size: 10 } },
        grid: { color: "#2a2a35" },
      },
    },
  },
});

const chartTemp = new Chart(
  document.getElementById("chart-temp"),
  CHART_CONFIG("Temperatur", "#ff6b35"),
);
const chartHum = new Chart(
  document.getElementById("chart-hum"),
  CHART_CONFIG("Luftfeuchtigkeit", "#00d4aa"),
);
const chartPress = new Chart(
  document.getElementById("chart-press"),
  CHART_CONFIG("Luftdruck", "#7b8cff"),
);

// ── Grenzwerte ──────────────────────────────────────
let thresholds = null;
let lastData = null;

async function loadThresholds() {
  const response = await fetch("/telemetry/thresholds");
  thresholds = await response.json();
  // Kam die erste Messung schon vor den Grenzwerten an, jetzt nachträglich prüfen
  if (lastData) checkAlerts(lastData);
}

loadThresholds();

// (Feld, Status-Element, Anzeigename, Einheit)
const PARAMETERS = [
  ["temperature_c", "status-temp", "Temperatur", "°C"],
  ["humidity_pct", "status-hum", "Luftfeuchtigkeit", "%"],
  ["pressure_hpa", "status-press", "Luftdruck", "hPa"],
];

function checkAlerts(data) {
  if (!thresholds) return;

  const alerts = [];
  for (const [field, statusId, name, unit] of PARAMETERS) {
    const limits = thresholds[field];
    const value = data[field];
    const status = document.getElementById(statusId);
    if (value < limits.min || value > limits.max) {
      alerts.push(
        `${name} ${fmt(value)} ${unit} außerhalb des Grenzbereichs ` +
          `(${fmt(limits.min)}–${fmt(limits.max)} ${unit})`,
      );
      status.textContent = "ALARM";
      status.className = "metric-status alert";
    } else {
      status.textContent = "IM BEREICH";
      status.className = "metric-status ok";
    }
  }

  const list = document.getElementById("alerts-list");
  list.replaceChildren();
  if (alerts.length === 0) {
    list.append(el("div", "no-alerts", "✓ Alle Messwerte innerhalb der Grenzwerte"));
  } else {
    for (const text of alerts) {
      const item = el("div", "alert-item");
      item.append(el("span", "alert-icon", "⚠"), document.createTextNode(text));
      list.append(item);
    }
  }
}

// ── Live-Stream (SSE) ───────────────────────────────
const MAX_POINTS = 30; // so viele Punkte zeigt jedes Diagramm
let lastTs = null;

function plot(data) {
  const label = new Date(data.ts).toLocaleTimeString(LOCALE);
  addPoint(chartTemp, label, data.temperature_c);
  addPoint(chartHum, label, data.humidity_pct);
  addPoint(chartPress, label, data.pressure_hpa);
}

function showMeasurement(data) {
  // Jede Messung nur einmal zeichnen (z. B. nach einem Neuaufbau der Verbindung)
  if (lastTs !== null && new Date(data.ts) <= new Date(lastTs)) return;
  lastTs = data.ts;
  lastData = data;

  // 1. Aktuelle Werte
  document.getElementById("val-temp").textContent = fmt(data.temperature_c);
  document.getElementById("val-hum").textContent = fmt(data.humidity_pct);
  document.getElementById("val-press").textContent = fmt(data.pressure_hpa);

  // 2. Zeitstempel
  document.getElementById("last-update").textContent =
    "Letzte Aktualisierung: " + new Date(data.ts).toLocaleString(LOCALE);

  // 3. Punkt in die Diagramme
  plot(data);

  checkAlerts(data);
}

// Beim Öffnen: Diagramme mit den letzten Messwerten füllen, danach live
async function start() {
  try {
    const response = await fetch(`/telemetry/history?limit=${MAX_POINTS}`);
    const history = await response.json();
    history.forEach(showMeasurement);
  } catch (err) {
    console.warn("Verlauf konnte nicht geladen werden:", err);
  }
  const source = new EventSource("/stream/telemetry");
  source.onmessage = (event) => showMeasurement(JSON.parse(event.data));
}

start();

function addPoint(chart, label, value) {
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(value);
  if (chart.data.labels.length > MAX_POINTS) {
    chart.data.labels.shift();
    chart.data.datasets[0].data.shift();
  }
  chart.update();
}

// ── Statistik ───────────────────────────────────────
function renderStats(id, s, count) {
  const box = document.getElementById(id);
  box.replaceChildren();
  for (const [label, value] of [["MIN", s.min], ["MAX", s.max], ["Ø", s.avg]]) {
    const span = el("span", null, `${label} `);
    span.append(el("b", null, fmt(value)));
    box.append(span);
  }
  box.append(el("span", "stats-period", `letzte ${count} Messwerte`));
}

async function updateStats() {
  const response = await fetch("/telemetry/stats");
  const stats = await response.json();
  renderStats("stats-temp", stats.temperature, stats.count);
  renderStats("stats-hum", stats.humidity, stats.count);
  renderStats("stats-press", stats.pressure, stats.count);
}

updateStats();
setInterval(updateStats, 30000); // alle 30 Sekunden

// ── Uhr ─────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById("current-time").textContent =
    now.toLocaleTimeString(LOCALE);
  document.getElementById("current-date").textContent = now.toLocaleDateString(
    LOCALE,
    { day: "2-digit", month: "short", year: "numeric" },
  );
}

updateClock();
setInterval(updateClock, 1000);

// ── KI-Analyse ──────────────────────────────────────
const aiBtn = document.getElementById("ai-btn");
const aiOutput = document.getElementById("ai-output");

function showReport(report, generatedAt) {
  const time = new Date(generatedAt).toLocaleTimeString(LOCALE);
  const header = el("div");
  header.append(
    el("span", "ai-badge", "KI-generiert"),
    el("span", "ai-note", "Automatisch erstellt – kann Fehler enthalten. Maßgeblich sind die Messwerte."),
  );
  aiOutput.replaceChildren(
    header,
    el("div", "ai-report", `${report}\n\n— erstellt um ${time}`),
  );
  aiOutput.classList.add("active");
}

aiBtn.addEventListener("click", async () => {
  aiBtn.disabled = true;
  aiBtn.textContent = "Analyse läuft…";
  aiOutput.classList.remove("error", "active");

  // Die Inferenz dauert je nach Gerät 20 s bis 2 min → sichtbarer Fortschritt
  const started = Date.now();
  aiOutput.textContent = "Analyse läuft… 0 s";
  const timer = setInterval(() => {
    const s = Math.floor((Date.now() - started) / 1000);
    aiOutput.textContent = `Analyse läuft… ${s} s`;
  }, 1000);

  try {
    const response = await fetch("/analyse", { method: "POST" });
    const body = await response.json();
    // 409: Daten ungeeignet, 429: Analyse läuft bereits, 503: Modell nicht verfügbar
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    clearInterval(timer);
    showReport(body.report, body.generated_at);
  } catch (err) {
    clearInterval(timer);
    aiOutput.textContent = `⚠ ${err.message}`;
    aiOutput.classList.add("error");
  } finally {
    aiBtn.disabled = false;
    aiBtn.textContent = "Analysieren";
  }
});
