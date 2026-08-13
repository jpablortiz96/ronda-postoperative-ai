# -*- coding: utf-8 -*-
"""Conjunto de evaluación del recuperador, derivado SOLO de los documentos.

DE DÓNDE SALEN ESTAS PREGUNTAS
------------------------------
De los planes de cuidado del corpus oficial, leyéndolos. NO de las etiquetas
del dataset clínico: eso mediría otra cosa (si reproducimos unas anotaciones)
y además contaminaría la evaluación de FASE 4 con la de FASE 5.

Cada pregunta declara qué documento y qué fragmento la sustenta. Las de tipo
`sin_respuesta` son igual de importantes: comprobar que el sistema NO recupera
nada cuando no hay nada es la mitad de "cita o silencio".

REGISTROS
    pregunta        lo que diría un paciente
    doc_esperado    documento que debe aparecer en el top-k (None = ninguno)
    fragmento       texto del plan que justifica la respuesta esperada
    tipo            literal | parafrasis | coloquial | sin_respuesta
    procedimiento   a qué plan pertenece
"""
from __future__ import annotations

# Documentos del corpus oficial usados como fuente. Los nombres son los
# reales dentro de `dataset/textos/`.
DOC_APX = "PLAN DE CUIDADO EN CASA DE PACIENTE EN POSTOPERATORIO DE APENDICECTOMÍA"
DOC_COL = "PLAN DE CUIDADO COLECISTECTOMIA"
DOC_ART = "PLAN CASERO REEMPLAZO TOTAL DE RODILLA"
DOC_ART2 = "Recomendaciones Programa Reemplazo Articular de Rodilla"

