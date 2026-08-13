/* RONDA — cliente de la llamada de voz.
 * Pipeline: mic → VAD por energía (fin de habla = 900 ms de silencio) →
 * envía el turno completo por WebSocket (binario) → recibe JSON del turno +
 * audio mp3 del agente → reproduce. Barge-in: si el paciente habla mientras
 * suena el agente, se pausa la reproducción de inmediato.
 */

/* ── Parámetros de captura ────────────────────────────────────────────────
 * Calibrados con la sesión a6e0566079, donde el agente se autointerrumpía:
 * un único pico de energía (eco del propio TTS o ruido de sala) abría un
 * turno cuya grabación era casi todo silencio, y Whisper alucinaba texto
 * sobre ese silencio ("Gracias por ver el video.", "."). El bitrate de Opus
 * es constante, así que el servidor no puede detectarlo: la única defensa
 * acústica posible vive aquí.
 */
const TICK_MS = 60;                  // periodo del VAD (1024 muestras ≈ 21 ms)
const UMBRAL_RMS = 0.015;            // piso absoluto heredado; validado con voz real
const FACTOR_SOBRE_RUIDO = 3.0;      // habla = 3× el ruido ambiente medido
const FACTOR_BARGE_IN = 2.5;         // durante el TTS se exige 2,5× el umbral normal
const VENTANAS_CONFIRMA_ESCUCHA = 2; // 120 ms para abrir turno en silencio
const VENTANAS_CONFIRMA_BARGE = 5;   // 300 ms sostenidos para interrumpir al agente
const VENTANAS_MINIMAS_VOZ = 4;      // 240 ms de voz dentro del blob para enviarlo
const FACTOR_HISTERESIS = 0.6;       // seguir hablando exige menos que empezar
const CALIBRACION_MS = 1200;         // ventana inicial para medir el ruido de la sala
const GRACIA_CONFIRMACION_MS = 700;  // si no se confirma en este plazo, se descarta
const MIN_TURNO_MS = 350;            // descarta ruidos muy cortos
const COOLDOWN_TRAS_TTS_MS = 250;    // cola de reverberación tras la voz del agente

/* Endpointing adaptativo. Los 900 ms fijos anteriores se sumaban ÍNTEGROS a la
 * espera percibida en cada turno. Una frase larga y clara ya dio evidencia
 * suficiente de dónde termina, así que puede cerrarse antes; una respuesta
 * corta necesita más margen para no cortar al paciente que aún está pensando;
 * y con ruido alto conviene esperar más para no cerrar en un hueco. */
const SILENCIO_FRASE_MS = 550;       // ≥ 1,2 s de voz acumulada
const SILENCIO_CORTO_MS = 800;       // respuestas de una o dos palabras
const VOZ_PARA_CIERRE_RAPIDO_MS = 1200;
const SILENCIO_MAX_MS = 1100;        // techo con ruido alto

function silencioDeCierre() {
  const vozMs = ventanasVoz * TICK_MS;
  let base = vozMs >= VOZ_PARA_CIERRE_RAPIDO_MS ? SILENCIO_FRASE_MS : SILENCIO_CORTO_MS;
  // Con ruido de fondo alto, el VAD parpadea más: se amplía el margen.
  if (ruidoBase > UMBRAL_RMS / 2) base = Math.min(SILENCIO_MAX_MS, base + 250);
  return base;
}

/* Estados explícitos de la captura. Sin ellos era imposible distinguir
 * "el paciente interrumpe" de "el micrófono oye al propio agente". */
const ESTADO = {
  IDLE: "IDLE",
  LISTENING: "LISTENING",
  PATIENT_SPEAKING: "PATIENT_SPEAKING",
  PROCESSING: "PROCESSING",
  AGENT_SPEAKING: "AGENT_SPEAKING",
  BARGE_IN_CANDIDATE: "BARGE_IN_CANDIDATE",
};

let ws = null;
let sessionId = null;
let mediaRecorder = null;
let chunks = [];
let audioCtx, analyser, micStream, filtroPasoAlto;
let vadTimer = null;
let reproductor = new Audio();
let enLlamada = false;
let turnos = 0;
let citasTotal = 0;

let estado = ESTADO.IDLE;
let estadoPrevio = ESTADO.IDLE;   // a dónde volver si la captura no se confirma
let ruidoBase = 0.004;            // estimación EMA del ruido ambiente
let finCooldown = 0;
let grabando = false;
let inicioGrabacionTs = 0;
let ultimoSonidoTs = 0;
let rachaActiva = 0;
let ventanasVoz = 0;
let rmsPico = 0;
let rmsSuma = 0;
let ventanasTotales = 0;
let confirmado = false;
let iniciadaDuranteTts = false;
let motivoInicio = "";
/* Marcas para la latencia PERCIBIDA (fin de habla → primer audio audible). */
let tsFinHabla = 0;
let tsPrimerAudio = 0;
let tsTextoRecibido = 0;
let msEndpointing = 0;
/* El usuario pulsó Finalizar: un cierre esperado NO es un error de conexión. */
let cierreIntencional = false;

const $ = (id) => document.getElementById(id);

$("btnIniciar").addEventListener("click", iniciarLlamada);
// Envuelto a propósito: pasar el manejador directo le entregaría el MouseEvent
// como etiqueta de estado. El cierre manual (§A8) sigue siendo el de siempre.
$("btnFinalizar").addEventListener("click", () => finalizarLlamada());

