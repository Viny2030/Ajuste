/**
 * dashboard_tabs_patch.js
 * ========================
 * TAREAS 1 + 2 + 3 — Reemplaza la lógica de tabs sectoriales
 * que cruzaba por programa_id (inválido post-fusión ministerial)
 * por consumo directo del endpoint /api/v1/analisis/sector.
 *
 * También actualiza el tab "Resumen" con los 5 KPIs nuevos (TAREA 3).
 *
 * INSTRUCCIONES DE INTEGRACIÓN:
 * 1. Incluir este script DESPUÉS del script principal del dashboard.
 * 2. O copiar las funciones directamente en el index.html reemplazando
 *    filtrarSector(), renderTabSectorial() y renderResumen().
 *
 * MAPEO DE JURISDICCIONES (TAREA 2):
 *   Obra Pública : jur 64+57+65 → 77
 *   Cap. Humano  : jur 70+75+85 → 88
 *   (el endpoint lo conoce — aquí solo definimos los tab-keys)
 */

// ─── Configuración de tabs sectoriales ────────────────────────────────────────
const TAB_SECTOR_MAP = {
  "tab-obra":       "obra_publica",
  "tab-social":     "capital_humano",
  "tab-jubilacion": "jubilaciones",
  "tab-salud":      "salud",
  "tab-defensa":    "defensa",
  "tab-seguridad":  "seguridad",
};

// Cache en memoria por sesión
const _sectorCache = {};

// ─── Fetch con cache ───────────────────────────────────────────────────────────
async function fetchSector(sectorKey) {
  if (_sectorCache[sectorKey]) return _sectorCache[sectorKey];

  const API = window.API_URL || "";
  const url = `${API}/api/v1/analisis/sector?sector=${sectorKey}`;

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    // El endpoint devuelve { sectores: [...], ipc_factor_acumulado, tc_usd_vigente }
    const sector = data.sectores?.[0] ?? null;
    _sectorCache[sectorKey] = { sector, meta: data };
    return _sectorCache[sectorKey];
  } catch (err) {
    console.error("[MAP] fetchSector error:", err);
    return null;
  }
}

// ─── Render KPI card ──────────────────────────────────────────────────────────
function _kpiCard(label, value, unit = "", colorClass = "") {
  return `
    <div class="kpi-card ${colorClass}">
      <span class="kpi-label">${label}</span>
      <span class="kpi-value">${value !== null && value !== undefined ? value : "N/D"}${unit ? " " + unit : ""}</span>
    </div>`;
}

function _varClass(val) {
  if (val === null || val === undefined) return "";
  return val < -10 ? "kpi-rojo" : val < 0 ? "kpi-naranja" : "kpi-verde";
}

// ─── TAREA 1: Render de tab sectorial consumiendo /api/v1/analisis/sector ─────
async function renderTabSectorial(tabId) {
  const sectorKey = TAB_SECTOR_MAP[tabId];
  if (!sectorKey) return;

  const container = document.getElementById(tabId + "-content");
  if (!container) return;

  container.innerHTML = `<div class="loading">⏳ Cargando datos del sector…</div>`;

  const result = await fetchSector(sectorKey);
  if (!result || !result.sector) {
    container.innerHTML = `<div class="error">❌ No se pudieron cargar los datos del sector <strong>${sectorKey}</strong>.</div>`;
    return;
  }

  const s = result.sector;
  const meta = result.meta;

  // ── 5 KPIs (TAREA 3) ────────────────────────────────────────────────────────
  const kpis = [
    {
      label: "Crédito Original 2023",
      value: s.credito_original_2023_mm !== null
        ? `$${s.credito_original_2023_mm.toLocaleString("es-AR")} M`
        : "N/D",
      color: "",
    },
    {
      label: "Crédito Vigente 2026",
      value: s.credito_vigente_2026_mm !== null
        ? `$${s.credito_vigente_2026_mm.toLocaleString("es-AR")} M`
        : "N/D",
      color: "",
    },
    {
      label: "Var. Nominal",
      value: s.var_nominal_pct !== null ? `${s.var_nominal_pct > 0 ? "+" : ""}${s.var_nominal_pct}%` : "N/D",
      color: _varClass(s.var_nominal_pct),
    },
    {
      label: "Var. Real (IPC)",
      value: s.var_real_ipc_pct !== null ? `${s.var_real_ipc_pct > 0 ? "+" : ""}${s.var_real_ipc_pct}%` : "N/D",
      color: _varClass(s.var_real_ipc_pct),
    },
    {
      label: "Var. Real (USD)",
      value: s.var_real_usd_pct !== null ? `${s.var_real_usd_pct > 0 ? "+" : ""}${s.var_real_usd_pct}%` : "N/D",
      color: _varClass(s.var_real_usd_pct),
    },
  ];

  // ── Mapeo jurisdicciones visible ─────────────────────────────────────────────
  const jurMapHTML = `
    <div class="jur-map">
      <span class="jur-label">Jurisdicciones 2023:</span>
      <span class="jur-ids">${(s.jur_2023 || []).map(j => `<code>jur ${j}</code>`).join(" + ")}</span>
      <span class="jur-arrow">→</span>
      <span class="jur-label">Jurisdicciones 2026:</span>
      <span class="jur-ids">${(s.jur_2026 || []).map(j => `<code>jur ${j}</code>`).join(" + ")}</span>
    </div>`;

  // ── USD context ───────────────────────────────────────────────────────────────
  const usdContextHTML = s.credito_2023_usd_mm !== null ? `
    <div class="usd-context">
      <span>USD 2023: <strong>$${s.credito_2023_usd_mm?.toLocaleString("es-AR")} M</strong></span>
      <span>USD 2026: <strong>$${s.credito_2026_usd_mm?.toLocaleString("es-AR")} M</strong></span>
      <span class="muted">TC: $${s.tc_usd?.toLocaleString("es-AR")}/USD · IPC ×${s.ipc_factor}</span>
    </div>` : "";

  container.innerHTML = `
    <div class="sector-header">
      <h2>${s.icon} ${s.label}</h2>
      ${jurMapHTML}
    </div>
    <div class="kpi-grid">
      ${kpis.map(k => `
        <div class="kpi-card ${k.color}">
          <span class="kpi-label">${k.label}</span>
          <span class="kpi-value">${k.value}</span>
        </div>`).join("")}
    </div>
    ${usdContextHTML}
    <p class="footnote">
      Fuente: Presupuesto Abierto · Deflactado por IPC INDEC acumulado (×${s.ipc_factor}) · 
      TC oficial $${s.tc_usd?.toLocaleString("es-AR")}/USD
    </p>`;
}