PREGUNTAS: list[dict] = [
    # ── Apendicectomía · respuestas que SÍ están en el plan ─────────────────
    {"pregunta": "¿Puedo lavarme la herida?", "doc_esperado": DOC_APX,
     "tipo": "parafrasis", "procedimiento": "Apendicectomía",
     "fragmento": "Lavar la herida todos los días con agua y jabón líquido neutro de "
                  "uso personal. Mantenerla seca y sin humedad."},
    {"pregunta": "¿Me puedo meter a la piscina?", "doc_esperado": DOC_APX,
     "tipo": "coloquial", "procedimiento": "Apendicectomía",
     "fragmento": "No sumergir la herida en agua (tinas, piscinas, ríos o mar) hasta "
                  "que el médico lo autorice."},
    {"pregunta": "¿Cuánta agua debo tomar al día?", "doc_esperado": DOC_APX,
     "tipo": "literal", "procedimiento": "Apendicectomía",
     "fragmento": "Mantener buena hidratación, tomando entre 6 y 8 vasos de agua al día."},
    {"pregunta": "¿Puedo cargar cosas pesadas o montar bicicleta?", "doc_esperado": DOC_APX,
     "tipo": "literal", "procedimiento": "Apendicectomía",
     "fragmento": "Evitar actividades que requieran esfuerzo fuerte, como cargar peso, "
                  "montar bicicleta, trotar o levantar objetos pesados, hasta la cita "
                  "de control postoperatorio."},
    {"pregunta": "¿Le puedo poner una crema o alcohol a la herida?",
     "doc_esperado": DOC_APX, "tipo": "coloquial", "procedimiento": "Apendicectomía",
     "fragmento": "No retirar vendajes ni aplicar cremas, alcohol, yodo o remedios "
                  "caseros en la herida, a menos que el médico lo indique."},
    {"pregunta": "¿Desde cuántos grados de fiebre debo ir a urgencias?",
     "doc_esperado": DOC_APX, "tipo": "parafrasis", "procedimiento": "Apendicectomía",
     "fragmento": "Fiebre mayor de 38 °C."},
    {"pregunta": "¿Qué como para no quedar estreñido?", "doc_esperado": DOC_APX,
     "tipo": "coloquial", "procedimiento": "Apendicectomía",
     "fragmento": "Incluir alimentos con fibra, como cereales integrales, frutas y "
                  "verduras, para evitar el estreñimiento."},
    {"pregunta": "¿Puedo empezar a caminar?", "doc_esperado": DOC_APX,
     "tipo": "parafrasis", "procedimiento": "Apendicectomía",
     "fragmento": "Empezar a caminar en casa de manera gradual; caminar ayuda a la "
                  "cicatrización y a la recuperación."},
    {"pregunta": "¿Tengo que ir al control si ya me siento bien?",
     "doc_esperado": DOC_APX, "tipo": "parafrasis", "procedimiento": "Apendicectomía",
     "fragmento": "Acudir al control postoperatorio con el cirujano, incluso si hay "
                  "mejoría, para verificar la evolución."},
    {"pregunta": "¿Cuántas comidas al día debo hacer?", "doc_esperado": DOC_APX,
     "tipo": "literal", "procedimiento": "Apendicectomía",
     "fragmento": "Hacer cinco comidas al día en porciones pequeñas para favorecer la "
                  "recuperación nutricional."},

    # ── Colecistectomía ────────────────────────────────────────────────────
    {"pregunta": "¿Qué es la vesícula y para qué sirve?", "doc_esperado": DOC_COL,
     "tipo": "parafrasis", "procedimiento": "Colecistectomía",
     "fragmento": "La vesícula biliar es un órgano pequeño en forma de pera ubicada en "
                  "la zona derecha del abdomen debajo del hígado cuya función es "
                  "concentrar y liberar bilis que ayuda a la digestión de las grasas."},
    {"pregunta": "¿Qué son los cálculos en la vesícula?", "doc_esperado": DOC_COL,
     "tipo": "coloquial", "procedimiento": "Colecistectomía",
     "fragmento": "COLELITIASIS: presencia de cálculos al interior de la vesícula biliar."},
    {"pregunta": "¿Por qué me daba ese dolor tan fuerte del lado derecho?",
     "doc_esperado": DOC_COL, "tipo": "coloquial", "procedimiento": "Colecistectomía",
     "fragmento": "Dolor agudo tipo cólico en el lado derecho del abdomen."},
    {"pregunta": "¿Cuándo se opera la vesícula?", "doc_esperado": DOC_COL,
     "tipo": "parafrasis", "procedimiento": "Colecistectomía",
     "fragmento": "INDICACIONES: cálculos > 3 cm, anomalías congénitas con cálculo, "
                  "inflamación de la vesícula, anemia falciforme, microlitiasis."},
    {"pregunta": "¿Qué es la ictericia?", "doc_esperado": DOC_COL,
     "tipo": "literal", "procedimiento": "Colecistectomía",
     "fragmento": "Ictericia (en algunos casos), entre los síntomas."},

    # ── Reemplazo articular ────────────────────────────────────────────────
    {"pregunta": "¿Qué signos de alarma vigilo después del reemplazo de rodilla?",
     "doc_esperado": DOC_ART, "tipo": "parafrasis",
     "procedimiento": "Reemplazo de cadera/rodilla",
     "fragmento": "Signos de alarma: fiebre mayor a 38°, aumento del dolor, "
                  "inflamación o enrojecimiento de la herida, sangrado o secreción en "
                  "la herida, tos, dolor en el pecho."},
    {"pregunta": "¿Es normal que me salgan ampollas o zonas rojas en la pierna?",
     "doc_esperado": DOC_ART2, "tipo": "parafrasis",
     "procedimiento": "Reemplazo de cadera/rodilla",
     "fragmento": "Vigile si tiene ampollas, granos o zonas rojas en la extremidad."},
    {"pregunta": "¿Qué hago con los ejercicios de la rodilla en casa?",
     "doc_esperado": DOC_ART, "tipo": "coloquial",
     "procedimiento": "Reemplazo de cadera/rodilla",
     "fragmento": "Tabla de ejercicios de rehabilitación postoperatoria de reemplazo "
                  "de rodilla, con registro diario."},
    {"pregunta": "Si toso con flema o con sangre, ¿eso importa?",
     "doc_esperado": DOC_ART2, "tipo": "coloquial",
     "procedimiento": "Reemplazo de cadera/rodilla",
     "fragmento": "Presencia de tos con flema o sangre."},
    {"pregunta": "¿A dónde voy si tengo alguno de esos síntomas?",
     "doc_esperado": DOC_ART, "tipo": "parafrasis",
     "procedimiento": "Reemplazo de cadera/rodilla",
     "fragmento": "En caso de presentar alguno de estos síntomas acuda al Servicio de "
                  "Urgencias del Hospital."},

    # ── Preguntas SIN respuesta en el corpus ───────────────────────────────
    # Mitad de la propiedad "cita o silencio": si el sistema recupera algo
    # aquí y responde, está inventando con apariencia de fuente.
    {"pregunta": "¿Cuánto cuesta la consulta de control?", "doc_esperado": None,
     "tipo": "sin_respuesta", "procedimiento": "Apendicectomía",
     "fragmento": "Los planes de cuidado no hablan de costos ni de trámites."},
    {"pregunta": "¿Puedo viajar en avión la próxima semana?", "doc_esperado": None,
     "tipo": "sin_respuesta", "procedimiento": "Apendicectomía",
     "fragmento": "Ningún plan del corpus menciona viajes aéreos."},
    {"pregunta": "¿Cuándo puedo volver a tener relaciones sexuales?",
     "doc_esperado": None, "tipo": "sin_respuesta", "procedimiento": "Colecistectomía",
     "fragmento": "No aparece en los planes de cuidado del corpus."},
    {"pregunta": "¿Me van a dar incapacidad laboral y por cuántos días?",
     "doc_esperado": None, "tipo": "sin_respuesta",
     "procedimiento": "Reemplazo de cadera/rodilla",
     "fragmento": "Asunto administrativo, ausente del corpus clínico."},
    {"pregunta": "¿Qué marca de crema cicatrizante me recomienda?",
     "doc_esperado": None, "tipo": "sin_respuesta", "procedimiento": "Apendicectomía",
     "fragmento": "El plan prohíbe aplicar cremas; no recomienda ninguna marca."},
    {"pregunta": "¿Puedo tomarme una cerveza el fin de semana?", "doc_esperado": None,
     "tipo": "sin_respuesta", "procedimiento": "Colecistectomía",
     "fragmento": "El alcohol no aparece en los planes de cuidado del corpus."},
]


def con_respuesta() -> list[dict]:
    return [p for p in PREGUNTAS if p["doc_esperado"]]


def sin_respuesta() -> list[dict]:
    return [p for p in PREGUNTAS if not p["doc_esperado"]]
