# Guía del video — RONDA

> **Video final publicado:** https://youtu.be/4vaptuQEiQQ

Este documento se conserva como registro del proceso: es el guion con
el que se grabó la demostración.

Material de apoyo para la grabación. Duración objetivo: **5–7 minutos**.

Narración en español, tono natural colombiano. No leer este documento en voz
alta: son puntos de apoyo, no un guion palabra por palabra.

**No traducir** nombres de tecnologías, modelos, comandos ni rutas.

---

## Antes de grabar

- [ ] Servidor levantado y probado (`python run.py`)
- [ ] Corpus sembrado (6 documentos activos)
- [ ] Micrófono probado, sin eco
- [ ] Preparar un archivo `.txt` con un código inventado para la demo de G5
- [ ] Cerrar notificaciones de Windows, DevTools y pestañas privadas
- [ ] Navegador a 1440×900 o 1920×1080

---

## Estructura

### 0:00 — Gancho y problema

La ventana de riesgo tras una cirugía no está en el quirófano: está en la casa
del paciente. Una infección de herida o una fiebre que sube se detectan
preguntando, todos los días. Con el personal disponible, eso no se hace.

### 0:20 — Qué es RONDA

Un agente de voz que llama, conversa en español y decide con criterios
auditables. La frase clave: **sabe cuándo un paciente está en riesgo, y también
cuándo no tiene evidencia suficiente para responder.**

### 0:40 — Llamada real (grabación de pantalla)

Iniciar seguimiento, hablar de verdad. Mostrar la transcripción apareciendo y
la voz respondiendo. Señalar el panel de decisión con los **tres ejes
separados**.

### 1:30 — Caso rojo

Reportar fiebre alta y secreción purulenta. Mostrar:
- el semáforo pasando a rojo,
- el guion de escalamiento,
- el acta de alerta creada al instante.

Decir en voz alta: **el modelo puede subir una alarma; nunca puede bajarla.**

### 2:10 — Trazabilidad y evidencia

Hacer una pregunta clínica cubierta por el corpus. Abrir el chip de evidencia y
mostrar documento, fragmento, `evidence_id`, `sha256` y `kb_version`.

Frase clave: **sin evidencia no hay afirmación clínica.**

### 2:40 — Consola

Mostrar los documentos activos y la versión del conocimiento.

### 3:00 — G5, aprender

Preguntar por el código del documento **antes** de subirlo → RONDA se abstiene.
Subir el archivo. Preguntar otra vez → responde **citando** el documento nuevo.
Sin reiniciar el servidor. Señalar el cambio de `kb_version`.

### 3:30 — G5, olvidar

Eliminar el documento. Pulsar «Verificar olvido» → cero vectores. Preguntar de
nuevo → vuelve a abstenerse. **El olvido es verificable, no declarativo.**

### 4:00 — Arquitectura

Mostrar el diagrama. Detenerse en los seis carriles y la fusión por máximo, y
en que la compuerta de evidencia corre **antes** del TTS.

### 4:30 — Resultados

- 0 rojos clasificados como verdes
- Recall de rojo 100 % en la capa limpia (12/12)
- 533 pruebas sin fallos
- Arranque en 2 min 09 s

**Y decir el contrapeso en voz alta:** 111 de 246 verdes fueron a revisión
humana. Es sobretriaje, es el precio de la seguridad elegida, y no lo
escondemos.

---

## Las dos preguntas de cierre, frente a cámara

> Texto literal tomado de `docs/rubrica-evaluacion.md` del kit oficial.

### Pregunta 1

> **Si debes convencer a un cliente de que adopte el agente que construiste,
> ¿cómo presentarías el problema que resuelve, por qué tu solución es la
> adecuada y qué valor diferencial ofrece frente a otras alternativas?**

**Puntos a cubrir (≈ 60–75 s):**

*El problema.* Después de una cirugía, el riesgo se juega en casa. Detectar una
complicación exige preguntar sistemáticamente a todos los pacientes, todos los
días. Con el personal disponible eso no ocurre: los seguimientos se espacian o
se saltan, y parte de esos pacientes reaparece por urgencias.