async function iniciarLlamada() {
  $("btnIniciar").disabled = true;
  cierreIntencional = false;
  const resp = await fetch("/api/llamada/iniciar", { method: "POST" });
  const data = await resp.json();
  sessionId = data.session_id;
  pintarPaciente(data.paciente);
  agregarBurbuja("agente", data.saludo.texto, []);
  setEstadoLlamada(true, "En seguimiento");
  pintarVoz(data.voz);
  anotarTecnico({
    estado_fsm: data.saludo.estado, sesion: sessionId,
    voz: data.voz ? `${data.voz.voice_id} (${data.voz.provider})` : null,
  });

  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/llamada/${sessionId}`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = onMensaje;
  ws.onerror = () => {
    if (cierreIntencional) return;   // el usuario ya estaba colgando
    mostrarError({
      codigo: "conexion",
      mensaje: "Se perdió la conexión con el servidor.",
      recuperable: false,
    });
  };
  ws.onopen = async () => {
    enLlamada = true;
    $("btnFinalizar").disabled = false;
    await prepararMicrofono();
    reproducirB64(data.audio_b64);
  };
  ws.onclose = () => {
    // Solo es un error si NADIE pidió cerrar. Antes se deducía del código de
    // cierre, y pulsar "Finalizar" acababa mostrando "llamada interrumpida":
    // un error falso justo al terminar bien la llamada.
    const inesperado = !cierreIntencional;
    enLlamada = false;
    if (inesperado)
      mostrarError({
        codigo: "conexion_cerrada",
        mensaje: "La llamada se interrumpió. Vuelva a iniciarla.",
        recuperable: false,
      });
  };
}

/* Un fallo de proveedor JAMÁS puede dejar la interfaz muda aparentando
 * normalidad: se muestra en el transcript y en el estado del micrófono. */
/* Un fallo técnico no puede parecer una alarma clínica: son cosas distintas y
 * quien mira la pantalla debe poder separarlas de un vistazo. Los avisos van
 * FUERA del transcript, con severidad propia; el rojo queda para el riesgo. */
function mostrarError({ codigo, mensaje, recuperable }) {
  const nivel = recuperable ? "warning" : "critical";
  const div = mostrarAviso(nivel, mensaje);
  const meta = document.createElement("small");
  meta.style.cssText = "opacity:.75; margin-left:auto; white-space:nowrap;";
  meta.textContent = recuperable ? "puede seguir hablando" : "la llamada no puede continuar";
  div.appendChild(meta);
  anotarTecnico({ ultimo_error: codigo });
  if (recuperable) {
    setEstadoMic("escuchando", "");
  } else {
    setEstadoMic("error", "");
    setEstadoLlamada(false, "Llamada interrumpida");
  }
}

function onMensaje(ev) {
  if (typeof ev.data !== "string") {
    reproductor.agregarTrozo(ev.data);
    return;
  }
  const m = JSON.parse(ev.data);
  if (m.tipo === "turno") {
    agregarBurbuja("paciente", m.paciente_texto, []);
    agregarBurbuja("agente", m.agente_texto, m.citas, m.response_mode);
    actualizarDecision(m);
    turnos += 1;
    citasTotal += (m.citas || []).length;
    $("mTurnos").textContent = turnos;
    $("mFuentes").textContent = citasTotal;
    anotarTecnico({
      estado_fsm: m.estado_fsm,
      kb_version: m.kb_version,
      response_mode: m.response_mode,
      evidencias_recuperadas: m.evidencias_recuperadas,
    });
    tsTextoRecibido = performance.now();
  } else if (m.tipo === "audio_inicio") {
    audioAbierto = true;
    reproductor.abrirStream(m.formato, m.sample_rate);
  } else if (m.tipo === "audio_fin") {
    reproductor.cerrarStream();
  } else if (m.tipo === "cierre_llamada") {
    // §A7 · el servidor ANUNCIA el cierre; no cuelga. Colgamos nosotros, y
    // solo cuando la despedida haya terminado de sonar.
    esperarAudioYCerrar(m.motivo);
  } else if (m.tipo === "latencia") {
    // Latencia PERCIBIDA: desde que el paciente dejó de hablar hasta que
    // empieza a sonar el agente. Incluye endpointing, red y reproducción.
    const percibida = tsPrimerAudio && tsFinHabla
      ? Math.round(tsPrimerAudio - tsFinHabla) : null;
    $("mLatencia").textContent = percibida != null
      ? `${percibida} ms` : `${m.servidor_a_primer_audio_ms} ms`;
    anotarTecnico({
      latencia_percibida_ms: percibida,
      servidor_a_primer_audio_ms: m.servidor_a_primer_audio_ms,
      endpointing_ms: msEndpointing,
      ...(m.etapas || {}),
    });
    console.info("[latencia]", JSON.stringify({
      percibida_ms: percibida,
      servidor_a_primer_audio_ms: m.servidor_a_primer_audio_ms,
      etapas: m.etapas,
      endpointing_ms: msEndpointing,
      texto_en_pantalla_ms: tsTextoRecibido && tsFinHabla
        ? Math.round(tsTextoRecibido - tsFinHabla) : null,
    }));
  } else if (m.tipo === "sin_audio") {
    agregarBurbuja("agente", m.texto, []);
  } else if (m.tipo === "error") {
    mostrarError(m);
  }
}

/* ── Micrófono + VAD ─────────────────────────────────────────────────── */
async function prepararMicrofono() {
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,   // clave contra la autoconversación
      noiseSuppression: true,
      autoGainControl: true,    // nivela micrófonos de distinta sensibilidad
    },
  });
  // Se registra lo que el navegador APLICÓ de verdad, que no siempre coincide
  // con lo pedido. Solo configuración técnica: nunca audio.
  const pista = micStream.getAudioTracks()[0];
  const aplicado = pista.getSettings ? pista.getSettings() : {};
  console.info("[audio-entrada]", JSON.stringify({
    echoCancellation: aplicado.echoCancellation,
    noiseSuppression: aplicado.noiseSuppression,
    autoGainControl: aplicado.autoGainControl,
    sampleRate: aplicado.sampleRate,
    channelCount: aplicado.channelCount,
    latency: aplicado.latency,
  }));

  audioCtx = new AudioContext();
  const src = audioCtx.createMediaStreamSource(micStream);
  // Paso alto suave: quita ventiladores, retumbe de mesa y golpes graves sin
  // tocar el rango de la voz (la fundamental masculina arranca sobre 85 Hz).
  filtroPasoAlto = audioCtx.createBiquadFilter();
  filtroPasoAlto.type = "highpass";
  filtroPasoAlto.frequency.value = 80;
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  src.connect(filtroPasoAlto);
  filtroPasoAlto.connect(analyser);
  cambiarEstado(ESTADO.LISTENING);
  vadTimer = setInterval(pasoVad, TICK_MS);
  calibrarRuido();
}

/* Calibración por llamada: el piso de ruido de ESTA sala, no el aprendido en
 * una llamada anterior. Se mide en la ventana de silencio natural que hay
 * entre abrir el micrófono y el primer turno. */
function calibrarRuido() {
  const muestras = [];
  const t = setInterval(() => {
    if (grabando || agenteHablando()) return;
    const buf = new Float32Array(analyser.fftSize);
    analyser.getFloatTimeDomainData(buf);
    let s = 0;
    for (const v of buf) s += v * v;
    muestras.push(Math.sqrt(s / buf.length));
  }, TICK_MS);
  setTimeout(() => {
    clearInterval(t);
    if (muestras.length >= 5) {
      muestras.sort((a, b) => a - b);
      ruidoBase = muestras[Math.floor(muestras.length / 2)];   // mediana
      console.info("[vad] ruido calibrado", JSON.stringify({
        ruido_base: +ruidoBase.toFixed(5), muestras: muestras.length,
        umbral_resultante: +umbralActual().toFixed(5),
      }));
    }
  }, CALIBRACION_MS);
}

function cambiarEstado(nuevo) {
  if (estado === nuevo) return;
  estado = nuevo;
  // Solo cambia la ETIQUETA que se muestra. La máquina de estados del VAD,
  // los umbrales y el barge-in quedan exactamente como estaban.
  const rotulo = {
    LISTENING: "escuchando",
    PATIENT_SPEAKING: "voz",
    PROCESSING: "procesando",
    AGENT_SPEAKING: "hablando",
    BARGE_IN_CANDIDATE: "hablando",
    IDLE: "inactivo",
  }[nuevo];
  if (rotulo) setEstadoMic(rotulo);
}

function agenteHablando() {
  return estado === ESTADO.AGENT_SPEAKING || estado === ESTADO.BARGE_IN_CANDIDATE;
}

/* ¿Suena audio del agente ahora mismo, por CUALQUIERA de las dos rutas?
 * Con el motor remoto suena el elemento <audio>; con el local, nodos de Web
 * Audio programados. El VAD y el barge-in usan esta función y por tanto no
 * dependen del motor. */
function agenteAudible() {
  return (!reproductor.paused && !reproductor.ended) || !!reproductor._pcmSonando;
}

/* Umbral adaptativo: se apoya en el ruido ambiente real medido, y se endurece
 * mientras el agente habla para que su propio eco no abra un turno. */
function umbralActual() {
  const base = Math.max(UMBRAL_RMS, ruidoBase * FACTOR_SOBRE_RUIDO);
  return agenteHablando() ? base * FACTOR_BARGE_IN : base;
}

/* HISTÉRESIS: cuesta más entrar en voz que mantenerse dentro. Con un único
 * umbral, los valles naturales entre sílabas caían por debajo y el VAD
 * parpadeaba, rompiendo la racha de confirmación y alargando el cierre. */
function umbralContinuacion() {
  return umbralActual() * FACTOR_HISTERESIS;
}

function pasoVad() {
  if (!enLlamada) return;
  const buf = new Float32Array(analyser.fftSize);
  analyser.getFloatTimeDomainData(buf);
  let sum = 0;
  for (const v of buf) sum += v * v;
  const rms = Math.sqrt(sum / buf.length);
  const ahora = performance.now();
  // Histéresis: el umbral para SEGUIR dentro de la voz es más bajo que el de
  // entrar, de modo que los valles entre sílabas no rompen la racha.
  const umbral = grabando ? umbralContinuacion() : umbralActual();
  const enCooldown = ahora < finCooldown;
  const activo = rms > umbral && !enCooldown;

  if (!activo && !grabando) {
    // El piso de ruido solo se actualiza cuando no hay voz ni captura abierta.
    ruidoBase = ruidoBase * 0.95 + rms * 0.05;
  }

  if (activo) {
    rachaActiva += 1;
    ultimoSonidoTs = ahora;
    if (grabando) {
      ventanasVoz += 1;
      rmsPico = Math.max(rmsPico, rms);
    }
    if (!grabando) {
      // BACKPRESSURE: mientras el servidor procesa el turno anterior no se
      // abre una captura nueva. Evita dos MediaRecorder simultáneos, chunks
      // pisados y turnos superpuestos. El paciente ve "procesando…" y el VAD
      // vuelve a escuchar en cuanto llega la respuesta.
      if (estado === ESTADO.PROCESSING) {
        rachaActiva = 0;
        return;
      }
      // Se graba desde la PRIMERA ventana activa para no perder el ataque de
      // la primera sílaba; el envío se decide después, al cerrar la captura.
      iniciadaDuranteTts = agenteHablando();
      motivoInicio = iniciadaDuranteTts ? "energia_durante_tts" : "energia_en_escucha";
      comenzarGrabacion(ahora, rms);
      if (!iniciadaDuranteTts) estadoPrevio = ESTADO.LISTENING;
      else { estadoPrevio = ESTADO.AGENT_SPEAKING; cambiarEstado(ESTADO.BARGE_IN_CANDIDATE); }
    }
    const requeridas = iniciadaDuranteTts ? VENTANAS_CONFIRMA_BARGE : VENTANAS_CONFIRMA_ESCUCHA;
    if (!confirmado && rachaActiva >= requeridas) {
      confirmado = true;
      if (iniciadaDuranteTts && agenteAudible()) {
        // BARGE-IN REAL: solo aquí se calla al agente, tras voz sostenida.
        // Se corta la ruta que esté sonando, sea <audio> o PCM programado.
        reproductor.pause();
        reproductor.detenerPcm();
      }
      cambiarEstado(ESTADO.PATIENT_SPEAKING);
    }
  } else {
    rachaActiva = 0;
    if (grabando) {
      rmsSuma += rms;
      ventanasTotales += 1;
      if (!confirmado && ahora - inicioGrabacionTs > GRACIA_CONFIRMACION_MS) {
        // Fue un pico aislado (eco del agente, portazo, click): se descarta
        // sin enviarlo y sin haber interrumpido al agente.
        descartarGrabacion("sin_confirmacion");
      } else if (confirmado && ahora - ultimoSonidoTs > silencioDeCierre()) {
        cerrarGrabacion(ahora, "silencio");
      }
    }
  }
}

function comenzarGrabacion(ahora, rms) {
  chunks = [];
  grabando = true;
  confirmado = false;
  inicioGrabacionTs = ahora;
  ultimoSonidoTs = ahora;
  ventanasVoz = 1;
  rmsPico = rms;
  rmsSuma = rms;
  ventanasTotales = 1;
  mediaRecorder = new MediaRecorder(micStream, { mimeType: mimePreferido() });
  mediaRecorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  mediaRecorder.onstop = onGrabacionDetenida;
  mediaRecorder.start();
}

let envioPendiente = null;   // metadatos del turno a enviar, o null si se descarta

function descartarGrabacion(motivo) {
  envioPendiente = null;
  grabando = false;
  detener();
  cambiarEstado(estadoPrevio === ESTADO.AGENT_SPEAKING && agenteAudible()
    ? ESTADO.AGENT_SPEAKING : ESTADO.LISTENING);
  registrarVad({ motivo_fin: motivo, enviado: false });
}

function cerrarGrabacion(ahora, motivo) {
  const duracionMs = Math.round(ahora - inicioGrabacionTs);
  // El paciente dejó de hablar en `ultimoSonidoTs`; lo posterior es la ventana
  // de silencio que confirma el fin. Ese es el t0 de la latencia percibida.
  tsFinHabla = ultimoSonidoTs;
  tsPrimerAudio = 0;
  tsTextoRecibido = 0;
  msEndpointing = Math.round(ahora - ultimoSonidoTs);
  grabando = false;
  const suficienteVoz = ventanasVoz >= VENTANAS_MINIMAS_VOZ;
  const suficienteDuracion = duracionMs >= MIN_TURNO_MS;
  const enviar = confirmado && suficienteVoz && suficienteDuracion;
  envioPendiente = enviar ? {
    tipo: "vad",
    duracion_ms: duracionMs,
    motivo_inicio: motivoInicio,
    motivo_fin: motivo,
    durante_tts: iniciadaDuranteTts,
    barge_in: iniciadaDuranteTts && confirmado,
    ventanas_voz: ventanasVoz,
    ventanas_confirmacion: iniciadaDuranteTts ? VENTANAS_CONFIRMA_BARGE : VENTANAS_CONFIRMA_ESCUCHA,
    rms_pico: +rmsPico.toFixed(5),
    rms_promedio: +(rmsSuma / Math.max(1, ventanasTotales)).toFixed(5),
    ruido_base: +ruidoBase.toFixed(5),
    umbral: +umbralActual().toFixed(5),
  } : null;
  detener();
  if (!enviar) {
    registrarVad({
      motivo_fin: suficienteVoz ? "duracion_insuficiente" : "voz_insuficiente",
      enviado: false, duracion_ms: duracionMs, ventanas_voz: ventanasVoz,
    });
    cambiarEstado(ESTADO.LISTENING);
  } else {
    cambiarEstado(ESTADO.PROCESSING);
  }
}

function detener() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
}

async function onGrabacionDetenida() {
  if (!chunks.length || !envioPendiente) return;
  const meta = envioPendiente;
  envioPendiente = null;
  const blob = new Blob(chunks, { type: mediaRecorder.mimeType });
  const buf = await blob.arrayBuffer();
  if (ws && ws.readyState === WebSocket.OPEN) {
    meta.bytes = buf.byteLength;
    ws.send(JSON.stringify(meta));   // telemetría del VAD, antes del audio
    ws.send(buf);
  }
}

/* Telemetría de capturas DESCARTADAS: nunca llegan al servidor, así que se
 * anotan en consola para poder calibrar el VAD sin guardar audio. */
function registrarVad(info) {
  console.info("[vad]", JSON.stringify(info));
}

function mimePreferido() {
  for (const m of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"])
    if (MediaRecorder.isTypeSupported(m)) return m;
  return "";
}

/* ── Reproducción ────────────────────────────────────────────────────── */
/* ÚNICA vía de reproducción del agente. Saludo, turnos, escalamiento y
 * fallback pasan todos por aquí, así que todos quedan bajo el mismo control
 * de barge-in: no existe ningún audio del agente fuera de la máquina de
 * estados. Reproduce en streaming con MediaSource y, si el navegador no lo
 * soporta, cae a reproducir el bloque completo. */
const MIME_AUDIO = "audio/mpeg";

reproductor.addEventListener("play", () => {
  if (enLlamada) cambiarEstado(ESTADO.AGENT_SPEAKING);
  if (!tsPrimerAudio) tsPrimerAudio = performance.now();
});
reproductor.addEventListener("ended", () => {
  finCooldown = performance.now() + COOLDOWN_TRAS_TTS_MS;
  if (enLlamada && !grabando) cambiarEstado(ESTADO.LISTENING);
});
reproductor.addEventListener("pause", () => {
  finCooldown = performance.now() + COOLDOWN_TRAS_TTS_MS;
});

reproductor.abrirStream = function (formato, sampleRate) {
  tsPrimerAudio = 0;
  this._cola = [];
  this._pendientes = [];
  this._finalizado = false;
  this._formato = formato || "mp3";
  this._sampleRate = sampleRate || 22050;
  // PCM crudo (motor local): no hay contenedor que decodificar, así que se
  // programa por Web Audio. El transcript y el barge-in no cambian.
  if (this._formato === "pcm16") { this._abrirPcm(); return; }
  this._usaMse = !!(window.MediaSource && MediaSource.isTypeSupported(MIME_AUDIO));
  if (!this._usaMse) return;              // respaldo: se acumula y se reproduce al final
  this._ms = new MediaSource();
  this.src = URL.createObjectURL(this._ms);
  this._ms.addEventListener("sourceopen", () => {
    try {
      this._sb = this._ms.addSourceBuffer(MIME_AUDIO);
      this._sb.addEventListener("updateend", () => this._vaciarCola());
      this._vaciarCola();
      this.play().catch(() => {});
    } catch (e) {
      this._usaMse = false;              // cualquier problema → respaldo
      console.warn("[audio] MediaSource no utilizable, se usa respaldo", e);
    }
  }, { once: true });
};

reproductor._vaciarCola = function () {
  if (!this._sb || this._sb.updating) return;
  if (this._cola.length) {
    try { this._sb.appendBuffer(this._cola.shift()); } catch (e) { this._usaMse = false; }
    return;
  }
  if (this._finalizado && this._ms && this._ms.readyState === "open") {
    try { this._ms.endOfStream(); } catch (e) { /* ya cerrado */ }
  }
};

reproductor.agregarTrozo = function (arrayBuffer) {
  this._pendientes = this._pendientes || [];
  if (this._formato === "pcm16") { this._encolarPcm(arrayBuffer); return; }
  this._pendientes.push(arrayBuffer);
  if (this._usaMse) {
    this._cola.push(new Uint8Array(arrayBuffer));
    this._vaciarCola();
  }
};

reproductor.cerrarStream = function () {
  this._finalizado = true;
  if (this._formato === "pcm16") return;   // el PCM se programa al llegar
  if (this._usaMse) { this._vaciarCola(); return; }
  reproducirBloque(new Blob(this._pendientes, { type: MIME_AUDIO }));
};

/* ── Ruta PCM (motor de voz local) ─────────────────────────────────────────
 * Los trozos llegan como PCM de 16 bits mono. Se programan encadenados en el
 * reloj de AudioContext para que suenen sin huecos. El estado AGENT_SPEAKING
 * y el barge-in se emiten igual que con el elemento <audio>, así que el resto
 * de la máquina de estados no distingue de dónde vino el audio. */
reproductor._abrirPcm = function () {
  if (!audioCtx) return;
  this._pcmNodos = [];
  this._pcmProximo = 0;
  this._pcmSonando = false;
};

reproductor._encolarPcm = function (arrayBuffer) {
  if (!audioCtx) return;
  const pcm = new Int16Array(arrayBuffer);
  if (!pcm.length) return;
  const buffer = audioCtx.createBuffer(1, pcm.length, this._sampleRate);
  const canal = buffer.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) canal[i] = pcm[i] / 32768;

  const nodo = audioCtx.createBufferSource();
  nodo.buffer = buffer;
  nodo.connect(audioCtx.destination);
  const ahora = audioCtx.currentTime;
  const inicio = Math.max(ahora, this._pcmProximo || ahora);
  nodo.start(inicio);
  this._pcmProximo = inicio + buffer.duration;
  this._pcmNodos.push(nodo);

  if (!this._pcmSonando) {
    this._pcmSonando = true;
    if (!tsPrimerAudio) tsPrimerAudio = performance.now();
    if (enLlamada) cambiarEstado(ESTADO.AGENT_SPEAKING);
  }
  nodo.onended = () => {
    this._pcmNodos = this._pcmNodos.filter((n) => n !== nodo);
    if (this._finalizado && !this._pcmNodos.length) {
      this._pcmSonando = false;
      finCooldown = performance.now() + COOLDOWN_TRAS_TTS_MS;
      if (enLlamada && !grabando) cambiarEstado(ESTADO.LISTENING);
    }
  };
};

/* Barge-in sobre la ruta PCM: cortar todos los nodos programados. */
reproductor.detenerPcm = function () {
  (this._pcmNodos || []).forEach((n) => { try { n.stop(); } catch (e) { /* ya paró */ } });
  this._pcmNodos = [];
  this._pcmSonando = false;
  this._pcmProximo = 0;
};

function reproducirBloque(blob) {
  reproductor._usaMse = false;
  reproductor.src = URL.createObjectURL(blob);
  reproductor.play().catch(() => {});
}

/* El saludo llega por REST en base64, pero se reproduce por ESTA MISMA vía
 * para que sea interrumpible exactamente igual que cualquier otra respuesta. */
function reproducirB64(b64) {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  tsPrimerAudio = 0;
  reproducirBloque(new Blob([bytes], { type: MIME_AUDIO }));
}

/* ── Cierre ──────────────────────────────────────────────────────────── */

/* §A7 · AUTO-COLGADO. El servidor avisa de que la conversación terminó, pero
 * el último audio todavía está sonando. Cortarlo a mitad de la despedida sería
 * peor que no cerrar, así que aquí NO hay temporizador de seguridad: se espera
 * a que la reproducción se agote de verdad.
 *
 * Se exigen varias comprobaciones seguidas en silencio porque entre trozo y
 * trozo del stream hay microcortes en los que `agenteAudible()` da falso sin
 * que el audio haya terminado. */
let cerrandoAuto = false;
let audioAbierto = false;
const SILENCIOS_PARA_CERRAR = 4;

function esperarAudioYCerrar(motivo) {
  if (cerrandoAuto) return;
  cerrandoAuto = true;
  setEstadoMic("inactivo");
  clearInterval(vadTimer);          // deja de escuchar: la entrevista terminó
  grabando = false;
  envioPendiente = null;
  if (!audioAbierto) { cerrarPorCierreNatural(motivo); return; }
  let silencios = 0;
  const tic = setInterval(() => {
    if (agenteAudible()) { silencios = 0; return; }
    if (++silencios < SILENCIOS_PARA_CERRAR) return;
    clearInterval(tic);
    cerrarPorCierreNatural(motivo);
  }, 120);
}

async function cerrarPorCierreNatural(motivo) {
  await finalizarLlamada("Seguimiento finalizado");
  aviso("info", motivo === "seguimiento_completado"
    ? "Seguimiento finalizado. El acta quedó registrada."
    : "Llamada finalizada: " + motivo);
}

async function finalizarLlamada(etiqueta) {
  // Se marca ANTES de tocar el WebSocket: cualquier onclose/onerror que
  // dispare el cierre ya sabrá que fue el usuario quien colgó.
  cierreIntencional = true;
  $("btnFinalizar").disabled = true;
  clearInterval(vadTimer);
  envioPendiente = null;
  grabando = false;
  reproductor.pause();
  reproductor.detenerPcm();
  cambiarEstado(ESTADO.IDLE);
  if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
  if (ws) ws.close(1000, "finalizada por el usuario");
  const resp = await fetch(`/api/llamada/${sessionId}/finalizar`, { method: "POST" });
  const acta = await resp.json();
  $("resumenJson").textContent = JSON.stringify(acta, null, 2);
  $("dlgResumen").showModal();
  $("btnIniciar").disabled = false;
  setEstadoMic("inactivo");
  setEstadoLlamada(false, typeof etiqueta === "string" ? etiqueta : "Finalizada");
  cerrandoAuto = false;
  audioAbierto = false;
}


/* ══════════════════════════════════════════════════════════════════════════
 * PRESENTACIÓN
 * Solo pinta lo que el servidor envía. Ningún valor de esta sección se
 * calcula aquí: si un dato no viene en el turno, no se muestra. Nada de "N/D"
 * inventados ni porcentajes de adorno.
 * ═════════════════════════════════════════════════════════════════════════ */

const MARGEN_FONDO = 90;
const evidenciasVistas = new Map();   // evidence_id → cita, para el drawer

function alFinal(c) {
  return c.scrollHeight - c.scrollTop - c.clientHeight < MARGEN_FONDO;
}

/* Los iconos llevan SIEMPRE tamaño propio. Un <svg> sin width/height y sin
   regla CSS que lo dimensione se dibuja a 300×150 —el tamaño por defecto de un
   elemento reemplazado—, que es exactamente como salía el escudo del panel de
   evidencia: ocupando media columna. */
function icono(id, clase, px) {
  const s = px || 15;
  return `<svg class="${clase || ""}" width="${s}" height="${s}"><use href="#${id}"/></svg>`;
}

/* ── Conversación ────────────────────────────────────────────────────── */
function agregarBurbuja(rol, texto, citas, modo) {
  const cont = $("transcript");
  const portada = $("idle");
  if (portada) portada.remove();
  const seguir = alFinal(cont);

  const t = document.createElement("div");
  t.className = `turno ${rol}`;

  const quien = document.createElement("div");
  quien.className = "turno-quien";
  quien.innerHTML =
    `<span class="av">${rol === "agente"
      ? '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#04121F" stroke-width="3" stroke-linecap="round"><path d="M12 4v16M6 9v6M18 9v6"/></svg>'
      : '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="#93A6BE" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>'}</span>` +
    (rol === "agente" ? "RONDA" : "Paciente");
  t.appendChild(quien);

  const b = document.createElement("div");
  b.className = "burbuja";
  b.textContent = texto;
  t.appendChild(b);

  if (citas && citas.length) {
    t.appendChild(chipsEvidencia(citas));
  } else if (rol === "agente" && modo === "abstained") {
    const n = document.createElement("span");
    n.className = "nota-turno";
    n.innerHTML = icono("i-info") + "<span>Sin evidencia suficiente en el conocimiento activo</span>";
    t.appendChild(n);
  }

  cont.appendChild(t);
  if (seguir) cont.scrollTop = cont.scrollHeight;
}

/* La cita la construye el servidor desde objetos Evidence; aquí se le da
 * forma y se hace ABRIBLE. Ver la fuente exacta que sostuvo una frase es la
 * mejor prueba de que el sistema no improvisa, así que merece un panel, no
 * una nota al pie. */
function chipsEvidencia(citas) {
  const cont = document.createElement("div");
  cont.className = "fuentes";
  citas.forEach((c) => {
    if (c.evidence_id) evidenciasVistas.set(c.evidence_id, c);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip-ev";
    chip.dataset.evidenceId = c.evidence_id || "";
    chip.setAttribute("aria-label", `Ver evidencia de ${c.documento || "documento"}`);
    chip.innerHTML = icono("i-doc") +
      `<span class="doc"></span>` +
      (c.chunk != null ? `<span class="frag">frag ${c.chunk}</span>` : "");
    chip.querySelector(".doc").textContent = (c.documento || "documento").replace(/\.(pdf|txt|md)$/i, "");
    chip.addEventListener("click", () => abrirEvidencia(c));
    cont.appendChild(chip);
  });
  return cont;
}

/* ── Drawer de evidencia ─────────────────────────────────────────────── */
function abrirEvidencia(c) {
  const campos = [
    ["Documento", c.documento],
    ["Fragmento", c.chunk != null ? `#${c.chunk}` : null],
    ["Evidence ID", c.evidence_id],
    ["KB version", c.kb_version],
    ["Distancia", c.distancia != null ? c.distancia : null],
    ["SHA-256", c.sha256 ? String(c.sha256).slice(0, 24) + "…" : null],
    ["doc_id", c.doc_id],
  ].filter(([, v]) => v != null && v !== "");

  const cuerpo = $("drCuerpo");
  cuerpo.innerHTML = "";

  const sello = document.createElement("div");
  sello.className = "sello";
  sello.innerHTML = icono("i-shield") + "<span>Esta evidencia sustentó la respuesta del turno.</span>";
  cuerpo.appendChild(sello);

  if (c.extracto) {
    const ex = document.createElement("div");
    ex.className = "extracto";
    ex.textContent = c.extracto;
    cuerpo.appendChild(ex);
  }

  campos.forEach(([k, v]) => {
    const d = document.createElement("div");
    d.className = "campo";
    const ek = document.createElement("span"); ek.className = "k"; ek.textContent = k;
    const ev = document.createElement("span"); ev.className = "v"; ev.textContent = String(v);
    d.append(ek, ev);
    cuerpo.appendChild(d);
  });

  $("drawer").classList.add("abierto");
  $("drawer").setAttribute("aria-hidden", "false");
  $("velo").classList.add("abierto");
  $("drCerrar").focus();
}

