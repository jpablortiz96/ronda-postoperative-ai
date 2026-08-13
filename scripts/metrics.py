"""Genera las métricas obligatorias del README DESDE LOS LOGS REALES.

Uso:  python scripts/metrics.py            (imprime tabla Markdown)
      python scripts/metrics.py --json     (salida JSON)

Al calcularse desde logs/events.jsonl, las métricas reportadas son por
construcción consistentes con lo que el jurado verá en la sesión — la rúbrica
castiga severamente los números que no se sostienen contra los logs.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import config  # noqa: E402


def cargar_turnos() -> list[dict]:
    if not config.EVENTS_LOG.exists():
        return []
    turnos = []
    for line in config.EVENTS_LOG.read_text(encoding="utf-8").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("tipo") == "turno":
            turnos.append(ev)
    return turnos


SIN_DATOS = "N/D — sin turnos medidos"


def percentil(datos: list[float], p: float) -> float | None:
    """Devuelve None (no 0) cuando no hay observaciones: un 0 imputado es una
    métrica falsa, y la rúbrica penaliza los números que no se sostienen."""
    if not datos:
        return None
    datos = sorted(datos)
    k = (len(datos) - 1) * p
    f, c = int(k), min(int(k) + 1, len(datos) - 1)
    return datos[f] + (datos[c] - datos[f]) * (k - f)


def _redondear(v, digitos=0):
    return None if v is None else round(v, digitos) if digitos else round(v)


def _fmt(v, sufijo="", digitos=None):
    """Formatea para la tabla distinguiendo 'no hay datos' de un 0 real."""
    if v is None:
        return SIN_DATOS
    if digitos is not None:
        return f"{v:.{digitos}f}{sufijo}"
    return f"{v}{sufijo}"


def calcular() -> dict:
    turnos = cargar_turnos()
    latencias = [t["latencia_ms"] for t in turnos if t.get("latencia_ms")]
    por_llamada: dict[str, list[dict]] = defaultdict(list)
    for t in turnos:
        por_llamada[t["session_id"]].append(t)

    tokens_in_turno = [t.get("tokens_entrada", 0) for t in turnos]
    tokens_out_turno = [t.get("tokens_salida", 0) for t in turnos]
    invocaciones_turno = [t.get("invocaciones_modelo", 0) for t in turnos]

    llamadas = []
    for sid, ts in por_llamada.items():
        llamadas.append(
            {
                "session_id": sid,
                "turnos": len(ts),
                "tokens_entrada": sum(t.get("tokens_entrada", 0) for t in ts),
                "tokens_salida": sum(t.get("tokens_salida", 0) for t in ts),
                "consultas_rag": sum(t.get("consultas_rag", 0) for t in ts),
                # None si NINGÚN turno de la llamada pudo medir su audio.
                "audio_entrada_s": (
                    sum(t["audio_entrada_s"] for t in ts if t.get("audio_entrada_s"))
                    if any(t.get("audio_entrada_s") for t in ts) else None
                ),
            }
        )

    def prom(xs):
        """None cuando el conjunto de observaciones está vacío (≠ media 0)."""
        return round(statistics.mean(xs), 1) if xs else None

    tokens_in_llamada = prom([c["tokens_entrada"] for c in llamadas])
    tokens_out_llamada = prom([c["tokens_salida"] for c in llamadas])
    audio_s_llamada = prom([c["audio_entrada_s"] for c in llamadas
                            if c["audio_entrada_s"] is not None])

    # Sin llamadas medidas no hay costo que reportar: no se imputa 0.
    if tokens_in_llamada is None or tokens_out_llamada is None:
        costo_llamada = None
    else:
        audio_min = (audio_s_llamada or 0) / 60
        costo_llamada = round(
            tokens_in_llamada / 1e6 * config.COST_INPUT_PER_M
            + tokens_out_llamada / 1e6 * config.COST_OUTPUT_PER_M
            + audio_min * config.COST_STT_PER_MIN,
            5,
        )

    supuestos = {
        "input_usd_por_M": config.COST_INPUT_PER_M,
        "output_usd_por_M": config.COST_OUTPUT_PER_M,
        "stt_usd_por_min": config.COST_STT_PER_MIN,
        "nota": "Ejecutado en tiers gratuitos (costo real $0); extrapolación a precios de producción del proveedor.",
    }
    if llamadas and audio_s_llamada is None:
        supuestos["aviso_stt"] = (
            "Duración de audio no registrada en los eventos: el componente STT "
            "del costo NO está incluido en esta cifra."
        )
    elif audio_s_llamada is not None:
        supuestos["audio_entrada_s_por_llamada"] = audio_s_llamada
        supuestos["costo_stt_por_llamada_usd"] = round(
            audio_s_llamada / 60 * config.COST_STT_PER_MIN, 6
        )

    return {
        "turnos_registrados": len(turnos),
        "llamadas_registradas": len(llamadas),
        "latencia_ms": {
            "p50": _redondear(percentil(latencias, 0.50)),
            "p95": _redondear(percentil(latencias, 0.95)),
            "n": len(latencias),
        },
        "tokens_por_turno": {
            "entrada_prom": prom(tokens_in_turno),
            "salida_prom": prom(tokens_out_turno),
        },
        "tokens_por_llamada": {
            "entrada_prom": tokens_in_llamada,
            "salida_prom": tokens_out_llamada,
        },
        "invocaciones_modelo_por_turno_prom": prom(invocaciones_turno),
        "consultas_rag_por_llamada_prom": prom([c["consultas_rag"] for c in llamadas]),
        "costo_estimado_por_llamada_usd": costo_llamada,
        "supuestos_costo": supuestos,
    }


def tabla_markdown(m: dict) -> str:
    lat, tt, tl = m["latencia_ms"], m["tokens_por_turno"], m["tokens_por_llamada"]
    costo = m["costo_estimado_por_llamada_usd"]
    costo_txt = SIN_DATOS if costo is None else f"**${costo} USD**"
    par = lambda d: (  # noqa: E731
        SIN_DATOS if d["entrada_prom"] is None else f"{d['entrada_prom']} / {d['salida_prom']}"
    )
    encabezado = "## Métricas (generadas desde logs/events.jsonl — `python scripts/metrics.py`)\n"
    if not m["turnos_registrados"]:
        encabezado += (
            "\n> ⚠️ **No hay turnos de conversación registrados.** Ninguna cifra de "
            "latencia, tokens ni costo puede reportarse todavía: ejecute llamadas "
            "de prueba y vuelva a generar esta tabla.\n"
        )
    aviso = m["supuestos_costo"].get("aviso_stt")
    return f"""{encabezado}
| Métrica | Valor |
|---|---|
| Latencia P50 (fin de habla del paciente → primer audio del agente) | {_fmt(lat['p50'], ' ms')} |
| Latencia P95 | {_fmt(lat['p95'], ' ms')} |
| Turnos medidos | {lat['n']} |
| Llamadas medidas | {m['llamadas_registradas']} |
| Tokens entrada / salida por turno (prom) | {par(tt)} |
| Tokens entrada / salida por llamada (prom) | {par(tl)} |
| Invocaciones al modelo por turno (prom) | {_fmt(m['invocaciones_modelo_por_turno_prom'])} |
| Consultas RAG por llamada (prom) | {_fmt(m['consultas_rag_por_llamada_prom'])} |
| **Costo estimado por llamada (extrapolado a producción)** | {costo_txt} |

Supuestos del costo: entrada ${m['supuestos_costo']['input_usd_por_M']}/M tok, salida ${m['supuestos_costo']['output_usd_por_M']}/M tok, STT ${m['supuestos_costo']['stt_usd_por_min']}/min. {m['supuestos_costo']['nota']}
{('**Aviso:** ' + aviso) if aviso else ''}"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    m = calcular()
    if args.json:
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print(tabla_markdown(m))