*Por qué RONDA es la solución adecuada.* No vendo un chatbot que habla bonito.
Vendo un sistema que **ordena la cola de enfermería**: convierte «llamar a 250
pacientes» en «revisar los que el sistema marcó, sabiendo exactamente por qué».
Y lo hace con tres garantías estructurales, no con promesas: una alarma
detectada por reglas **no puede ser rebajada** por el modelo; un paciente que
no contestó **no se pinta de verde**, se marca como no evaluado; y ninguna
afirmación clínica sale sin un documento citable detrás.

*El valor diferencial.* Frente a un asistente conversacional genérico, RONDA es
**auditable**: cada decisión se rastrea hasta la regla que la disparó y cada
frase clínica hasta su fuente. Frente a un formulario telefónico automatizado,
RONDA **entiende lenguaje real**, tolera interrupciones y retoma el hilo.

*Honestidad como argumento de venta.* Le digo al cliente el coste: capturamos
el 100 % de los rojos, y a cambio enviamos a revisión humana 111 de 246 verdes.
Eso es trabajo adicional. Un proveedor que no le enseñe ese número no lo ha
medido.

---

### Pregunta 2

> **Elige la decisión técnica más relevante que tomaste (arquitectura, modelo,
> herramientas, prompts, RAG, memoria, manejo del contexto, etc.) y cuéntanos:
> ¿qué alternativas evaluaste?, ¿por qué las descartaste?, ¿qué riesgos
> identificaste?, y si tuvieras dos semanas más para mejorar la solución, ¿qué
> cambiarías y por qué?**

**Decisión elegida: separar el triaje en dos ejes y fusionar los carriles de
forma conservadora.**

*Qué es.* El riesgo clínico y el estado de la evaluación son variables
distintas que solo se combinan al cerrar la llamada. Y el nivel de riesgo es el
**máximo** de seis carriles que corren en paralelo, de los cuales solo uno es
el modelo de lenguaje.

*Alternativas evaluadas y por qué las descarté.*

1. **Un solo semáforo decidido por el LLM.** Es lo más simple y lo más común.
   Lo descarté porque hace estructuralmente posible el peor error: que el
   modelo tranquilice sobre una alarma real. Además confunde «paciente sano»
   con «paciente que no respondió».
2. **LLM con function calling y reglas como herramientas.** Más elegante, pero
   deja al modelo decidir *cuándo* consultar las reglas. La seguridad pasaría a
   depender de que el modelo elija bien.
3. **Reglas puras, sin modelo.** Seguro pero rígido: no entiende «me siento
   como cuando me dio la infección la otra vez».

*Riesgos que identifiqué.* El principal es el **sobretriaje**: fusionar por
máximo garantiza que no se escapen rojos, pero infla los falsos positivos. Lo
medí y lo publico: 111 de 246. El segundo es que las reglas viven en un YAML
que alguien puede editar mal; por eso el motor está congelado por hash y
cualquier cambio tiene que demostrar que no lo altera.

*Qué haría con dos semanas más.*

1. **Bajar el sobretriaje sin tocar el recall de rojo**, calibrando la
   compuerta de verde con más casos etiquetados. Es el número que más pesa para
   que un servicio real lo adopte.
2. **Mejorar la recuperación**, hoy en Recall@1 del 55 %. Con la advertencia
   aprendida: dos estrategias que mejoraban las métricas agregadas **rompían el
   ciclo de conocimiento vivo**, así que cualquier cambio ahí se mide contra G5
   antes de adoptarse.
3. **Completar los paneles de supervisión** de alertas, actas y métricas. Los
   datos ya existen y son accesibles por API; falta la superficie.
4. **Bajar el P95 de latencia**, hoy en 11 s, con un estudio por etapa.

---

## Resultado

Video publicado: **https://youtu.be/4vaptuQEiQQ**
