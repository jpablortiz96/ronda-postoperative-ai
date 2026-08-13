/* RONDA — consola de administración del conocimiento vivo. */
const $ = (id) => document.getElementById(id);

$("btnSubir").addEventListener("click", subir);
cargarTodo();
setInterval(cargarTodo, 6000);

async function subir() {
  const input = $("archivo");
  if (!input.files.length) return alert("Selecciona un archivo .pdf, .txt o .md");
  const fd = new FormData();
  fd.append("file", input.files[0]);
  $("btnSubir").disabled = true;
  $("btnSubir").textContent = "Procesando…";
  try {
    const resp = await fetch("/api/docs", { method: "POST", body: fd });
    const data = await resp.json();
    if (data.duplicado) alert("Documento ya indexado (mismo SHA-256). No se duplicó.");
    if (data.estado === "error") alert("Error de ingesta: " + (data.error || ""));
  } finally {
    $("btnSubir").disabled = false;
    $("btnSubir").textContent = "Subir documento";
    input.value = "";
    cargarTodo();
  }
}

async function cargarTodo() {
  await Promise.all([cargarDocs(), cargarAlertas(), cargarActas()]);
}

async function cargarDocs() {
  const docs = await (await fetch("/api/docs")).json();
  const tbody = $("tablaDocs");
  tbody.innerHTML = "";
  if (!docs.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="color:var(--texto-2)">
      Sin documentos. Sube el corpus clínico del reto para alimentar el RAG.</td></tr>`;
    return;
  }
  for (const d of docs) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td title="SHA-256: ${d.sha256}">${d.titulo}</td>
      <td><span class="badge ${d.estado}">${d.estado === "disponible" ? "procesado y disponible" : d.estado}</span></td>
      <td>${d.chunks ?? "—"}</td>
      <td>${d.metodo_extraccion ?? "—"}</td>
      <td class="fila-acciones">
        <button class="secundario" onclick="verificarOlvido('${d.doc_id}')">Verificar olvido</button>
        <button class="peligro" onclick="eliminarDoc('${d.doc_id}', '${d.titulo.replaceAll("'", "")}')">Eliminar</button>
      </td>`;
    tbody.appendChild(tr);
  }
}

async function eliminarDoc(docId, titulo) {
  if (!confirm(`¿Eliminar "${titulo}"? El agente lo olvidará de inmediato.`)) return;
  const resp = await fetch(`/api/docs/${docId}`, { method: "DELETE" });
  const data = await resp.json();
  mostrarVerificacion({ mensaje: "Documento eliminado. Tombstone registrado en el manifiesto.", ...data });
  cargarTodo();
}

async function verificarOlvido(docId) {
  const resp = await fetch(`/api/docs/${docId}/verificar-olvido`, { method: "POST" });
  const data = await resp.json();
  mostrarVerificacion(data);
}

function mostrarVerificacion(data) {
  const pre = $("salidaVerificacion");
  pre.style.display = "block";
  pre.textContent = JSON.stringify(data, null, 2);
}

async function cargarAlertas() {
  const alertas = await (await fetch("/api/alertas")).json();
  const tbody = $("tablaAlertas");
  tbody.innerHTML = alertas.length ? "" :
    `<tr><td colspan="4" style="color:var(--texto-2)">Sin alertas.</td></tr>`;
  for (const a of alertas.slice(0, 8)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${a.paciente?.nombre ?? "—"}</td>
      <td><span class="badge ${a.nivel}">${a.nivel}</span></td>
      <td>${(a.timestamp || "").slice(11, 19)}</td>
      <td><button class="secundario" onclick='verDetalle("Acta de alerta", ${JSON.stringify(JSON.stringify(a))})'>Ver</button></td>`;
    tbody.appendChild(tr);
  }
}

async function cargarActas() {
  const actas = await (await fetch("/api/actas")).json();
  const tbody = $("tablaActas");
  tbody.innerHTML = actas.length ? "" :
    `<tr><td colspan="3" style="color:var(--texto-2)">Sin llamadas registradas.</td></tr>`;
  for (const a of actas.slice(0, 8)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${a.paciente}</td>
      <td><span class="badge ${a.criticidad_final}">${a.criticidad_final}</span>${a.escalado ? " ⚠" : ""}</td>
      <td><button class="secundario" onclick="verActa('${a.session_id}')">Ver</button></td>`;
    tbody.appendChild(tr);
  }
}

async function verActa(sessionId) {
  const acta = await (await fetch(`/api/actas/${sessionId}`)).json();
  verDetalle("Acta de llamada", JSON.stringify(acta));
}

function verDetalle(titulo, jsonStr) {
  $("dlgTitulo").textContent = titulo;
  $("dlgJson").textContent = JSON.stringify(JSON.parse(jsonStr), null, 2);
  $("dlgDetalle").showModal();
}
