# Qué motor produjo cada número

Un recall no significa nada si no se puede decir qué código lo generó. Este
archivo asocia cada benchmark del informe con la huella SHA-256 del motor que
lo produjo (`python eval/huellas.py`). No sustituye al control de versiones:
es la evidencia mínima para que dos cifras sean comparables.

La huella cubre los siete archivos que deciden criticidad: `red_flags.yaml`,
`rules.py`, `engine.py`, `composicion.py`, `cobertura.py`, `assess.py`,
`llm.py`. Cambia con cualquier edición de esos archivos, incluidos los
comentarios — por eso conviene anotar también qué cambió.

| Huella | Fase | Qué introduce | Benchmark asociado |
|---|---|---|---|
| `f11f59e7f0fd1733` | 4.7 inicio | motor congelado antes de UNKNOWN/Coverage | — (referencia) |
| `9ea181260fd53dc9` | 4.7 final | cobertura + compuerta de verde | determinista 320 · exactitud 43,1% · V→R 23 |
| `a481059c071c9a19` | 4.7 + Gemini | idéntico en comportamiento al anterior | Gemini FULL · paired 91,7% |
| `734e94b55365efe6` | 4.8 · v1 | dos ejes, dedup, tendencia, amplitud restringida | determinista · exactitud 70,0% · V→R 11 |
| `ce1bc405bc198feb` | 4.8 · v2 | corrige el decimal leído como escala de dolor | determinista · exactitud 71,9% · V→R 6 |
| `633b0c22f191fa42` | 4.8 · v3 | umbral subfebril 37,5 → 37,8 | determinista · exactitud 75,0% · V→R 2 |
| `fe10b939a94b84bf` | **4.8 final** | negaciones explícitas dentro del tramo | determinista · exactitud 75,6% · **V→R 0** |

## Por qué hay tres motores intermedios en la 4.8

Cada uno corrige un defecto encontrado *después* de medir, no antes:

1. **v1 → v2.** Con el motor v1 en marcha, el análisis de falsos positivos
   mostró `«marcó como 36.8 °C»` registrado como **dolor 8/10**. El extractor de
   dolor por tema, añadido ese mismo día, tomaba el decimal de la temperatura.
   Defecto propio, introducido y corregido en la misma fase.
2. **v2 → v3.** Los falsos positivos restantes compartían una temperatura
   subfebril de 37,5–37,6. Ningún plan del corpus define fiebre bajo 38 °C, así
   que ese escalón era una calibración mía; medida en DEV, 37,8 conserva el
   recall entero y elimina dos tercios de las falsas alarmas.
3. **v3 → final.** El último falso positivo era `«¿fiebre? Creo que no, no me he
   sentido con escalofríos»` disparando la regla de sepsis: la negación queda
   *dentro* del tramo que abarca el patrón de combinación.

Las corridas de Gemini iniciadas sobre v1, v2 y v3 se **cancelaron al detectar
cada defecto**, para no gastar cuota midiendo un motor que ya se sabía
incorrecto. Solo se reporta la corrida sobre el motor final.
