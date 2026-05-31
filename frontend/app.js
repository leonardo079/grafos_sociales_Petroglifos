// ── Panel de pruebas — Grafos Sociales Rupestres ──────────────────────────
// Vanilla JS. Ejerce todos los endpoints de la API FastAPI.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const baseUrl = () => $("#baseUrl").value.replace(/\/+$/, "");

let sitesCache = [];        // cache de GET /sites para selects y nombres
let selectedSiteId = null;

// ── Helpers ────────────────────────────────────────────────────────────────

function toast(msg, type = "") {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show " + type;
  setTimeout(() => (t.className = "toast"), 3200);
}

async function api(path, opts = {}) {
  const url = baseUrl() + path;
  const res = await fetch(url, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(`${res.status} — ${detail}`);
  }
  return res.json();
}

function confidenceBadge(level) {
  const lv = (level || "low").toLowerCase();
  return `<span class="badge badge-${lv}">${lv}</span>`;
}

function pct(x) {
  return (x * 100).toFixed(1) + "%";
}

function setLoading(el, txt = "Cargando…") {
  el.innerHTML = `<div class="spinner">${txt}</div>`;
}

// ── Tabs ─────────────────────────────────────────────────────────────────────

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".tab").forEach((t) => t.classList.remove("active"));
    $$(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $("#tab-" + tab.dataset.tab).classList.add("active");

    // Al abrir la pestaña de visualización, cargar Plotly automáticamente la 1ª vez
    if (tab.dataset.tab === "visual" && !vizLoaded) {
      loadViz("/graph/export/plotly", $("#vizPlotly"));
    }
  });
});

// ── Health ─────────────────────────────────────────────────────────────────

$("#pingBtn").addEventListener("click", checkHealth);

async function checkHealth() {
  const dot = $("#healthDot");
  try {
    const data = await api("/health");
    const ok = data.status === "ok";
    dot.className = "dot " + (ok ? "dot-ok" : "dot-bad");
    dot.title = `status=${data.status} | db=${data.database}`;
    toast(`API ${data.status} · DB ${data.database} · ${data.environment}`, ok ? "ok" : "error");
  } catch (e) {
    dot.className = "dot dot-bad";
    dot.title = e.message;
    toast("No se pudo conectar: " + e.message, "error");
  }
}

// ── GET /sites ───────────────────────────────────────────────────────────────

$("#loadSites").addEventListener("click", loadSites);

async function loadSites() {
  const dept = $("#filterDept").value.trim();
  const muni = $("#filterMuni").value.trim();
  const qs = new URLSearchParams();
  if (dept) qs.set("department", dept);
  if (muni) qs.set("municipality", muni);
  const tbody = $("#sitesTable tbody");
  setLoading($("#sitesCount"));
  try {
    const sites = await api("/sites" + (qs.toString() ? "?" + qs : ""));
    sitesCache = sites;
    $("#sitesCount").textContent = `${sites.length} sitio(s)`;
    tbody.innerHTML = sites
      .map(
        (s) => `<tr data-id="${s.id}">
          <td>${s.name}</td>
          <td>${s.municipality || "—"}</td>
          <td>${s.department || "—"}</td>
          <td>${s.dominant_taxonomy || "—"}</td>
          <td>${s.petroglyph_count}</td>
        </tr>`
      )
      .join("");
    tbody.querySelectorAll("tr").forEach((tr) =>
      tr.addEventListener("click", () => {
        tbody.querySelectorAll("tr").forEach((r) => r.classList.remove("selected"));
        tr.classList.add("selected");
        loadSiteDetail(tr.dataset.id);
      })
    );
    populateSimSelect();
  } catch (e) {
    $("#sitesCount").textContent = "";
    toast("Error listando sitios: " + e.message, "error");
  }
}

// ── GET /sites/{id} ────────────────────────────────────────────────────────

async function loadSiteDetail(id) {
  selectedSiteId = id;
  const box = $("#siteDetail");
  setLoading(box);
  try {
    const s = await api("/sites/" + id);
    const conns = (s.iconographic_connections || [])
      .sort((a, b) => b.weight - a.weight);
    const idToName = Object.fromEntries(sitesCache.map((x) => [x.id, x.name]));

    const rows = conns
      .map((c) => {
        const name = idToName[c.connected_site_id] || c.connected_site_id.slice(0, 8);
        const prov = c.is_provisional ? ' <span class="badge badge-prov">provisional</span>' : "";
        return `<tr>
          <td>${name}</td>
          <td>${pct(c.weight)}</td>
          <td>${c.evidence_count}</td>
          <td>${confidenceBadge(c.confidence_level)}${prov}</td>
          <td>${(c.shared_taxonomies || []).join(", ") || "—"}</td>
        </tr>`;
      })
      .join("");

    box.innerHTML = `
      <div class="detail-head">${s.name}</div>
      <div class="detail-meta">
        ${s.municipality || "—"}, ${s.department || "—"} ·
        ${s.dominant_taxonomy} · ${s.petroglyph_count} petroglifos ·
        estado: ${s.conservation_status}
        ${s.latitude ? `· (${s.latitude}, ${s.longitude})` : ""}
      </div>
      <div class="muted">${conns.length} conexión(es) iconográfica(s)</div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Sitio</th><th>Similitud</th><th>Evid.</th><th>Confianza</th><th>Taxonomías</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="5" class="muted">Sin conexiones</td></tr>'}</tbody>
        </table>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="muted">Error: ${e.message}</div>`;
  }
}

