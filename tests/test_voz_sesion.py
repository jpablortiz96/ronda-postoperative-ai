# -*- coding: utf-8 -*-
"""La voz del agente no cambia de persona a mitad de llamada.

EL FALLO QUE CORRIGE
--------------------
Un evaluador humano reportó una llamada donde la voz empezó con un acento,
luego sonó masculina y después femenina otra vez. Reproducido en el código:

  · `iniciar()` resolvía el motor en CADA turno con `orden_motores()`. En modo
    `auto`, un fallo transitorio de Edge en el turno 3 caía a Piper —modelo
    local masculino, es-MX— solo para ese turno, y volvía a Edge en el 4.
  · El saludo viajaba por REST (`synthesize`) y los turnos por WebSocket
    (`iniciar`), así que podían resolverse a motores distintos.

LA POLÍTICA
-----------
La identidad se elige UNA vez, al iniciar la llamada, y queda atada a la
sesión. Si el motor falla a mitad de conversación se reintenta el mismo; solo
se admite otro si está declarado compatible con esa persona. Como hoy no
existe una voz local femenina en es-CO, en la práctica la llamada continúa por
escrito antes que cambiar de personaje.

Un paciente que oye tres voces distintas deja de creer que habla con alguien.
Eso vale más que mantener audio a cualquier precio.

Las pruebas NO llaman a ningún servicio de voz: sustituyen los generadores por
dobles controlados, que es la única forma de provocar un fallo a mitad de
llamada de manera determinista.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import tts  # noqa: E402

fallos = 0


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    global fallos
    fallos += not cond
    print(f"  [{'PASS' if cond else 'FAIL'}] {nombre}" + (f"   → {detalle}" if not cond else ""))


# ── Dobles de motor ─────────────────────────────────────────────────────────
class Guion:
    """Decide, por número de llamada, si el motor responde o falla."""

    def __init__(self, fallos_en=()):
        self.llamadas = 0
        self.fallos_en = set(fallos_en)

    def generador(self, texto):
        self.llamadas += 1
        n = self.llamadas
        falla = n in self.fallos_en

        async def gen():
            if falla:
                raise RuntimeError("motor caído (simulado)")
            yield b"\x00\x01"
        return gen()


def montar(edge_guion=None, piper_guion=None):
    edge_guion = edge_guion or Guion()
    piper_guion = piper_guion or Guion()
    tts._IMPL = {
        "edge": (edge_guion.generador, lambda: {"motor": "edge", "formato": "mp3",
                                                "sample_rate": None,
                                                "voz": "es-CO-SalomeNeural"}),
        "piper": (piper_guion.generador, lambda: {"motor": "piper", "formato": "pcm16",
                                                  "sample_rate": 22050,
                                                  "voz": "es_MX-ald-medium"}),
    }
    return edge_guion, piper_guion


_IMPL_REAL = dict(tts._IMPL)


async def turnos(perfil, n, texto="hola"):
    """Sintetiza n turnos con el perfil dado. Devuelve los motores usados."""
    usados = []
    for _ in range(n):
        try:
            meta, gen = await tts.iniciar(texto, perfil)
            async for _ in gen:
                pass
            usados.append(meta["motor"])
        except tts.TtsNoDisponible:
            usados.append("SIN_VOZ")
    return usados


# ══════════════════════════════════════════════════════════════════════════
print("\nV1 · saludo + 5 turnos con todo funcionando")
montar()
perfil = asyncio.run(tts.elegir_perfil())
check("el perfil elegido es el primario", perfil.provider == tts.orden_motores()[0],
      perfil.provider)
usados = asyncio.run(turnos(perfil, 6))
check("misma voz 6/6", len(set(usados)) == 1 and usados[0] == perfil.provider,
      str(usados))
check("el perfil declara persona, locale y género",
      all([perfil.persona_id, perfil.locale, perfil.gender]), str(perfil))

print("\nV2 · Edge no disponible ANTES de iniciar")
montar(edge_guion=Guion(fallos_en=range(1, 99)))
perfil2 = asyncio.run(tts.elegir_perfil())
check("se elige Piper antes del saludo", perfil2.provider == "piper", perfil2.provider)
usados = asyncio.run(turnos(perfil2, 6))
check("Piper en los 6 turnos", set(usados) == {"piper"}, str(usados))
check("y la persona es coherente con ese motor",
      perfil2.persona_id != "ronda_salome", perfil2.persona_id)

print("\nV3 · Edge cae A MITAD de una llamada ya iniciada")
edge, piper = montar()
perfil3 = asyncio.run(tts.elegir_perfil())
check("la sesión arrancó en Edge", perfil3.provider == "edge", perfil3.provider)
# A partir de ahora Edge falla siempre; Piper sigue disponible.
edge.fallos_en = set(range(edge.llamadas + 1, 999))
usados = asyncio.run(turnos(perfil3, 3))
check("NO se cambia a la voz masculina de Piper",
      "piper" not in usados, str(usados))
check("la llamada degrada a texto en vez de cambiar de persona",
      set(usados) == {"SIN_VOZ"}, str(usados))
check("se reintentó el mismo motor antes de rendirse",
      tts.REINTENTOS_MISMO_MOTOR >= 1)

print("\nV3b · un fallo puntual se recupera con el MISMO motor")
edge, _ = montar(edge_guion=Guion(fallos_en={2}))   # falla el 1er intento del turno
perfil4 = asyncio.run(tts.elegir_perfil())          # llamada 1: éxito
usados = asyncio.run(turnos(perfil4, 1))
check("el reintento salva el turno sin cambiar de voz",
      usados == ["edge"], str(usados))

print("\nV4 · sin perfil (compatibilidad) sigue funcionando el modo automático")
montar(edge_guion=Guion(fallos_en=range(1, 99)))
usados = asyncio.run(turnos(None, 1))
check("sin perfil de sesión, `auto` puede caer a Piper",
      usados == ["piper"], str(usados))

print("\nV5 · política de compatibilidad")
p_edge = tts._perfil_edge()
check("Edge no declara ningún sustituto de persona",
      tts.perfiles_compatibles(p_edge) == ["edge"],
      str(tts.perfiles_compatibles(p_edge)))
check("el perfil de Edge es femenino es-CO",
      p_edge.gender == "female" and p_edge.locale.startswith("es-CO"), str(p_edge))
check("el perfil local disponible es masculino (por eso no sustituye)",
      tts._perfil_piper().gender == "male", str(tts._perfil_piper()))

tts._IMPL = _IMPL_REAL
total = 15
print(f"\nRESULTADO: {total - fallos}/{total}")
raise SystemExit(1 if fallos else 0)