// ─── TAREA 3: Actualizar tab Resumen con los 5 KPIs nuevos ────────────────────
async function renderResumen() {
  const container = document.getElementById("tab-resumen-content");
  if (!container) return;

  container.innerHTML = `<div class="loading">⏳ Cargando resumen…</div>`;

  // Fetch todos los sectores
  const API = window.API_URL || "";
  let allSectors = null;
  try {
    const res = await fetch(`${API}/api/v1/analisis/sector`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    allSectors = await res.json();
  } catch (err) {
    container.innerHTML = `<div class="error">❌ Error cargando resumen sectorial.</div>`;
    return;
  }

  const sectores = allSectors.sectores || [];

  const filas = sectores.map(s => `
    <tr>
      <td>${s.icon} ${s.label}</td>
      <td class="num">${s.credito_original_2023_mm !== null ? "$" + s.credito_original_2023_mm.toLocaleString("es-AR") + " M" : "N/D"}</td>
      <td class="num">${s.credito_vigente_2026_mm !== null ? "$" + s.credito_vigente_2026_mm.toLocaleString("es-AR") + " M" : "N/D"}</td>
      <td class="num ${_varClass(s.var_nominal_pct)}">${s.var_nominal_pct !== null ? (s.var_nominal_pct > 0 ? "+" : "") + s.var_nominal_pct + "%" : "N/D"}</td>
      <td class="num ${_varClass(s.var_real_ipc_pct)}">${s.var_real_ipc_pct !== null ? (s.var_real_ipc_pct > 0 ? "+" : "") + s.var_real_ipc_pct + "%" : "N/D"}</td>
      <td class="num ${_varClass(s.var_real_usd_pct)}">${s.var_real_usd_pct !== null ? (s.var_real_usd_pct > 0 ? "+" : "") + s.var_real_usd_pct + "%" : "N/D"}</td>
    </tr>`).join("");

  container.innerHTML = `
    <div class="resumen-meta">
      IPC acumulado: ×${allSectors.ipc_factor_acumulado} · TC: $${allSectors.tc_usd_vigente?.toLocaleString("es-AR")}/USD
    </div>
    <table class="resumen-table">
      <thead>
        <tr>
          <th>Sector</th>
          <th>Original 2023 (M)</th>
          <th>Vigente 2026 (M)</th>
          <th>Var. Nominal</th>
          <th>Var. Real IPC</th>
          <th>Var. Real USD</th>
        </tr>
      </thead>
      <tbody>${filas}</tbody>
    </table>
    <p class="footnote">
      Fuente: Presupuesto Abierto · Deflactado por IPC INDEC acumulado · 
      Mapeado por jurisdicción (no por programa_id).
    </p>`;
}

// ─── Hook: activar al cambiar de tab ──────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  // Asume que los tabs tienen atributo data-tab="tab-obra" etc.
  document.querySelectorAll("[data-tab]").forEach(btn => {
    btn.addEventListener("click", () => {
      const tabId = btn.dataset.tab;
      if (tabId === "tab-resumen") {
        renderResumen();
      } else if (TAB_SECTOR_MAP[tabId]) {
        renderTabSectorial(tabId);
      }
    });
  });

  // Renderizar tab activo inicial si es sectorial
  const activeTab = document.querySelector("[data-tab].active");
  if (activeTab) {
    const tabId = activeTab.dataset.tab;
    if (tabId === "tab-resumen") renderResumen();
    else if (TAB_SECTOR_MAP[tabId]) renderTabSectorial(tabId);
  }
});