function cerrarEvidencia() {
  $("drawer").classList.remove("abierto");
  $("drawer").setAttribute("aria-hidden", "true");
  $("velo").classList.remove("abierto");
}

/* ── Decisión clínica: tres ejes, un solo módulo ─────────────────────── */
const T_RIESGO = {
  verde: "Sin señales de alarma",
  amarillo: "Hallazgos que requieren revisión",
  rojo: "Escalamiento activo",
};
const T_EVAL = { completa: "Completa", incompleta: "Incompleta", fallida: "Fallida" };
const T_ACCION = {
  continuar: "Continuar seguimiento", repreguntar: "Repreguntar",
  revision_humana: "Revisión humana", escalar: "Escalar",
};
const T_DOMINIO = {
  dolor: "Dolor", temperatura: "Temperatura", herida: "Herida",
  movilidad: "Movilidad", alimentacion: "Alimentación", medicacion: "Medicación",
};

function actualizarDecision(m) {
  const riesgo = m.riesgo_clinico || m.semaforo;
  if (riesgo) {
    $("decision").dataset.riesgo = riesgo;
    $("dRiesgo").textContent = riesgo.charAt(0).toUpperCase() + riesgo.slice(1);
    $("dRiesgoDet").textContent = m.alerta ? "Alerta enviada a enfermería" : (T_RIESGO[riesgo] || "");
  }
  if (m.estado_evaluacion) {
    const e = $("dEval");
    e.dataset.v = m.estado_evaluacion;
    e.textContent = T_EVAL[m.estado_evaluacion] || m.estado_evaluacion;
    $("dEvalDet").textContent = m.razon_de_incertidumbre || "";
  }
  if (m.accion_operativa) {
    const a = $("dAccion");
    a.dataset.v = m.accion_operativa;
    a.textContent = T_ACCION[m.accion_operativa] || m.accion_operativa;
  }
  if (m.kb_version) {
    $("chipKb").innerHTML = "KB <b>" + m.kb_version.replace(/^kb_/, "").slice(0, 8) + "</b>";
  }
  if (m.cobertura) pintarCobertura(m.cobertura);
}

