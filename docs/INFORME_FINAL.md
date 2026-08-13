# Informe final — RONDA

**Inteligencia postoperatoria**
Tech Sphere Challenge 2026 · Agosto de 2026
Autor: Juan Pablo Enriquez Ortiz

---

## 1. Resumen ejecutivo

RONDA es un agente de voz que realiza seguimiento telefónico a pacientes
postoperatorios en español colombiano y produce una decisión de triaje
**auditable**: cada nivel de riesgo puede rastrearse hasta la regla que lo
disparó, y cada afirmación clínica hasta el fragmento documental que la
sostiene.

La tesis del proyecto es que en un dominio clínico el problema difícil no es
que el agente hable bien, sino que **no mienta y no tranquilice cuando no
debe**. De ahí las tres invariantes que gobiernan todo el sistema:

1. **Una alarma no se rebaja.** El modelo de lenguaje puede subir un nivel de
   riesgo; jamás bajarlo.
2. **No haber evaluado no es haber descartado.** Un dominio sin respuesta queda
   como desconocido y arrastra la evaluación a `incompleta`.
3. **Sin evidencia no hay afirmación clínica.** Una compuerta verifica cada
   oración antes de que llegue al sintetizador de voz.

Estado verificado a la entrega: 533 comprobaciones automatizadas sin fallos,
0 rojos clasificados como verdes sobre el dataset oficial, ciclo completo de
conocimiento vivo (aprender / citar / olvidar) demostrable en vivo, y arranque
en sala limpia medido en 2 min 09 s.

## 2. Contexto del problema

La ventana de riesgo tras una cirugía no está en el quirófano sino en el
domicilio, durante los días siguientes al alta. Infección de herida, fiebre
persistente, dolor que no cede, sangrado tardío o intolerancia a la vía oral
son complicaciones que se detectan **preguntando de forma sistemática**.

El obstáculo es de capacidad: preguntar a todos los pacientes operados, todos
los días, con la misma rigurosidad, excede el personal de enfermería
disponible. En la práctica los seguimientos se espacian, se acortan o se
saltan, y una parte de los pacientes reaparece por urgencias con una
complicación que llevaba días avisando.

## 3. Propuesta de solución

Automatizar la llamada de seguimiento con un agente de voz que:

- recorra un protocolo clínico completo con cobertura verificable;
- tolere lenguaje coloquial, respuestas ambiguas e interrupciones;
- escale de inmediato lo que deba escalarse;
- y **declare explícitamente lo que no pudo evaluar**, en lugar de asumir
  normalidad.

El objetivo no es sustituir a enfermería, sino ordenarle la cola: convertir
«llamar a 250 pacientes» en «revisar los 40 que el sistema marcó, con el
detalle de por qué».

## 4. Arquitectura

Detalle completo con diagramas en [DIAGRAMA.md](DIAGRAMA.md).

El flujo de un turno es: audio del navegador → WebSocket → transcripción →
clasificación de la intervención → decisión clínica → recuperación documental →
generación estructurada → compuerta de evidencia → síntesis de voz.

Dos decisiones de orden importan más de lo que parece:

**La recuperación ocurre siempre, antes de decidir si la pregunta es de
misión.** En una versión anterior, un clasificador de intención cortaba el
turno antes de consultar el conocimiento; el resultado era que un documento
recién subido resultaba inalcanzable si el modelo consideraba que la pregunta
«no era clínica». El error no era la clasificación: era decidir sin mirar el
conocimiento activo.

**La compuerta de evidencia corre antes del TTS**, no después. No existe una
variante «sin filtrar» del texto que pueda escaparse por otra vía: el mismo
texto que pasó la compuerta es el que va al navegador, al sintetizador, al
transcript y al acta.

## 5. Diseño conversacional

La conversación es una máquina de estados con un checklist de seis dominios
(dolor, temperatura, herida, movilidad, alimentación, medicación) sobre la que
se superponen tres mecanismos:

- **Clasificación de la intervención.** Cada turno del paciente se clasifica
  como respuesta clínica, pregunta lateral, no respuesta o ambigua. Una
  pregunta lateral **no consume el intento de evaluación** del dominio
  pendiente: es la diferencia entre «no supo contestar» y «me preguntó otra
  cosa antes de contestar».
- **Retomar el hilo.** Tras responder una interrupción, el sistema vuelve
  explícitamente al tema pendiente en el mismo mensaje.
- **Repregunta acotada.** Un dominio crítico perdido se reintenta **una vez**
  con pregunta cerrada. Agotado el intento, queda como desconocido y lo recoge
  la compuerta de cobertura al cerrar. No hay bucles.

### Cierre natural de la llamada

De una sesión humana real salieron tres defectos puramente conversacionales:
el sistema volvía a preguntar por el acompañante después de que el paciente
contestara «Sí, mi mamá»; repetía en cada turno que el caso estaba escalado; y
seguía la conversación después de un «No, nada más, eso sería todo».

La corrección fue una política de cierre **determinista** (`conversation/cierre.py`):
memoria de hechos ya aportados por el paciente, registro de anuncios
operativos ya emitidos, y detección general de intención de terminar. Un «no»
aislado no cierra —casi siempre responde a una pregunta clínica— salvo que la
frase previa del agente fuera una pregunta de cierre.

Cerrar solo ocurre si el paciente lo manifiesta, no ha aparecido una alarma
nueva en ese turno, y —si hubo escalamiento— el acta ya está persistida. El
rojo **no cuelga**: escala, y sigue disponible para preguntas operativas.

## 6. Motor clínico

Seis carriles corren en paralelo sobre cada turno:

| Carril | Naturaleza | Qué aporta |
|---|---|---|
| Textual determinista | regex sobre `config/red_flags.yaml` | inmune a inyección |
| Numérico determinista | umbrales | temperatura, dolor, frecuencias |
| Slots estructurados | extracción del modelo | señales que el texto no nombra |
| Composición clínica | multidominio | deterioro repartido en varios sistemas |
| Riesgo histórico | acumulado de la llamada | una alarma previa no se olvida |
| Señal del modelo | LLM | matices lingüísticos |

El nivel final es el **máximo**. Esta fusión conservadora es el núcleo del
proyecto: hace estructuralmente imposible que el modelo tranquilice sobre una
alarma detectada por reglas.

Las banderas viven en un YAML editable por personal clínico, no incrustadas en
código.

## 7. Triaje de doble eje

La aportación conceptual del proyecto. Tres preguntas distintas, tres
respuestas distintas:

- **Riesgo clínico** — qué se encontró (verde/amarillo/rojo).
- **Estado de evaluación** — cuánto se logró evaluar (completa/incompleta/fallida).
- **Acción operativa** — qué hacer ahora (continuar/repreguntar/revisión humana/escalar).

Solo se combinan al cerrar la llamada. Un sistema que los mezcla desde el
principio acaba pintando de verde a quien nunca respondió, que es exactamente
el falso negativo más peligroso y menos visible.

La cobertura registra por separado si un dominio fue **evaluado** y si resultó
**positivo**. Son campos distintos a propósito.

## 8. Modo degradado

Si el proveedor de lenguaje cae, agota cuota o devuelve respuestas
inutilizables, el sistema **no se apaga ni improvisa**: entra en modo
determinista y sigue triando con los carriles de reglas. Se pierde naturalidad
conversacional; no se pierde seguridad.

El fallback **no es otro modelo de lenguaje**. Es la ausencia de modelo.

## 9. RAG y Evidence Gate

La recuperación usa FastEmbed sobre ONNX Runtime con el modelo
`paraphrase-multilingual-MiniLM-L12-v2` (384 dimensiones), ChromaDB como
almacén, y un reordenamiento léxico que toma 12 candidatos y los reordena antes
de quedarse con los mejores.

La generación es **estructurada**: el modelo devuelve oraciones, cada una con
una marca de si es clínica y con los identificadores de evidencia que la
sostienen. La compuerta verifica, para cada oración clínica, que cada
identificador:

1. exista realmente entre las evidencias recuperadas;
2. pertenezca a **este** turno (una cita válida de hace tres turnos no sostiene
   la frase de ahora);
3. apunte a un documento **activo**;
4. corresponda a la **versión vigente** del conocimiento.

Las oraciones que no pasan se descartan. Si no queda ninguna, se emite una
abstención explícita.

El `evidence_id` lo genera **el código**, no el modelo:
`sha256(kb_version|doc_id|chunk_id|texto)`. Un modelo no puede inventar un
identificador válido.

### Guarda estructural de medicación

Detecta el **acto** de prescribir —verbos más morfología farmacológica— en
lugar de mantener una lista de fármacos. Bloquea afirmaciones de prescripción
específica para el paciente **incluso con evidencia válida**: el corpus es
literatura clínica, no la historia de esa persona. Distingue una prescripción
de una abstención segura («no dispongo de información sobre esa dosis»).

## 10. Versionado del conocimiento y olvido verificable

`kb_version` es el hash de los documentos activos ordenados. Cambia al subir y
**también al borrar**. Es lo que permite que una evidencia caduque
automáticamente cuando el conocimiento cambia.

El olvido se comprueba sondeando el almacén: se exige **cero** vectores
restantes del documento eliminado y un cambio de `kb_version`. No es una marca
de estado, es una verificación.

## 11. Voz

- **Entrada:** captura en el navegador, VAD por energía con umbral adaptativo
  sobre el ruido ambiente medido, y barge-in —el paciente puede interrumpir al
  agente.
- **Salida:** Edge TTS con `es-CO-SalomeNeural`, en streaming: cada trozo sale
  hacia el navegador en cuanto el motor lo produce.
- **Identidad de voz por sesión.** El perfil se fija al iniciar y no cambia
  hasta el final. Un paciente mayor no debe percibir que «cambió de persona» a
  mitad de una conversación clínica.
- **Cierre sin cortar audio.** Cuando la conversación termina, el servidor
  *anuncia* el cierre; el navegador espera a que la despedida termine de sonar
  antes de colgar. No hay temporizador de seguridad: cortar el TTS a mitad de
  frase sería peor que tardar un segundo más.

## 12. Resiliencia

Reintentos con backoff, cambio a respaldo de voz local (Piper) si el motor
remoto falla, degradación explícita declarada en `/api/salud`, e indicador de
código obsoleto —el servidor compara la marca temporal del código que cargó
contra el del disco, porque un proceso viejo sirviendo código viejo sin avisar
costó un ciclo completo de validación humana.

## 13. Seguridad

- **El paciente es información, nunca instrucción.** Los intentos de inyección
  por voz no alteran el comportamiento; el carril determinista es inmune por
  construcción.
- **Los documentos tampoco mandan.** Una instrucción incrustada en un PDF
  subido no se obedece. Se detectó y corrigió un caso real en el que una cita
  *válida* de un documento manipulado hacía pasar una afirmación de
  prescripción.
- **Lenguaje no diagnóstico.** La descripción técnica de una regla va al acta;
  al paciente se le habla sin alarmarlo y sin emitir diagnósticos.
- **Sin secretos en el repositorio.** Solo `.env.example` con nombres de
  variables. Los registros redactan claves automáticamente.

## 14. Observabilidad

Cada turno emite un evento JSONL con marcas de tiempo por etapa
(audio recibido, fin de transcripción, fin de modelo, primer byte de voz),
tokens reales del proveedor, consultas al conocimiento, evidencias recuperadas,
evidencias usadas y rechazos de la compuerta.

**Las métricas del README se generan desde esos eventos** con
`scripts/metrics.py`. No se escriben a mano. Si las métricas y los registros
discrepasen, sería un defecto de integridad, no un detalle de presentación.

## 15. Evaluación y resultados

Dataset oficial de 160 casos con etiqueta de verdad, en modo determinista:

| Resultado | Valor |
|---|---|
| Exactitud global | 75,6 % |
| Rojo clasificado como verde | **0** |
| Verde clasificado como rojo | **0** |
| Recall de rojo (capa limpia) | 100 % (12/12) |
| Concordancia pareada limpio/ruidoso | 83,3 % |
| Captura operativa de rojo | 100 % |

**Contrapeso, sin maquillar.** Esa captura del 100 % se paga con sobretriaje:
**111 de 246 conversaciones verdes fueron enviadas a revisión humana**. En un
servicio real eso es carga adicional para enfermería. Es una decisión
deliberada —preferimos revisar de más a soltar un rojo— pero es un coste.

**Recuperación:** Recall@1 del 55 %. La compuerta convierte los fallos de
recuperación en abstenciones, no en invenciones; pero una abstención sigue
siendo una pregunta sin responder.

**Naturaleza de cada cifra.** Las de esta tabla son *benchmark interno sobre
dataset sintético etiquetado*. El ciclo de conocimiento vivo se validó además
en *prueba humana* con navegador y micrófono reales. El arranque se midió en
*sala limpia*. Son tres tipos de evidencia distintos y no deben mezclarse.

### Pruebas automatizadas

533 comprobaciones en 21 archivos, sin fallos. Cubren compuerta de evidencia,
conocimiento vivo, inyección por voz y por documento, guarda de medicación,
semántica de cobertura, router conversacional, interrupciones, cierre de
llamada, identidad de voz y el ciclo G5 completo por la ruta humana desde
`POST /api/llamada/iniciar`.

## 16. Latencia, consumo y costo

| Métrica | Valor |
|---|---|
| Latencia P50 (fin de habla → primer audio) | 4014 ms |
| Latencia P95 | 11 055 ms |
| Tokens de entrada / salida por turno | 1623,9 / 272,6 |
| Tokens de entrada / salida por llamada | 3450,8 / 579,2 |
| Invocaciones al modelo por turno | 1,7 |
| Consultas RAG por llamada | 0,4 |
| **Costo estimado por llamada** | **0,00277 USD** |
| Llamadas / turnos evaluados | 40 / 76 |

El P95 de 11 s es alto: incluye turnos con reintentos del proveedor y turnos
con recuperación documental. El P50 de 4 s representa la conversación normal.

Las mediciones se hicieron en niveles gratuitos, de modo que el desembolso real
fue **0 USD**. La cifra es una extrapolación a precios de producción: entrada
0,59 USD/millón de tokens, salida 0,79 USD/millón, transcripción
0,00185 USD/minuto.

## 17. Reproducibilidad

- Dependencias fijadas por versión exacta.
- El corpus se siembra desde su **origen oficial** con verificación de SHA-256;
  los PDFs no se redistribuyen.
- Los hashes del motor clínico se publican para identificar qué código produjo
  cada benchmark: `engine.py 45ec0f70ff452e4e`,
  `composicion.py 006312e7dfd9e70d`, `red_flags.yaml 97e2beefe34cc5ad`,
  `rules.py 23b61963b72e86a3`.
- Arranque en sala limpia medido: 2 min 09 s (Windows 11, Python 3.12, ~9 MB/s,
  sin caché de `pip`).

## 18. Limitaciones

- **No es un producto médico.** Prototipo de competencia; no sustituye criterio
  clínico ni presta servicio asistencial.
- **Pacientes y evaluación sintéticos.** 160 casos etiquetados no son un ensayo
  clínico. La capa roja son 12 casos: un 100 % de recall sobre 12 tiene un
  intervalo de confianza ancho.
- **Sobretriaje medido y no resuelto.**
- **Recuperación imperfecta** (Recall@1 55 %).
- **Dependencia de proveedores externos** para voz y lenguaje.
- **Superficies de supervisión incompletas.** La consola de conocimiento
  funciona; los paneles dedicados de alertas, actas y métricas no se
  construyeron por límite de tiempo. Los datos existen y son accesibles por API.

## 19. Trabajo futuro

