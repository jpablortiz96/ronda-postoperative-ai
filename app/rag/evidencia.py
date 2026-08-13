# -*- coding: utf-8 -*-
"""Evidencia como objeto, y versión del conocimiento.

POR QUÉ EXISTE
--------------
Hasta FASE 4 la política de "cita o silencio" vivía en el prompt y en un
filtro de frases: se le pedía al modelo que citara, y después se borraban las
frases sospechosas. Eso es una política, no una propiedad. Un modelo puede
escribir «[FUENTE: protocolo de apendicectomía]» sin haber leído nada, y el
texto pasaría todos los filtros de forma.

Aquí la cita deja de ser texto y pasa a ser una REFERENCIA a un objeto que el
código creó. El `evidence_id` se genera al recuperar, a partir del contenido
real del fragmento; el modelo solo puede devolver identificadores que ya
existen, y el `EvidenceGate` comprueba uno por uno que:

    · existan,
    · pertenezcan al recuperado de ESTE turno,
    · su documento siga activo,
    · y correspondan a la versión de conocimiento vigente.

Una cita inventada no falla por parecerse poco a una cita: falla porque
apunta a un identificador que nadie creó.

VERSIÓN DEL CONOCIMIENTO
------------------------
`kb_version` es un hash del conjunto de documentos ACTIVOS (doc_id + sha256).
Cambia al subir y al eliminar. Sirve para dos cosas que la rúbrica pide
demostrar: fechar cada respuesta clínica contra el estado del corpus que la
produjo, y garantizar que ninguna caché sobreviva a un borrado.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Evidence:
    """Un fragmento recuperado, con todo lo necesario para auditarlo."""

    evidence_id: str
    doc_id: str
    chunk_id: str
    document_title: str
    sha256: str
    text: str
    retrieval_score: float
    kb_version: str
    source_location: str = ""     # página/sección cuando el extractor la conoce
    chunk_index: int = 0

    def como_cita(self) -> dict:
        """Lo que se muestra y lo que va al acta. El texto NO se duplica
        entero: el acta guarda la referencia, no una copia del corpus."""
        return {
            "evidence_id": self.evidence_id,
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "documento": self.document_title,
            "sha256": self.sha256,
            "ubicacion": self.source_location,
            "chunk": self.chunk_index,
            "distancia": self.retrieval_score,
            "kb_version": self.kb_version,
            "extracto": self.text[:180],
        }


def calcular_kb_version(documentos) -> str:
    """Huella del conocimiento vigente.

    Se construye SOLO con los documentos activos: un tombstone no cuenta, así
    que eliminar cambia la versión igual que añadir. Ordenado por doc_id para
    que la versión no dependa del orden de subida.

    Acepta el mapa del manifiesto {doc_id: entrada} —donde el doc_id es la
    CLAVE y no un campo de la entrada— o una lista de entradas que ya lleven
    `doc_id` dentro. La primera forma es la que usa `ingest`; buscar el
    doc_id dentro de la entrada devolvía siempre "kb_vacia".
    """
    if isinstance(documentos, dict):
        pares = documentos.items()
    else:
        pares = [(d.get("doc_id"), d) for d in documentos]
    partes = sorted(
        f"{doc_id}:{entrada.get('sha256')}"
        for doc_id, entrada in pares
        if doc_id and entrada.get("estado") == "disponible"
    )
    if not partes:
        return "kb_vacia"
    return "kb_" + hashlib.sha256("|".join(partes).encode()).hexdigest()[:16]


def nuevo_evidence_id(kb_version: str, doc_id: str, chunk_id: str, texto: str) -> str:
    """Identificador determinista y verificable.

    Se deriva del contenido: mismo fragmento, misma versión del conocimiento →
    mismo id. Y como incluye la kb_version, un id emitido antes de un borrado
    deja de ser válido después, sin necesidad de invalidar nada a mano.

    Es deliberadamente IMPOSIBLE de adivinar para el modelo: depende del hash
    del texto, que el modelo ve, pero también del estado global del corpus.
    """
    semilla = f"{kb_version}|{doc_id}|{chunk_id}|{texto}".encode()
    return "ev_" + hashlib.sha256(semilla).hexdigest()[:12]


@dataclass
class RegistroDeTurno:
    """Evidencia recuperada en un turno concreto.

    Existe para que la comprobación del gate sea local al turno: una cita
    válida de hace tres turnos NO sirve para justificar la frase de ahora. Sin
    esto, el modelo podría reciclar un identificador legítimo para sostener
    una afirmación sobre otra cosa.
    """

    kb_version: str
    evidencias: dict[str, Evidence] = field(default_factory=dict)
    consultas: list[str] = field(default_factory=list)
    candidatos_totales: int = 0
    mejor_distancia: float | None = None
    latencia_ms: int = 0

    def registrar(self, evs: Iterable[Evidence]) -> None:
        for e in evs:
            self.evidencias[e.evidence_id] = e

    def ids(self) -> list[str]:
        return sorted(self.evidencias)

    def obtener(self, evidence_id: str) -> Evidence | None:
        return self.evidencias.get(evidence_id)

    def hay_evidencia(self) -> bool:
        return bool(self.evidencias)