/* Hace visible el eje que casi ningún sistema separa: qué se evaluó de
 * verdad, dominio por dominio. */
function pintarCobertura(cob) {
  const cont = $("cobertura");
  const dominios = ["dolor", "temperatura", "herida", "movilidad", "alimentacion"];
  cont.innerHTML = "";
  dominios.forEach((d) => {
    const info = cob[d];
    const estado = !info ? "pendiente" : (info.positive ? "positivo" : "evaluado");
    const etiqueta = { pendiente: "Pendiente", evaluado: "Evaluado", positivo: "Hallazgo" }[estado];
    const fila = document.createElement("div");
    fila.className = "dom";
    fila.dataset.e = estado;
    fila.innerHTML =
      `<span class="dom-barra"></span>` +
      `<span class="dom-n">${T_DOMINIO[d] || d}</span>` +
      `<span class="dom-e">${etiqueta}</span>`;
    cont.appendChild(fila);
  });
}

/* ── Paciente ────────────────────────────────────────────────────────── */
function pintarPaciente(p) {
  const proc = p.procedimiento_nombre || p.procedimiento || "";
  const filas = [
    ["Edad", p.edad != null ? `${p.edad} años` : null],
    ["Procedimiento", proc],
    ["Día postop.", p.dia_postoperatorio],
  ].filter(([, v]) => v != null && v !== "");

  const cont = $("fichaPaciente");
  cont.innerHTML = `<div style="font-size:15px;font-weight:650;margin-bottom:10px">${p.nombre}</div>`;
  filas.forEach(([k, v]) => {
    const f = document.createElement("div");
    f.className = "pac-fila";
    f.innerHTML = `<span class="pac-k">${k}</span><span class="pac-v"></span>`;
    f.querySelector(".pac-v").textContent = String(v);
    cont.appendChild(f);
  });

  const comor = p.comorbilidades || [];
  if (comor.length) {
    const f = document.createElement("div");
    f.className = "pac-fila";
    f.style.marginTop = "8px";
    f.innerHTML = `<span class="pac-k">Comorbilidades</span>`;
    const tags = document.createElement("div");
    tags.className = "tags";
    comor.forEach((c) => {
      const t = document.createElement("span");
      t.className = "tag";
      t.textContent = c;
      tags.appendChild(t);
    });
    f.appendChild(tags);
    cont.appendChild(f);
  }

  $("hsNombre").textContent = p.nombre;
  $("hsMeta").textContent = `${proc} · Día ${p.dia_postoperatorio}`;
  $("ipNombre").textContent = p.nombre;
  $("ipMeta").textContent = `${proc} · Día ${p.dia_postoperatorio}`;
}

