# RONDA — Informe final (Tech Sphere Challenge 2026)

> PLANTILLA: completar los bloques marcados 🖊️ antes de la entrega.

## 1. Resumen ejecutivo

RONDA es un sistema de **triaje conversacional auditable** para seguimiento
postoperatorio por voz. No es un chatbot que suena bien: es un motor de decisión
clínica con dos carriles independientes (reglas deterministas + LLM extractor) cuya
fusión jamás rebaja una alarma, con trazabilidad de cada afirmación clínica al
documento fuente y un ciclo de conocimiento vivo con olvido verificable.

## 2. Declaración de modelo (compuerta G3)

- **Modelo razonador:** Llama 3.1 70B vía Groq, en su versión vigente publicada por
  el proveedor (`llama-3.3-70b-versatile`), acogiéndonos a la cláusula de la ficha
  técnica que autoriza usar el sucesor vigente del mismo proveedor.
- **Fallback declarado:** Gemini 1.5 Flash (lista permitida), conmutable por `.env`.
- **Justificación:** reto de voz → prioridad a latencia de inferencia (LPU de Groq)
  manteniendo capacidad 70B para seguir protocolos estrictos, extraer slots de
  lenguaje ambiguo/regional y resistir manipulación.
- STT: Whisper Large V3 (Groq). TTS: edge-tts `es-CO-SalomeNeural` (alternativa
  local Kokoro-82M). Embeddings: 🖊️ (indicar el usado en la entrega: BAAI/bge-m3
  o multilingual-MiniLM). Vector store: ChromaDB.

## 3. Arquitectura

Ver [`docs/diagrama.md`](diagrama.md) (nombres = módulos reales). 🖊️ Insertar aquí
export PNG de los tres diagramas para lectura sin renderizador Mermaid.

## 4. Decisiones de diseño y trade-offs

| Decisión | Alternativa descartada | Por qué |
|---|---|---|
| Doble carril con fusión `max` | Solo LLM con guardrails de prompt | Un guardrail de prompt es inyectable y no auditable; una regex no obedece instrucciones. El costo: mantener el YAML de banderas. |
| Cita o silencio (umbral de distancia) | Responder siempre "con mejor esfuerzo" | En salud, una respuesta sin fuente es un pasivo. El costo: más momentos "lo dejo anotado para la enfermera" — que convertimos en feature (aparece en el acta). |
| Turnos discretos con VAD en cliente | Streaming full-duplex | Menor complejidad y latencia ya competitiva vía Groq; barge-in cubre la interrupción natural. |
| Métricas generadas desde logs | Redactarlas a mano | La rúbrica castiga inconsistencias; aquí son imposibles por construcción. |

## 5. Seguridad de misión

- Prompt de misión bloqueada (`prompts/system_agente.md`): el texto del paciente se
  enmarca explícitamente como datos, nunca instrucciones.
- Detección `fuera_de_mision` en el extractor + respuesta fija de recentrado.
- El carril determinista es estructuralmente inmune a inyección.
- 🖊️ Pegar 2-3 capturas de intentos de manipulación fallidos durante las pruebas.

## 6. Evaluación

Toda cifra va acompañada de la **huella SHA-256 del motor** que la produjo
(`python eval/huellas.py`), de modo que cualquiera pueda comprobar que dos
benchmarks salieron del mismo código.

🖊️ Pegar la salida de:

```bash
python eval/estabilidad.py --kit-root ruta/al/kit      # 320 conversaciones + 6 bloques
python eval/gemini_full.py --kit-root ruta/al/kit --checkpoint eval/ckpt.jsonl
```

Reportar, en este orden:

1. Matriz de confusión global y por capa (limpia / ruidosa).
2. **Métricas pareadas por caso**: recall rojo limpio, ruidoso y pareado sobre
   los 12 `caso_id` rojos — no sobre las 24 conversaciones, que no son
   observaciones independientes.
3. `ROJO→VERDE` (el fallo que no puede ocurrir), `ROJO→AMARILLO`, `VERDE→ROJO`
   y `VERDE→AMARILLO`. **El sobretriaje se publica, no se esconde.**
4. Desglose del sobretriaje: cuánto es riesgo y cuánto es evaluación
   incompleta. Son dos cosas distintas y el acta las separa.
5. Estabilidad por bloque: media, mínimo y máximo. Sin intervalos de confianza.

## 7. Métricas de operación

