# Checklist de entrega — RONDA

Lista de verificación para comprobar que una instalación limpia funciona de
extremo a extremo. Cada punto es observable: se ejecuta, se mira y se marca.

El orden importa: si falla un punto, los siguientes no son concluyentes.

---

## 1. Arranque

- [ ] **Quick start** — `python -m venv .venv`, activar, `pip install -r requirements.txt`.
      Cronometrar desde la carpeta recién clonada.
- [ ] **Credenciales** — copiar `.env.example` a `.env` y rellenar `GROQ_API_KEY`.
      El repositorio no contiene ningún `.env`.
- [ ] **Servidor** — `python run.py` levanta en `http://localhost:8000` sin trazas de error.
- [ ] **Salud** — `GET /api/salud` devuelve `200` e indica proveedor, TTS, vectores
      y documentos indexados.

## 2. Superficies

- [ ] **Pantalla de llamada** — `GET /` carga sin peticiones a dominios externos.
- [ ] **Consola de conocimiento** — `GET /consola` carga y lista los documentos.
- [ ] **Sin dependencias remotas** — ninguna petición a CDN, tipografías o
      librerías de iconos. Todo viaja en el repositorio.

## 3. Voz

- [ ] **Micrófono** — el navegador pide permiso y el medidor reacciona al hablar.
- [ ] **Transcripción** — lo dicho aparece en la conversación.
- [ ] **Respuesta hablada** — RONDA contesta con voz y una sola identidad
      durante toda la llamada.
- [ ] **Interrupción** — hablar encima del agente lo detiene (barge-in).

## 4. Triaje determinista

- [ ] **Rojo** — una frase con una señal de alarma clara produce riesgo `rojo`
      y guion de escalamiento.
- [ ] **Nunca rebajar** — el carril de reglas manda: el modelo no puede bajar
      una alarma que las reglas levantaron.
- [ ] **Tres ejes separados** — riesgo clínico, estado de evaluación y acción
      operativa se muestran como tres cosas distintas, no como un semáforo único.
- [ ] **Alerta** — un rojo deja acta de alerta persistida.

## 5. Conocimiento vivo

- [ ] **Consulta con corpus** — una pregunta cubierta por los documentos activos
      se responde **citando** documento y fragmento.
- [ ] **Subida en caliente** — subir un documento nuevo desde la consola lo deja
      consultable sin reiniciar el servidor.
- [ ] **Antes / después** — la misma pregunta se abstiene antes de subir el
      documento y se responde citándolo después.
- [ ] **Olvido verificable** — al eliminar el documento, «verificar olvido»
      devuelve cero vectores y la versión de la base de conocimiento cambia.
- [ ] **Fuera de corpus** — una pregunta sin respaldo documental produce una
      abstención explícita, no una respuesta inventada.

## 6. Trazabilidad

- [ ] **Cita navegable** — cada afirmación clínica muestra su fuente, y al
      abrirla aparecen documento, fragmento, hash y versión del conocimiento.
- [ ] **Sin fuente, sin afirmación** — no hay ninguna frase clínica sin
      evidencia asociada.

## 7. Cierre

- [ ] **Cierre natural** — cuando el paciente dice que no tiene nada más y las
      condiciones se cumplen, RONDA se despide una sola vez.
- [ ] **La despedida se oye entera** — el audio final se reproduce completo
      antes de colgar.
- [ ] **Sin repeticiones** — el escalamiento no se anuncia dos veces, ni se
      vuelve a preguntar algo que el paciente ya contestó.
- [ ] **Cierre manual** — el botón «Finalizar llamada» sigue funcionando.
- [ ] **Acta** — al terminar queda un acta consultable con los tres ejes.

## 8. Seguridad y privacidad

- [ ] **Sin secretos** — no hay claves de API en el repositorio; solo
      `.env.example` con los nombres de las variables.
- [ ] **Inyección de prompt** — un intento de cambiar las instrucciones desde
      la voz del paciente o desde un documento no altera el comportamiento.
- [ ] **Sin prescripción** — el sistema no indica dosis ni medicación
      específica para el paciente, ni siquiera con evidencia válida.
- [ ] **Datos sintéticos** — el paciente de demostración es ficticio y la
      interfaz lo declara.

## 9. Pruebas

- [ ] **Suite completa** — todos los archivos de `tests/` terminan sin fallos.
- [ ] **Motor clínico intacto** — el hash del motor de decisión coincide con el
      declarado en el informe.

---

### Variables de entorno

| Variable | Obligatoria | Para qué |
|---|---|---|
| `GROQ_API_KEY` | Sí | Transcripción de voz |
| `GEMINI_API_KEY` | Según proveedor | Generación de lenguaje |
| `LLM_PROVIDER` | No | Proveedor activo |
| `RAG_MAX_DISTANCE` | No | Umbral de «citar o callar» |

Sin credenciales el sistema arranca igual y degrada de forma explícita: lo dice
en `/api/salud` y en la interfaz, en lugar de fingir que funciona.