// ── POST /compare ──────────────────────────────────────────────────────────

$("#compareBtn").addEventListener("click", async () => {
  const payload = {
    image_path: $("#cmpImage").value.trim(),
    site: $("#cmpSite").value.trim() || "Sin nombre",
    municipality: $("#cmpMuni").value.trim(),
    department: $("#cmpDept").value.trim(),
  };
  if (!payload.image_path) return toast("Indica un image_path", "error");

  const box = $("#compareResult");
  setLoading(box, "Procesando imagen…");
  try {
    const r = await api("/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const matchRows = (r.matches || [])
      .map(
        (m) => `<tr>
          <td>${m.site_name}</td>
          <td>${m.taxonomy}</td>
          <td>${pct(m.similarity_score)}</td>
          <td>${m.reference_name || "—"}</td>
        </tr>`
      )
      .join("");
    box.innerHTML = `
      <div class="analysis-out" style="display:block">
        <div class="kv"><span>embedding_available</span><span>${r.embedding_available}</span></div>
        <div class="kv"><span>graph_updated</span><span>${r.graph_updated}</span></div>
        <div class="kv"><span>edges_persisted</span><span>${r.edges_persisted}</span></div>
        <div class="kv"><span>latency_ms</span><span>${r.latency_ms}</span></div>
      </div>
      <div class="table-wrap" style="margin-top:0.6rem">
        <table>
          <thead><tr><th>Sitio</th><th>Taxonomía</th><th>Similitud</th><th>Referencia</th></tr></thead>
          <tbody>${matchRows || '<tr><td colspan="4" class="muted">Sin matches</td></tr>'}</tbody>
        </table>
      </div>`;
    toast(`Comparación lista · ${r.matches.length} matches · ${r.edges_persisted} aristas`, "ok");
  } catch (e) {
    box.innerHTML = `<div class="muted">Error: ${e.message}</div>`;
    toast("Error en /compare: " + e.message, "error");
  }
});

// ── GET /graph/sites/{id}/similar ────────────────────────────────────────────

function populateSimSelect() {
  const sel = $("#simSiteSelect");
  sel.innerHTML = sitesCache
    .map((s) => `<option value="${s.id}">${s.name}</option>`)
    .join("");
}

$("#simBtn").addEventListener("click", async () => {
  const id = $("#simSiteSelect").value;
  const topK = $("#simTopK").value || 5;
  if (!id) return toast("Primero lista los sitios", "error");
  const box = $("#similarResult");
  setLoading(box);
  try {
    const r = await api(`/graph/sites/${id}/similar?top_k=${topK}`);
    const rows = (r.similar_sites || [])
      .map((s) => {
        const prov = s.is_provisional ? ' <span class="badge badge-prov">provisional</span>' : "";
        return `<tr>
          <td>${s.site}</td>
          <td>${pct(s.weight)}</td>
          <td>${s.evidence_count}</td>
          <td>${confidenceBadge(s.confidence_level)}${prov}</td>
          <td>${(s.shared_taxonomies || []).join(", ") || "—"}</td>
        </tr>`;
      })
      .join("");
    box.innerHTML = `
      <div class="muted" style="margin-top:0.6rem">Similares a <b>${r.site_name}</b></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Sitio</th><th>Similitud</th><th>Evid.</th><th>Confianza</th><th>Taxonomías</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="5" class="muted">Sin vecinos</td></tr>'}</tbody>
        </table>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="muted">Error: ${e.message}</div>`;
    toast("Error: " + e.message, "error");
  }
});

// ── Análisis del grafo ───────────────────────────────────────────────────────

$$("[data-analysis]").forEach((btn) =>
  btn.addEventListener("click", () => runAnalysis(btn.dataset.analysis))
);

async function runAnalysis(kind) {
  const box = $("#out-" + kind);
  setLoading(box);
  try {
    if (kind === "pagerank") {
      const r = await api("/graph/pagerank");
      const entries = Object.entries(r.pagerank || {});
      const max = entries.length ? entries[0][1] : 1;
      box.innerHTML =
        `<div class="muted">Top site: <b>${r.top_site || "—"}</b></div>` +
        entries
          .map(
            ([site, score], i) => `<div class="rank-row">
              <div><span class="rank-num">${i + 1}.</span> ${site}</div>
              <div>${score.toFixed(5)}</div>
            </div>
            <div class="bar" style="width:${(score / max) * 100}%"></div>`
          )
          .join("");
    } else if (kind === "betweenness") {
      const r = await api("/graph/betweenness");
      const entries = Object.entries(r.betweenness || {});
      box.innerHTML =
        `<div class="muted">Sitio puente: <b>${r.top_bridge_site || "—"}</b></div>` +
        entries
          .map(
            ([site, score], i) =>
              `<div class="rank-row"><div><span class="rank-num">${i + 1}.</span> ${site}</div><div>${score.toFixed(5)}</div></div>`
          )
          .join("");
    } else if (kind === "communities") {
      const r = await api("/graph/communities");
      box.innerHTML =
        `<div class="muted">${r.count} comunidad(es)</div>` +
        (r.communities || [])
          .map(
            (c, i) =>
              `<div class="comm-group"><b>Comunidad ${i + 1}</b> (${c.length}): ${c.join(", ")}</div>`
          )
          .join("");
    } else if (kind === "metrics") {
      const m = await api("/graph/metrics");
      const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
      box.innerHTML =
        kv("nodos", m.nodes) +
        kv("aristas", m.edges) +
        kv("densidad", m.density) +
        kv("similitud media", m.avg_similarity) +
        kv("clustering", m.clustering_coefficient) +
        kv("componentes", m.connected_components) +
        kv("comp. mayor", m.largest_component_size) +
        kv("diámetro", m.diameter ?? "—") +
        kv("grado medio", m.degree_distribution?.avg_degree) +
        `<div class="muted" style="margin-top:0.5rem">Top hubs</div>` +
        (m.degree_distribution?.top_hubs || [])
          .map((h) => `<div class="rank-row"><div>${h.site}</div><div>grado ${h.degree}</div></div>`)
          .join("");
    }
  } catch (e) {
    box.innerHTML = `<div class="muted">Error: ${e.message}</div>`;
    toast("Error en /" + kind + ": " + e.message, "error");
  }
}

// ── Visualizaciones (iframe inline a los endpoints de exportación HTML) ──────

let vizLoaded = false;
let vizTimeout = null;

function loadViz(path, btn) {
  const url = baseUrl() + path;
  const frame = $("#vizFrame");
  const loading = $("#vizLoading");
  const wrap = frame.parentElement;

  // resaltar el botón activo
  $("#vizPlotly").classList.remove("btn-primary");
  $("#vizPyvis").classList.remove("btn-primary");
  if (btn) btn.classList.add("btn-primary");

  wrap.classList.remove("empty");
  loading.innerHTML =
    '<div class="viz-spinner"></div><p>Generando visualización…</p>' +
    "<small>El servidor está construyendo el grafo, puede tardar unos segundos.</small>";
  loading.classList.add("show");
  $("#vizOpenTab").href = url;
  frame.src = url;
  vizLoaded = true;

  // Si en 40s no carga (servidor caído/lento), avisar en vez de girar para siempre
  clearTimeout(vizTimeout);
  vizTimeout = setTimeout(() => {
    if (loading.classList.contains("show")) {
      loading.innerHTML =
        '<p>⚠️ No se pudo cargar la visualización</p>' +
        `<small>¿Está el servidor activo en ${baseUrl()}? Revisa la conexión arriba a la derecha e intenta de nuevo.</small>`;
    }
  }, 40000);
}

// Ocultar el overlay cuando el iframe termina de cargar el HTML
$("#vizFrame").addEventListener("load", () => {
  if (vizLoaded) {
    clearTimeout(vizTimeout);
    $("#vizLoading").classList.remove("show");
  }
});

$("#vizPlotly").addEventListener("click", (e) => loadViz("/graph/export/plotly", e.currentTarget));
$("#vizPyvis").addEventListener("click", (e) => loadViz("/graph/export", e.currentTarget));

// ── GET /graph (JSON crudo) ──────────────────────────────────────────────────

$("#loadGraph").addEventListener("click", async () => {
  const summary = $("#graphSummary");
  const out = $("#graphJson");
  setLoading(summary);
  out.textContent = "";
  try {
    const g = await api("/graph");
    const s = g.summary || {};
    const kv = (k, v) => `<div class="kv"><span>${k}</span><span>${v}</span></div>`;
    summary.innerHTML =
      `<div class="analysis-out" style="display:block">` +
      kv("nodos", s.nodes) +
      kv("aristas", s.edges) +
      kv("similitud media", s.avg_similarity) +
      kv("similitud máx", s.max_similarity) +
      kv("sitio más central", s.most_central_site) +
      kv("comunidades", s.communities) +
      kv("densidad", s.density) +
      `</div>`;
    out.textContent = JSON.stringify(g, null, 2);
  } catch (e) {
    summary.innerHTML = `<div class="muted">Error: ${e.message}</div>`;
    toast("Error en /graph: " + e.message, "error");
  }
});

// ── Init ─────────────────────────────────────────────────────────────────────

window.addEventListener("DOMContentLoaded", () => {
  checkHealth();
  loadSites();
});