🖊️ Pegar la salida de `python scripts/metrics.py` (misma tabla del README).

## 8. Limitaciones honestas

- Umbrales clínicos de referencia, pendientes de validación por personal de salud.
- VAD por energía: sensible a ambientes muy ruidosos (mitigación: umbral configurable).
- Sin autenticación en la consola (fuera de alcance del reto; roadmap: SSO clínico).
- **El dataset tiene 12 `caso_id` rojos.** Cualquier métrica de recall rojo se
  apoya en esa base: un solo caso mueve el resultado más de 8 puntos. Por eso se
  reporta el recall PAREADO por caso (detectado en capa limpia Y ruidosa) y no
  se publican intervalos de confianza, que con esta n serían decorativos.
- **La validación en 6 bloques mide estabilidad, no generalización.** Parte del
  dataset se usó durante el diseño del motor; los bloques dicen si el
  comportamiento es consistente entre subconjuntos, no si aguantaría pacientes
  nuevos. Para lo segundo haría falta material que no se haya tocado nunca.
- **Sobretriaje a amarillo, deliberado.** El sistema escala a amarillo más de lo
  que las etiquetas marcan. No es un error de clasificación sino la asimetría
  buscada: un amarillo cuesta una revisión de enfermería, un rojo perdido cuesta
  un reingreso. El acta separa "amarillo porque hay riesgo" de "verde que no
  pudimos terminar de comprobar", y esa segunda categoría ya no contamina la
  etiqueta clínica.
- **La exactitud es la métrica equivocada aquí, y se puede demostrar.** La
  configuración con mejor exactitud medida (72,5%, motor sin composición) es la
  que mandó **siete casos rojos a verde**. La configuración final tiene exactitud
  parecida y cero. Cualquier lectura del sistema que ordene por exactitud
  premiará el comportamiento peligroso.
- 🖊️ Añadir las encontradas en pruebas de voz.

---

## 9. Guion del video (≤ tiempo oficial)

1. **(20 s) Gancho:** "En el postoperatorio, la complicación no avisa en el control:
   avisa en la casa. RONDA es la ronda que nunca se salta a un paciente."
2. **(90 s) Demo llamada feliz:** saludo → checklist → pregunta clínica del paciente
   → respuesta con cita visible (doc + chunk) → semáforo verde → acta.
3. **(60 s) Demo alarma:** paciente reporta señal roja a mitad de llamada →
   escalamiento inmediato + acta de alerta en la consola.
4. **(45 s) Conocimiento vivo:** subir doc nunca visto → preguntar → responde con
   cita → eliminar → **Verificar olvido** en pantalla.
5. **(30 s) Métricas:** tabla generada desde logs + recall rojo del eval.

### Pregunta 1 (frente a cámara): ¿Qué problema resuelve y por qué así?

🖊️ Base sugerida: "El problema no es hacer llamadas: es decidir con seguridad
cuáles de esas llamadas necesitan un humano YA, y poder demostrar por qué. Por eso
RONDA no optimiza sonar humano; optimiza ser auditable: cada decisión tiene dos
carriles registrados y cada afirmación una fuente. En salud, la confianza no se
gana con fluidez sino con trazabilidad."

### Pregunta 2 (frente a cámara): decisión técnica más difícil / alternativas / con más tiempo

🖊️ Base sugerida: "La decisión más difícil fue no dejar que el LLM decidiera solo.
Evalué tres opciones: solo LLM con guardrails (descartada: inyectable), solo reglas
(descartada: rígida ante lenguaje regional ambiguo), y el doble carril con fusión
max, que tomé: el LLM entiende 'me siento maluco', la regla garantiza que 39 grados
son rojos, y ninguno puede silenciar al otro. El riesgo que acepté: regex que no
anticipen un regionalismo — lo mitigo con el harness de evaluación sobre los 160
casos ground truth. Con dos semanas más, cerraría el ciclo: que cada falso negativo
del eval proponga automáticamente el patrón faltante al YAML, con revisión humana."

---

## 10. Checklist de entrega

- [ ] Repo público con este informe, README y diagrama
- [ ] `python scripts/metrics.py` ejecutado y pegado (README + §7)
- [ ] Eval ejecutado y pegado (§6)
- [ ] Levantamiento cronometrado en máquina limpia < 15 min (compuerta G2)
- [ ] `.env` de evaluación con credenciales entregado por el canal indicado
- [ ] Video subido con las 2 preguntas frente a cámara
