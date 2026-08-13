/* ============================================================================
   RONDA · Centro de operaciones
   Conocimiento · Alertas · Actas · Métricas

   Esta capa SOLO PINTA. Ningún número de esta pantalla se calcula aquí: todos
   vienen del servidor, que a su vez los lee de las actas, las alertas y el
   registro de eventos. Si un dato no existe, se muestra un vacío honesto — no
   se inventa un cero ni un «N/D» de relleno.
   ========================================================================== */
"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ── Estado en memoria ───────────────────────────────────────────────── */
const estado = {
  docs: [], alertas: [], actas: [], metricas: null,
  ordenDocs: { col: "titulo", asc: true },
  ordenAlertas: { col: "timestamp", asc: false },
  ordenActas: { col: "fin", asc: false },
  pagDocs: 0, pagActas: 0,
};
const POR_PAGINA = 10;

/* ══════════════════════════════════════════════════════════════════════
 * Utilidades de presentación
 * ═══════════════════════════════════════════════════════════════════ */
function fecha(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("es-CO", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fechaCorta(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("es-CO", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

const T_ACCION = {
  continuar: "Continuar seguimiento",
  repreguntar: "Repreguntar",
  revision_humana: "Revisión humana",
  escalar: "Escalar a enfermería",
};
const T_EVAL = { completa: "Completa", incompleta: "Incompleta", fallida: "Fallida" };
const T_NIVEL = { verde: "Verde", amarillo: "Amarillo", rojo: "Rojo" };
const T_ESTADO_DOC = { disponible: "Disponible", procesando: "Procesando", error: "Error" };

function badge(texto, clase) {
  return `<span class="badge ${clase || ""}">${clase && clase !== "acento" ? "<i></i>" : ""}${esc(texto)}</span>`;
}

function vacio(contenedor, titulo, detalle) {
  contenedor.innerHTML =
    `<div class="vacio-panel">
       <div class="ico"><svg width="22" height="22"><use href="#i-vacio"/></svg></div>
       <h3>${esc(titulo)}</h3><p>${esc(detalle)}</p>
     </div>`;
}

function tostada(tipo, titulo, cuerpo) {
  const t = document.createElement("div");
  t.className = "tostada " + (tipo || "");
  t.innerHTML = `<b>${esc(titulo)}</b><span>${cuerpo || ""}</span>`;
  $("tostadas").appendChild(t);
  setTimeout(() => t.remove(), 7000);
}

/* Ordena una lista por columna, tolerando valores ausentes. */
function ordenar(lista, col, asc, extractor) {
  return lista.slice().sort((a, b) => {
    const va = extractor(a, col), vb = extractor(b, col);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    const r = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb), "es");
    return asc ? r : -r;
  });
}

function marcarOrden(tabla, orden) {
  tabla.querySelectorAll("th.ordenable").forEach((th) => {
    const m = th.querySelector(".orden");
    if (m) m.textContent = th.dataset.col === orden.col ? (orden.asc ? "▴" : "▾") : "";
  });
}

/* ══════════════════════════════════════════════════════════════════════
 * Navegación entre secciones
 * ═══════════════════════════════════════════════════════════════════ */
const SECCIONES = {
  conocimiento: { titulo: "Conocimiento", miga: "Conocimiento vivo del sistema" },
  alertas: { titulo: "Alertas", miga: "Centro de escalamiento" },
  actas: { titulo: "Actas", miga: "Historial de seguimientos" },
  metricas: { titulo: "Métricas", miga: "Panel operacional" },
};

function irA(nombre) {
  const sec = SECCIONES[nombre] ? nombre : "conocimiento";
  document.querySelectorAll(".seccion").forEach((s) => s.classList.remove("activa"));
  $("sec-" + sec).classList.add("activa");
  document.querySelectorAll(".nav a[data-sec]").forEach((a) => {
    if (a.dataset.sec === sec) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  $("tbTitulo").textContent = SECCIONES[sec].titulo;
  $("tbMiga").textContent = SECCIONES[sec].miga;
  window.scrollTo(0, 0);
}

/* ══════════════════════════════════════════════════════════════════════
 * CONOCIMIENTO
 * ═══════════════════════════════════════════════════════════════════ */
async function cargarDocs() {
  try {
    const r = await fetch("/api/docs");
    const d = await r.json();
    const lista = Array.isArray(d) ? d : (d.documentos || []);
    estado.docs = lista.filter((x) => x && x.estado !== "eliminado");
  } catch (e) {
    estado.docs = [];
  }
  pintarKpisDocs();
  pintarDocs();
}

function pintarKpisDocs() {
  const activos = estado.docs.filter((d) => d.estado === "disponible");
  $("kDocs").textContent = activos.length;
  const chunks = activos.reduce((a, d) => a + (Number(d.chunks) || 0), 0);
  $("kChunks").textContent = chunks || "—";
  const fechas = estado.docs.map((d) => d.actualizado || d.creado || d.timestamp).filter(Boolean).sort();
  $("kKbFecha").textContent = fechas.length ? "Última carga: " + fechaCorta(fechas[fechas.length - 1]) : " ";
}

function docsFiltrados() {
  const q = ($("buscaDoc").value || "").toLowerCase().trim();
  const est = $("filtroEstadoDoc").value;
  let lista = estado.docs.filter((d) => {
    if (est && d.estado !== est) return false;
    if (q && !String(d.titulo || "").toLowerCase().includes(q)) return false;
    return true;
  });
  return ordenar(lista, estado.ordenDocs.col, estado.ordenDocs.asc,
    (d, c) => (c === "chunks" ? Number(d.chunks) || 0 : String(d.titulo || "")));
}

function pintarDocs() {
  const lista = docsFiltrados();
  const cuerpo = $("cuerpoDocs");
  const total = lista.length;
  const paginas = Math.max(1, Math.ceil(total / POR_PAGINA));
  if (estado.pagDocs >= paginas) estado.pagDocs = paginas - 1;
  const pagina = lista.slice(estado.pagDocs * POR_PAGINA, (estado.pagDocs + 1) * POR_PAGINA);

  $("contadorDocs").textContent = total === estado.docs.length
    ? `${total} documento${total === 1 ? "" : "s"}`
    : `${total} de ${estado.docs.length}`;

  cuerpo.innerHTML = "";
  if (!total) {
    $("tablaDocs").hidden = true;
    $("pagDocs").hidden = true;
    vacio($("vacioDocs"),
      estado.docs.length ? "Ningún documento coincide" : "Todavía no hay conocimiento cargado",
      estado.docs.length ? "Pruebe con otro término o quite el filtro de estado."
                         : "Suba un PDF o un TXT para que RONDA pueda citarlo durante una llamada.");
    return;
  }
  $("tablaDocs").hidden = false;
  $("vacioDocs").innerHTML = "";

  for (const d of pagina) {
    const clase = { disponible: "verde", error: "rojo", procesando: "amarillo" }[d.estado] || "";
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="principal"><div class="celda-doc">
         <svg width="15" height="15"><use href="#i-doc"/></svg>
         <span title="${esc(d.titulo)}">${esc(d.titulo)}</span></div></td>
       <td>${badge(T_ESTADO_DOC[d.estado] || d.estado || "—", clase)}</td>
       <td class="num">${d.chunks != null ? d.chunks : "—"}</td>
       <td>${esc(d.extraccion || d.tipo || "texto")}</td>
       <td class="acciones">
         <button class="btn-fila" data-ver="${esc(d.doc_id)}">Ver detalle</button>
         <button class="btn-fila" data-olvido="${esc(d.doc_id)}">Verificar olvido</button>
         <button class="btn-fila peligro" data-borrar="${esc(d.doc_id)}">Eliminar</button>
       </td>`;
    cuerpo.appendChild(tr);
  }

  $("pagDocs").hidden = paginas <= 1;
  $("pagDocsTexto").textContent = `Página ${estado.pagDocs + 1} de ${paginas}`;
  $("pagDocsPrev").disabled = estado.pagDocs === 0;
  $("pagDocsNext").disabled = estado.pagDocs >= paginas - 1;
  marcarOrden($("tablaDocs"), estado.ordenDocs);
}

/* ── Subida ──────────────────────────────────────────────────────────── */
async function subir(file) {
  if (!file) return;
  const kbAntes = ($("chipKb").textContent || "").replace("KB", "").trim();
  $("progreso").hidden = false;
  $("progresoBarra").style.width = "35%";
  const fd = new FormData();
  fd.append("file", file);
  try {
    const r = await fetch("/api/docs", { method: "POST", body: fd });
    $("progresoBarra").style.width = "100%";
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || ("HTTP " + r.status));
    await Promise.all([cargarDocs(), cargarSalud()]);
    const kbDespues = ($("chipKb").textContent || "").replace("KB", "").trim();
    tostada("ok", "Conocimiento actualizado",
      `<span>${esc(file.name)} · ${d.chunks || 0} fragmentos indexados.</span>` +
      (kbAntes && kbDespues && kbAntes !== kbDespues
        ? `<br><span>KB <code>${esc(kbAntes)}</code> → <code>${esc(kbDespues)}</code></span>` : ""));
  } catch (e) {
    tostada("error", "No se pudo indexar", `<span>${esc(e.message || e)}</span>`);
  } finally {
    setTimeout(() => { $("progreso").hidden = true; $("progresoBarra").style.width = "0"; }, 700);
    $("archivo").value = "";
  }
}

async function verificarOlvido(docId) {
  try {
    const r = await fetch(`/api/docs/${docId}/verificar-olvido`, { method: "POST" });
    const d = await r.json();
    const ok = d.olvidado === true && d.vectores_restantes === 0;
    tostada(ok ? "ok" : "error", ok ? "Olvido verificado" : "El documento sigue presente",
      `<span>Vectores restantes: <code>${d.vectores_restantes}</code></span>`);
  } catch (e) {
    tostada("error", "No se pudo verificar", `<span>${esc(e.message || e)}</span>`);
  }
}

async function borrarDoc(docId, titulo) {
  if (!window.confirm(`¿Eliminar «${titulo}» del conocimiento?\n\nRONDA dejará de poder citarlo y la versión del conocimiento cambiará.`)) return;
  try {
    const r = await fetch(`/api/docs/${docId}`, { method: "DELETE" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    await Promise.all([cargarDocs(), cargarSalud()]);
    tostada("ok", "Documento eliminado",
      "<span>Puede comprobar el olvido con «Verificar olvido» sobre cualquier documento.</span>");
  } catch (e) {
    tostada("error", "No se pudo eliminar", `<span>${esc(e.message || e)}</span>`);
  }
}

function verDoc(docId) {
  const d = estado.docs.find((x) => x.doc_id === docId);
  if (!d) return;
  const campos = [
    ["Estado", T_ESTADO_DOC[d.estado] || d.estado],
    ["Fragmentos indexados", d.chunks],
    ["Tipo de extracción", d.extraccion || d.tipo || "texto"],
    ["Identificador", d.doc_id],
    ["SHA-256", d.sha256 ? String(d.sha256).slice(0, 32) + "…" : null],
    ["Cargado", d.creado ? fecha(d.creado) : null],
    ["Actualizado", d.actualizado ? fecha(d.actualizado) : null],
    ["Motivo del error", d.error || d.motivo || null],
  ].filter(([, v]) => v != null && v !== "");
  abrirDrawer(d.titulo,
    `<div class="sello"><svg width="15" height="15"><use href="#i-shield"/></svg>
       Este documento forma parte del conocimiento que RONDA puede citar.</div>` +
    campos.map(([k, v]) => `<div class="campo"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join(""));
}

/* ══════════════════════════════════════════════════════════════════════
 * ALERTAS
 * ═══════════════════════════════════════════════════════════════════ */
async function cargarAlertas() {
  try {
    const r = await fetch("/api/alertas");
    const d = await r.json();
    estado.alertas = Array.isArray(d) ? d : (d.alertas || []);
  } catch (e) { estado.alertas = []; }
  pintarKpisAlertas();
  pintarAlertas();
}

/* El paciente llega como objeto en las alertas y como cadena en el resumen de
   actas. Se acepta cualquiera de las dos formas en vez de asumir una. */
const nombrePaciente = (o) => {
  const p = o && o.paciente;
  if (!p) return "—";
  return (typeof p === "string" ? p : (p.nombre || p.paciente_id)) || "—";
};
/* El motivo legible de una alerta.
   `patron` sirve cuando es un umbral («temperatura 39.5°C»), pero en las reglas
   de texto es una expresión regular cruda que no debe verse en pantalla: se
   sustituye por la descripción clínica o por el nombre de la regla. */
const ES_REGEX = /[|{}()\\[\]]|\.\*|\.\{/;
const motivoAlerta = (a) => {
  const r = (a.reglas_disparadas || [])[0];
  if (!r) return "—";
  if (r.patron && !ES_REGEX.test(r.patron)) return r.patron;
  if (r.mensaje_paciente) return r.mensaje_paciente;
  if (r.descripcion) return r.descripcion;
  return String(r.regla || "—").replace(/^[a-z]+:/, "").replace(/_/g, " ");
};

function pintarKpisAlertas() {
  const A = estado.alertas;
  $("kAlTotal").textContent = A.length;
  $("kAlRojas").textContent = A.filter((a) => a.nivel === "rojo").length;
  const hace24 = Date.now() - 86400000;
  $("kAl24").textContent = A.filter((a) => {
    const t = Date.parse(a.timestamp);
    return !isNaN(t) && t >= hace24;
  }).length;
  $("kAlPacientes").textContent =
    new Set(A.map((a) => (a.paciente || {}).paciente_id || (a.paciente || {}).nombre).filter(Boolean)).size;
}

function pintarAlertas() {
  const q = ($("buscaAlerta").value || "").toLowerCase().trim();
  const niv = $("filtroNivel").value;
  let lista = estado.alertas.filter((a) => {
    if (niv && a.nivel !== niv) return false;
    if (q) {
      const heno = (nombrePaciente(a) + " " + motivoAlerta(a)).toLowerCase();
      if (!heno.includes(q)) return false;
    }
    return true;
  });
  lista = ordenar(lista, estado.ordenAlertas.col, estado.ordenAlertas.asc,
    (a, c) => (c === "timestamp" ? Date.parse(a.timestamp) || 0 : nombrePaciente(a)));

  $("contadorAlertas").textContent = lista.length === estado.alertas.length
    ? `${lista.length} alerta${lista.length === 1 ? "" : "s"}`
    : `${lista.length} de ${estado.alertas.length}`;

  const cuerpo = $("cuerpoAlertas");
  cuerpo.innerHTML = "";
  if (!lista.length) {
    $("tablaAlertas").hidden = true;
    vacio($("vacioAlertas"),
      estado.alertas.length ? "Ninguna alerta coincide" : "No hay alertas registradas",
      estado.alertas.length ? "Pruebe con otro término o quite el filtro de nivel."
                            : "Las alertas aparecen aquí en cuanto una llamada dispara una señal de alarma.");
    return;
  }
  $("tablaAlertas").hidden = false;
  $("vacioAlertas").innerHTML = "";

  lista.forEach((a, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="principal">${esc(nombrePaciente(a))}</td>
       <td>${badge(T_NIVEL[a.nivel] || a.nivel || "—", a.nivel)}</td>
       <td>${esc(motivoAlerta(a))}</td>
       <td>${esc(fecha(a.timestamp))}</td>
       <td>${esc(a.siguiente_paso || "—")}</td>
       <td class="acciones"><button class="btn-fila" data-alerta="${i}">Ver detalle</button></td>`;
    tr.querySelector("[data-alerta]").dataset.ref = JSON.stringify(a.alerta_id || "");
    tr.querySelector("[data-alerta]").onclick = () => verAlerta(a);
    cuerpo.appendChild(tr);
  });
  marcarOrden($("tablaAlertas"), estado.ordenAlertas);
}

function verAlerta(a) {
  const reglas = a.reglas_disparadas || [];
  const carriles = a.niveles_por_carril || {};
  let html =
    `<div class="sello"><svg width="15" height="15"><use href="#i-shield"/></svg>
       Alerta generada por una condición determinista, no por criterio del modelo.</div>` +
    [["Paciente", nombrePaciente(a)],
     ["Procedimiento", (a.paciente || {}).procedimiento],
     ["Día postoperatorio", (a.paciente || {}).dia_postoperatorio],
     ["Nivel", T_NIVEL[a.nivel] || a.nivel],
     ["Fecha y hora", fecha(a.timestamp)],
     ["Siguiente paso", a.siguiente_paso],
     ["Sesión", a.session_id],
    ].filter(([, v]) => v != null && v !== "")
     .map(([k, v]) => `<div class="campo"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");

  if (reglas.length) {
    html += `<div class="acta-bloque"><h4>Reglas disparadas</h4>`;
    reglas.forEach((r) => {
      html += `<div class="extracto" style="margin-bottom:8px">
                 <b>${esc(r.regla || "—")}</b> · ${esc(r.ambito || "")} · ${esc(T_NIVEL[r.nivel] || r.nivel || "")}
                 <br>${esc(r.patron || "")}
                 ${r.descripcion ? "<br><span style='color:var(--faint)'>" + esc(r.descripcion) + "</span>" : ""}
               </div>`;
    });
    html += `</div>`;
  }
  const cs = Object.entries(carriles).filter(([, v]) => v);
  if (cs.length) {
    html += `<div class="acta-bloque"><h4>Nivel por carril</h4><div class="barras">` +
      cs.map(([k, v]) =>
        `<div class="barra-fila"><span class="et">${esc(k.replace(/^carril_/, "").replace(/_/g, " "))}</span>
         <span></span><span class="n">${badge(T_NIVEL[v] || v, v)}</span></div>`).join("") +
      `</div></div>`;
  }
  if (a.extracto_transcript) {
    html += `<div class="acta-bloque"><h4>Extracto de la conversación</h4>
             <div class="extracto">${esc(a.extracto_transcript)}</div></div>`;
  }
  abrirDrawer("Alerta · " + nombrePaciente(a), html);
}

/* ══════════════════════════════════════════════════════════════════════
 * ACTAS
 * ═══════════════════════════════════════════════════════════════════ */
async function cargarActas() {
  try {
    const r = await fetch("/api/actas-resumen");
    const d = await r.json();
    estado.actas = Array.isArray(d) ? d : (d.actas || []);
  } catch (e) { estado.actas = []; }
  pintarKpisActas();
  pintarActas();
}

const critActa = (a) => a.criticidad_final || a.criticidad_clinica || null;
const decActa = (a) => a.decision || {};

function pintarKpisActas() {
  const A = estado.actas;
  $("kAcTotal").textContent = A.length;
  $("kAcRojas").textContent = A.filter((a) => critActa(a) === "rojo").length;
  $("kAcVerdes").textContent = A.filter((a) => critActa(a) === "verde").length;
  const fechas = A.map((a) => a.fin || a.inicio).filter(Boolean).sort();
  $("kAcUltima").textContent = fechas.length ? fechaCorta(fechas[fechas.length - 1]) : "—";
}

function pintarActas() {
  const q = ($("buscaActa").value || "").toLowerCase().trim();
  const crit = $("filtroCriticidad").value;
  let lista = estado.actas.filter((a) => {
    if (crit && critActa(a) !== crit) return false;
    if (q) {
      const heno = (nombrePaciente(a) + " " + (a.session_id || "")).toLowerCase();
      if (!heno.includes(q)) return false;
    }
    return true;
  });
  lista = ordenar(lista, estado.ordenActas.col, estado.ordenActas.asc,
    (a, c) => (c === "fin" ? Date.parse(a.fin || a.inicio) || 0 : nombrePaciente(a)));

  const total = lista.length;
  const paginas = Math.max(1, Math.ceil(total / POR_PAGINA));
  if (estado.pagActas >= paginas) estado.pagActas = paginas - 1;
  const pagina = lista.slice(estado.pagActas * POR_PAGINA, (estado.pagActas + 1) * POR_PAGINA);

  $("contadorActas").textContent = total === estado.actas.length
    ? `${total} acta${total === 1 ? "" : "s"}`
    : `${total} de ${estado.actas.length}`;

  const cuerpo = $("cuerpoActas");
  cuerpo.innerHTML = "";
  if (!total) {
    $("tablaActas").hidden = true;
    $("pagActas").hidden = true;
    vacio($("vacioActas"),
      estado.actas.length ? "Ningún acta coincide" : "Todavía no hay actas",
      estado.actas.length ? "Pruebe con otro término o quite el filtro de criticidad."
                          : "Cada llamada finalizada deja aquí su acta con los tres ejes de la decisión.");
    return;
  }
  $("tablaActas").hidden = false;
  $("vacioActas").innerHTML = "";

  pagina.forEach((a) => {
    const d = decActa(a);
    const c = critActa(a);
    const ev = a.evaluacion || a.estado_evaluacion;
    const ac = a.accion_operativa;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="principal">${esc(nombrePaciente(a))}</td>
       <td>${esc(fecha(a.fin || a.inicio))}</td>
       <td>${c ? badge(T_NIVEL[c] || c, c) : "—"}</td>
       <td>${ev ? badge(T_EVAL[ev] || ev, "") : "—"}</td>
       <td>${ac ? badge(T_ACCION[ac] || ac, ac === "escalar" ? "rojo" : "") : "—"}</td>
       <td class="acciones"><button class="btn-fila" type="button">Ver acta</button></td>`;
    tr.querySelector("button").onclick = () => verActa(a.session_id);
    cuerpo.appendChild(tr);
  });

  $("pagActas").hidden = paginas <= 1;
  $("pagActasTexto").textContent = `Página ${estado.pagActas + 1} de ${paginas}`;
  $("pagActasPrev").disabled = estado.pagActas === 0;
  $("pagActasNext").disabled = estado.pagActas >= paginas - 1;
  marcarOrden($("tablaActas"), estado.ordenActas);
}

async function verActa(sessionId) {
  let a = estado.actas.find((x) => x.session_id === sessionId);
  try {
    const r = await fetch(`/api/actas/${sessionId}`);
    if (r.ok) a = await r.json();
  } catch (e) { /* se usa lo que ya había en la lista */ }
  if (!a) return;

  const d = decActa(a);
  const c = critActa(a);
  const ev = a.evaluacion || a.estado_evaluacion;
  const ac = a.accion_operativa;

  let html =
    `<div class="acta-grid">
       <div class="kpi-card"><div class="k">Riesgo clínico</div>
         <div class="v ${c || ""}" style="font-size:19px">${esc(T_NIVEL[c] || "—")}</div></div>
       <div class="kpi-card"><div class="k">Evaluación</div>
         <div class="v" style="font-size:19px">${esc(T_EVAL[ev] || "—")}</div></div>
       <div class="kpi-card"><div class="k">Acción operativa</div>
         <div class="v" style="font-size:15px">${esc(T_ACCION[ac] || "—")}</div></div>
     </div>`;

  html += `<div class="acta-bloque"><h4>Paciente</h4>` +
    [["Nombre", (a.paciente || {}).nombre],
     ["Procedimiento", (a.paciente || {}).procedimiento],
     ["Día postoperatorio", (a.paciente || {}).dia_postoperatorio],
     ["Sesión", a.session_id],
     ["Inicio", a.inicio ? fecha(a.inicio) : null],
     ["Fin", a.fin ? fecha(a.fin) : null],
    ].filter(([, v]) => v != null && v !== "")
     .map(([k, v]) => `<div class="campo"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("") +
    `</div>`;

  const h = a.hallazgos || {};
  const hs = Object.entries(h).filter(([, v]) => v != null && v !== "" && v !== false);
  if (hs.length) {
    html += `<div class="acta-bloque"><h4>Hallazgos</h4>` +
      hs.map(([k, v]) => `<div class="campo"><div class="k">${esc(k.replace(/_/g, " "))}</div>
                          <div class="v">${esc(typeof v === "boolean" ? (v ? "sí" : "no") : v)}</div></div>`).join("") +
      `</div>`;
  }

  if ((a.proximos_pasos || []).length) {
    html += `<div class="acta-bloque"><h4>Próximos pasos</h4><ul class="lista-limpia">` +
      a.proximos_pasos.map((p) => `<li>${esc(p)}</li>`).join("") + `</ul></div>`;
  }
  if ((a.referencias_usadas || []).length) {
    html += `<div class="acta-bloque"><h4>Evidencia citada</h4>` +
      a.referencias_usadas.map((r) => `<div class="extracto" style="margin-bottom:8px">
        ${esc(typeof r === "string" ? r : (r.documento || r.document_title || JSON.stringify(r)))}</div>`).join("") +
      `</div>`;
  }
  if ((a.preguntas_sin_respuesta_en_corpus || []).length) {
    html += `<div class="acta-bloque"><h4>Preguntas sin respuesta en el conocimiento</h4><ul class="lista-limpia">` +
      a.preguntas_sin_respuesta_en_corpus.map((p) => `<li>${esc(p)}</li>`).join("") + `</ul></div>`;
  }
  if ((a.checklist_sin_cubrir || []).length) {
    html += `<div class="acta-bloque"><h4>Temas que no se alcanzaron a cubrir</h4><ul class="lista-limpia">` +
      a.checklist_sin_cubrir.map((p) => `<li>${esc(p)}</li>`).join("") + `</ul></div>`;
  }
  if ((a.transcript || []).length) {
    html += `<div class="acta-bloque"><h4>Conversación</h4><div class="dialogo">` +
      a.transcript.map((t) => `<div class="dialogo-turno ${t.rol === "agente" ? "agente" : ""}">
        <span class="quien">${t.rol === "agente" ? "RONDA" : "Paciente"}</span>
        <div class="txt">${esc(t.texto)}</div></div>`).join("") +
      `</div></div>`;
  }

  html += `<div class="acta-bloque">
    <details class="tec"><summary>Ver JSON técnico</summary>
      <pre class="json" style="margin-top:10px">${esc(JSON.stringify(a, null, 2))}</pre>
    </details></div>`;

  abrirDrawer("Acta · " + nombrePaciente(a), html);
}

/* ══════════════════════════════════════════════════════════════════════
 * MÉTRICAS
 * ═══════════════════════════════════════════════════════════════════ */
async function cargarMetricas() {
  try {
    const r = await fetch("/api/metricas");
    estado.metricas = await r.json();
  } catch (e) { estado.metricas = null; }
  pintarMetricas();
}

const num = (v, suf) => (v == null ? "—" : (typeof v === "number" ? v.toLocaleString("es-CO") : v) + (suf || ""));

function pintarMetricas() {
  const m = estado.metricas;
  if (!m) return;
  $("mLlamadas").textContent = num(m.llamadas);
  $("mTurnos").textContent = num(m.turnos);
  $("mP50").textContent = m.latencia_p50_ms != null ? num(m.latencia_p50_ms, " ms") : "—";
  $("mP95").textContent = m.latencia_p95_ms != null ? num(m.latencia_p95_ms, " ms") : "—";
  $("mMuestra").textContent = m.latencias_muestra ? `sobre ${m.latencias_muestra} turnos` : " ";
  $("mRojas").textContent = num(m.alertas_rojas);
  $("mRag").textContent = num(m.consultas_rag);
  $("mRagLlamada").textContent = m.consultas_rag_por_llamada != null
    ? `${m.consultas_rag_por_llamada} por llamada` : " ";
  $("mTokens").textContent = (m.tokens_entrada_por_turno != null)
    ? `${m.tokens_entrada_por_turno} / ${m.tokens_salida_por_turno}` : "—";
  $("mDocs").textContent = num(m.documentos_activos);
  $("mVectores").textContent = m.vectores != null ? `${m.vectores} vectores` : " ";

  barras($("gRiesgo"), [
    ["Verde", m.riesgo.verde, "verde"],
    ["Amarillo", m.riesgo.amarillo, "amarillo"],
    ["Rojo", m.riesgo.rojo, "rojo"],
  ], "Ninguna llamada registrada todavía.");

  barras($("gAccion"), [
    ["Continuar", m.acciones.continuar, ""],
    ["Repreguntar", m.acciones.repreguntar, ""],
    ["Revisión humana", m.acciones.revision_humana, "amarillo"],
    ["Escalar", m.acciones.escalar, "rojo"],
  ], "Sin acciones registradas.");

  barras($("gEvaluacion"), [
    ["Completa", m.evaluacion.completa, "verde"],
    ["Incompleta", m.evaluacion.incompleta, "amarillo"],
    ["Fallida", m.evaluacion.fallida, "rojo"],
  ], "Sin evaluaciones registradas.");

  pintarSobretriaje(m);
}

function barras(cont, filas, textoVacio) {
  const total = filas.reduce((a, f) => a + (Number(f[1]) || 0), 0);
  if (!total) {
    cont.innerHTML = `<p style="margin:0;color:var(--faint);font-size:13px">${esc(textoVacio)}</p>`;
    return;
  }
  cont.innerHTML = `<div class="barras">` + filas.map(([et, v, cl]) => {
    const n = Number(v) || 0;
    const pct = Math.round((n / total) * 100);
    return `<div class="barra-fila">
              <span class="et">${esc(et)}</span>
              <span class="barra-pista"><i class="${cl}" style="width:${pct}%"></i></span>
              <span class="n">${n}</span>
            </div>`;
  }).join("") + `</div>`;
}

/* Dona SVG dibujada a mano: sin librerías de gráficos. */
function dona(porcentaje, color) {
  const r = 52, c = 2 * Math.PI * r;
  const usado = (porcentaje / 100) * c;
  return `<svg width="132" height="132" viewBox="0 0 132 132" aria-hidden="true">
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="var(--surface-3)" stroke-width="13"/>
    <circle cx="66" cy="66" r="${r}" fill="none" stroke="${color}" stroke-width="13"
            stroke-dasharray="${usado} ${c - usado}" stroke-dashoffset="${c / 4}"
            stroke-linecap="round" transform="rotate(-90 66 66)"/>
    <text x="66" y="63" text-anchor="middle" fill="var(--text)"
          font-size="23" font-weight="700" font-family="var(--font)">${porcentaje}%</text>
    <text x="66" y="81" text-anchor="middle" fill="var(--faint)"
          font-size="10" font-family="var(--font)">a revisión</text>
  </svg>`;
}

function pintarSobretriaje(m) {
  const cont = $("gSobretriaje");
  const b = m.benchmark || {};
  const local = m.verdes_total ? Math.round((m.verdes_a_revision / m.verdes_total) * 100) : null;
  const bench = b.verdes_total ? Math.round((b.verdes_a_revision / b.verdes_total) * 100) : null;

  let html = "";
  if (bench != null) {
    html += `<div class="dona">${dona(bench, "var(--yellow)")}
      <div class="dona-leyenda">
        <div><i style="background:var(--yellow)"></i>Verdes a revisión humana <b>${b.verdes_a_revision}</b></div>
        <div><i style="background:var(--surface-3)"></i>Verdes que siguieron su curso <b>${b.verdes_total - b.verdes_a_revision}</b></div>
        <div><i style="background:var(--green)"></i>Recall de rojo <b>${esc(b.recall_rojo || "—")}</b></div>
        <div><i style="background:var(--green)"></i>Rojo → verde <b>${b.rojo_a_verde}</b></div>
      </div></div>`;
    html += `<div class="nota-honesta"><b>Sin maquillar.</b> ${esc(b.fuente)}.
      Capturar el 100 % de los rojos se paga con sobretriaje:
      ${b.verdes_a_revision} de ${b.verdes_total} conversaciones verdes fueron dirigidas a
      revisión humana. En un servicio real eso es carga adicional para enfermería.</div>`;
  }
  if (local != null && m.verdes_total > 0) {
    html += `<div class="acta-bloque"><h4>En esta instalación</h4>
      <div class="barras"><div class="barra-fila">
        <span class="et">Verdes a revisión</span>
        <span class="barra-pista"><i class="amarillo" style="width:${local}%"></i></span>
        <span class="n">${m.verdes_a_revision}/${m.verdes_total}</span>
      </div></div></div>`;
  }
  cont.innerHTML = html || `<p style="margin:0;color:var(--faint);font-size:13px">Sin datos suficientes todavía.</p>`;
}

/* ══════════════════════════════════════════════════════════════════════
 * Panel lateral y salud
 * ═══════════════════════════════════════════════════════════════════ */
function abrirDrawer(titulo, html) {
  $("drTitulo").textContent = titulo;
  $("drCuerpo").innerHTML = html;
  $("drawer").classList.add("abierto");
  $("drawer").setAttribute("aria-hidden", "false");
  $("velo").classList.add("abierto");
  $("drCerrar").focus();
}
function cerrarDrawer() {
  $("drawer").classList.remove("abierto");
  $("drawer").setAttribute("aria-hidden", "true");
  $("velo").classList.remove("abierto");
}

async function cargarSalud() {
  try {
    const r = await fetch("/api/salud");
    if (!r.ok) return;
    const s = await r.json();
    const marcar = (id, e) => { const n = $(id); if (n) n.dataset.e = e; };
    const motores = (s.tts || {}).motores || {};
    marcar("sTTS", Object.values(motores).some((m) => m && m.disponible) ? "online" : "offline");
    marcar("sRAG", (s.vectores || 0) > 0 ? "online" : "degraded");
    marcar("sSTT", "online");
    marcar("sLLM", s.proveedor ? "online" : "offline");
    if (s.kb_version) {
      const corto = s.kb_version.replace(/^kb_/, "").slice(0, 8);
      $("chipKb").innerHTML = "KB <b>" + corto + "</b>";
      $("kKb").textContent = corto;
    }
    if (s.vectores != null) $("kVectores").textContent = s.vectores;
    if (s.codigo && s.codigo.obsoleto) {
      tostada("error", "El servidor ejecuta código anterior al del disco",
        "<span>Reinícielo para ver los cambios.</span>");
    }
  } catch (e) { /* la cabecera es informativa */ }
}

/* ══════════════════════════════════════════════════════════════════════
 * Arranque
 * ═══════════════════════════════════════════════════════════════════ */
document.addEventListener("DOMContentLoaded", () => {
  irA((location.hash || "#conocimiento").slice(1));
  window.addEventListener("hashchange", () => irA(location.hash.slice(1)));

  // ── Conocimiento
  $("btnElegir").addEventListener("click", (e) => { e.stopPropagation(); $("archivo").click(); });
  $("dropzona").addEventListener("click", () => $("archivo").click());
  $("dropzona").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("archivo").click(); }
  });
  $("archivo").addEventListener("change", (e) => subir(e.target.files[0]));
  ["dragenter", "dragover"].forEach((ev) => $("dropzona").addEventListener(ev, (e) => {
    e.preventDefault(); $("dropzona").classList.add("encima");
  }));
  ["dragleave", "drop"].forEach((ev) => $("dropzona").addEventListener(ev, (e) => {
    e.preventDefault(); $("dropzona").classList.remove("encima");
  }));
  $("dropzona").addEventListener("drop", (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files[0]) subir(e.dataTransfer.files[0]);
  });

  $("buscaDoc").addEventListener("input", () => { estado.pagDocs = 0; pintarDocs(); });
  $("filtroEstadoDoc").addEventListener("change", () => { estado.pagDocs = 0; pintarDocs(); });
  $("pagDocsPrev").addEventListener("click", () => { estado.pagDocs--; pintarDocs(); });
  $("pagDocsNext").addEventListener("click", () => { estado.pagDocs++; pintarDocs(); });
  $("tablaDocs").querySelectorAll("th.ordenable").forEach((th) => th.addEventListener("click", () => {
    const c = th.dataset.col;
    estado.ordenDocs = { col: c, asc: estado.ordenDocs.col === c ? !estado.ordenDocs.asc : true };
    pintarDocs();
  }));
  $("cuerpoDocs").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.ver) verDoc(b.dataset.ver);
    else if (b.dataset.olvido) verificarOlvido(b.dataset.olvido);
    else if (b.dataset.borrar) {
      const d = estado.docs.find((x) => x.doc_id === b.dataset.borrar);
      borrarDoc(b.dataset.borrar, d ? d.titulo : "este documento");
    }
  });

  // ── Alertas y actas
  $("buscaAlerta").addEventListener("input", pintarAlertas);
  $("filtroNivel").addEventListener("change", pintarAlertas);
  $("tablaAlertas").querySelectorAll("th.ordenable").forEach((th) => th.addEventListener("click", () => {
    const c = th.dataset.col;
    estado.ordenAlertas = { col: c, asc: estado.ordenAlertas.col === c ? !estado.ordenAlertas.asc : true };
    pintarAlertas();
  }));
  $("buscaActa").addEventListener("input", () => { estado.pagActas = 0; pintarActas(); });
  $("filtroCriticidad").addEventListener("change", () => { estado.pagActas = 0; pintarActas(); });
  $("pagActasPrev").addEventListener("click", () => { estado.pagActas--; pintarActas(); });
  $("pagActasNext").addEventListener("click", () => { estado.pagActas++; pintarActas(); });
  $("tablaActas").querySelectorAll("th.ordenable").forEach((th) => th.addEventListener("click", () => {
    const c = th.dataset.col;
    estado.ordenActas = { col: c, asc: estado.ordenActas.col === c ? !estado.ordenActas.asc : true };
    pintarActas();
  }));

  // ── Panel lateral
  $("drCerrar").addEventListener("click", cerrarDrawer);
  $("velo").addEventListener("click", cerrarDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") cerrarDrawer(); });

  cargarSalud();
  cargarDocs();
  cargarAlertas();
  cargarActas();
  cargarMetricas();
});