/* ── Estado de voz ───────────────────────────────────────────────────── */
const V_ESTADO = {
  inactivo:   "Micrófono inactivo",
  escuchando: "Escuchando al paciente",
  voz:        "Detectando su voz",
  procesando: "Procesando",
  hablando:   "RONDA está hablando",
  "sin-voz":  "Voz temporalmente no disponible",
};

function setEstadoMic(clase) {
  // Traduce el vocabulario del VAD al de la interfaz. El VAD, sus umbrales y
  // el barge-in no se tocan: solo cambia la etiqueta.
  const clave = V_ESTADO[clase] ? clase : "inactivo";
  $("dock").dataset.e = clave;
  $("dockEstado").textContent = V_ESTADO[clave];
}

function pintarVoz(voz) {
  if (!voz) return;
  const idioma = voz.locale === "es-CO" ? "Español (Colombia)" : (voz.locale || "");
  const nombre = (voz.persona_id || "").replace(/^ronda_/, "");
  const bonito = nombre ? nombre.charAt(0).toUpperCase() + nombre.slice(1) : "";
  $("dockVoz").textContent = bonito ? `${bonito} · ${idioma}` : idioma;
  $("hsVoz").textContent = bonito || idioma;
}

function setEstadoLlamada(activa, texto) {
  $("hsEstado").dataset.on = activa ? "true" : "false";
  $("hsEstadoTxt").textContent = texto;
  $("heroSesion").hidden = false;
}

