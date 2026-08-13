# Checklist de entrega — Tech Sphere Challenge 2026

Estado a la fecha de cierre. Marcar solo lo verificado, no lo intencionado.

## Entregables obligatorios

- [x] **Repositorio** — código completo, sin secretos, licencia MIT
- [ ] **Repositorio público** — pendiente de publicar en GitHub
- [x] **Diagrama de arquitectura** — [docs/DIAGRAMA.md](DIAGRAMA.md), dos diagramas Mermaid
- [x] **Informe final** — [docs/INFORME_FINAL.md](INFORME_FINAL.md)
- [ ] **Video demo** — `VIDEO_URL_PENDING` (guion en [docs/VIDEO_GUIA.md](VIDEO_GUIA.md))

## Compuertas eliminatorias

| Compuerta | Estado | Evidencia |
|---|---|---|
| **G1** · Cuatro entregables | Parcial | Faltan repo público y video |
| **G2** · Arranque en ≤ 15 min siguiendo solo el README | **PASS** | 2 min 09 s medidos en sala limpia |
| **G3** · Solo modelos permitidos | **PASS** | Gemini 3.6 Flash · Llama 3.3 70B vía Groq · Whisper Large V3 |
| **G4** · Llamada de voz en navegador | **PASS** | Validado con micrófono real |
| **G5** · Conocimiento vivo demostrable | **PASS** | Ciclo completo aprender/citar/olvidar |

## Repositorio

- [x] `README.md` en español, con instalación de 7 pasos
- [x] `LICENSE` — MIT completa, © 2026 Juan Pablo Enriquez Ortiz
- [x] `requirements.txt` con versiones fijadas
- [x] `.env.example` presente
- [x] **Sin `.env`** en el árbol de entrega
- [x] Auditoría de secretos limpia — sin claves, tokens ni contraseñas
- [x] Sin material privado de desarrollo
- [x] Captura real de la aplicación en `docs/assets/ronda-llamada.png`

## Documentación técnica

- [x] Modelos declarados explícitamente (lenguaje, voz, transcripción, embeddings)
- [x] Voz documentada — `es-CO-SalomeNeural`, respaldo local Piper
- [x] Métricas en el README, generadas desde `logs/events.jsonl`
- [x] Método de cálculo del costo explicado
- [x] Limitaciones declaradas sin maquillar
- [x] Sobretriaje reportado junto al recall (111/246 verdes a revisión humana)

## Funcionamiento verificado

- [x] `GET /api/salud` responde 200
- [x] `GET /` y `GET /consola` cargan sin dependencias externas
- [x] Triaje determinista: frase roja → rojo
- [x] Never Downgrade: el nivel final es el máximo de los carriles
- [x] Alerta persistida ante rojo
- [x] Acta persistida al finalizar
- [x] Subida de documento en caliente sin reiniciar
- [x] Eliminación con verificación de olvido (cero vectores)
- [x] Abstención ante pregunta fuera de corpus
- [x] Evidencia con `evidence_id`, `sha256` y `kb_version`
- [x] 533 pruebas automatizadas sin fallos

## Pendientes

| Pendiente | Bloqueante |
|---|---|
| Publicar repositorio en GitHub | Sí — entregable G1 |
| Grabar video y sustituir `VIDEO_URL_PENDING` | Sí — entregable G1 |
| Sitio web del proyecto | No — material de apoyo |