1. Reducir el sobretriaje sin tocar el recall de rojo, calibrando la compuerta
   de verde con más casos etiquetados.
2. Mejorar Recall@1 con reordenamiento semántico, midiendo antes de adoptar:
   dos estrategias con mejores métricas agregadas **rompían** el ciclo de
   conocimiento vivo y fueron descartadas.
3. Completar los paneles de supervisión.
4. Estudio de latencia por etapa para bajar el P95.

## 20. Proceso de construcción

El desarrollo avanzó por fases con puertas explícitas: cada fase tenía alcance
cerrado, prohibiciones y un informe final con veredicto PASS/FAIL. Los
componentes de seguridad clínica se **congelaron** por hash una vez validados,
y cualquier cambio posterior tenía que demostrar que no los alteraba.

Varias decisiones importantes salieron de medir y descartar, no de acertar a la
primera:

- Dos estrategias de recuperación con mejores métricas agregadas **rompían G5**
  y fueron rechazadas.
- Un fallo de G5 en prueba humana resultó ser tres defectos encadenados; la
  prueba automatizada que lo cubría daba un falso PASS porque se saltaba
  justamente el trozo roto.
- Un fallo visual reportado con capturas resultó ser caché heurística del
  navegador emparejando HTML nuevo con CSS viejo. Se corrigió sellando los
  recursos por contenido, no pidiendo al usuario que vaciara la caché.

## 21. Uso de herramientas de IA

Durante el desarrollo se utilizaron asistentes de inteligencia artificial como
apoyo en exploración de alternativas, revisión de código, depuración, diseño de
pruebas, redacción de documentación y análisis de resultados.

**Las decisiones de arquitectura, la integración, las pruebas humanas, la
validación de cada fase y la entrega final fueron controladas por el
participante.** Cada fase se cerró con verificación manual sobre la aplicación
en ejecución, y varias propuestas generadas automáticamente fueron rechazadas
tras medirlas —el caso más claro, las estrategias de recuperación que mejoraban
métricas agregadas pero rompían el ciclo de conocimiento vivo.

La autoría del repositorio corresponde únicamente al participante.

### Prompts representativos

Resumen de los tipos de instrucción que guiaron el desarrollo:

1. **Arquitectura.** «Separar riesgo clínico, estado de evaluación y acción
   operativa como tres ejes independientes; combinarlos solo al cerrar.»
2. **Seguridad clínica.** «Fusión conservadora por máximo entre carriles; el
   modelo puede subir una alarma, nunca bajarla. Congelar el motor por hash.»
3. **Evidence Gate.** «El `evidence_id` lo genera el código a partir de
   `kb_version|doc_id|chunk_id|texto`; validar existencia, localidad de turno,
   documento activo y versión vigente antes de sintetizar voz.»
4. **Conocimiento vivo (G5).** «Demostrar aprender/citar/olvidar con un
   documento nunca visto, por la ruta humana completa; no acepto una prueba que
   llame al recuperador directamente como sustituto.»
5. **Pruebas.** «Recorrer la sesión real desde `POST /api/llamada/iniciar`; si
   una prueba pasa mientras la llamada humana falla, la prueba está mal.»
6. **Entrega.** «Sala limpia real, cronometrar el arranque, auditar secretos,
   no maquillar métricas y reportar los bloqueantes aunque impliquen FAIL.»

## 22. Conclusiones

RONDA demuestra que un agente de voz clínico puede ser **auditable por
construcción** y no solo por buenas intenciones: la fusión conservadora hace
imposible que el modelo rebaje una alarma, la compuerta de evidencia hace
imposible una afirmación clínica sin fuente, y el versionado del conocimiento
hace demostrable el olvido.

El resultado más honesto del proyecto no es el 100 % de recall de rojo, sino el
contrapeso que lo acompaña: 111 de 246 verdes enviados a revisión humana. Ese
número es el precio real de la seguridad elegida, y aparece en la primera
página del README precisamente porque es lo que un servicio clínico necesitaría
negociar antes de adoptarlo.