/* ── Telemetría ──────────────────────────────────────────────────────── */
const tecnico = {};
function anotarTecnico(datos) {
  Object.assign(tecnico, datos);
  const dl = $("tecTabla");
  dl.innerHTML = "";
  Object.entries(tecnico).forEach(([k, v]) => {
    if (v == null || v === "") return;
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = String(v);
    dl.append(dt, dd);
  });
}

/* ── Salud del sistema ───────────────────────────────────────────────── */
async function cargarSalud() {
  try {
    const r = await fetch("/api/salud");
    if (!r.ok) return;
    const s = await r.json();
    const motores = (s.tts || {}).motores || {};
    marcar("sTTS", Object.values(motores).some((m) => m && m.disponible) ? "online" : "offline");
    marcar("sRAG", (s.vectores || 0) > 0 ? "online" : "degraded");
    marcar("sSTT", "online");
    marcar("sLLM", s.proveedor ? "online" : "offline");
    if (s.kb_version) {
      $("chipKb").innerHTML = "KB <b>" + s.kb_version.replace(/^kb_/, "").slice(0, 8) + "</b>";
    }
    anotarTecnico({
      proveedor: s.proveedor, modelo: s.modelo,
      documentos: s.documentos_indexados, vectores: s.vectores,
    });
    if (s.codigo && s.codigo.obsoleto) {
      aviso("warning", "El servidor ejecuta código anterior al del disco. Reinícielo.");
    }
  } catch (e) { /* la cabecera es informativa; su fallo no rompe la llamada */ }
}
function marcar(id, estado) { const e = $(id); if (e) e.dataset.e = estado; }

/* ── Avisos: técnico ≠ clínico ───────────────────────────────────────── */
function aviso(nivel, mensaje) {
  const div = document.createElement("div");
  div.className = "aviso";
  div.dataset.n = nivel;
  div.innerHTML = icono(nivel === "info" ? "i-info" : "i-warn");
  const s = document.createElement("span");
  s.textContent = mensaje;
  div.appendChild(s);
  $("avisos").appendChild(div);
  return div;
}

function mostrarError({ codigo, mensaje, recuperable }) {
  const div = aviso(recuperable ? "warning" : "critical", mensaje);
  const meta = document.createElement("small");
  meta.style.cssText = "margin-left:auto;opacity:.75;white-space:nowrap";
  meta.textContent = recuperable ? "puede seguir hablando" : "la llamada no puede continuar";
  div.appendChild(meta);
  anotarTecnico({ ultimo_error: codigo });
  if (recuperable) { setEstadoMic("escuchando"); }
  else { setEstadoMic("sin-voz"); setEstadoLlamada(false, "Interrumpida"); }
}

/* ── Arranque ────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
  cargarSalud();
  /* Los cinco dominios se listan como Pendiente desde el arranque: el carril
     clínico muestra qué se VA a evaluar, no un hueco esperando datos. */
  pintarCobertura({});
  fetch("/api/paciente-demo").then((r) => (r.ok ? r.json() : null)).then((p) => {
    if (p) { pintarPaciente(p); $("heroSesion").hidden = true; }
  }).catch(() => {});
  $("btnCerrarActa").addEventListener("click", () => $("dlgResumen").close());
  $("drCerrar").addEventListener("click", cerrarEvidencia);
  $("velo").addEventListener("click", cerrarEvidencia);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("drawer").classList.contains("abierto")) cerrarEvidencia();
  });
});


