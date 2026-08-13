# Arquitectura de RONDA

Los nombres de los nodos corresponden a los módulos reales del código. Se
pueden verificar abriendo los archivos que se citan en cada bloque.

---

## 1. Arquitectura del sistema

```mermaid
flowchart TD
    P([Paciente]) -->|voz| CAP[Captura de audio<br/>MediaRecorder]
    CAP --> VAD[VAD por energía<br/>+ barge-in]
    VAD -->|webm/opus| WS[[WebSocket<br/>/ws/llamada/id]]

    WS --> STT[Groq Whisper Large V3<br/>transcripción]
    STT --> ROUTER{Router conversacional<br/>conversation/router.py}

    ROUTER -->|respuesta clínica| MOTOR
    ROUTER -->|pregunta lateral| RAG
    ROUTER -->|no respuesta| MOTOR
    ROUTER -->|fuera de misión| DECLINA[Declinar y retomar<br/>el tema pendiente]

    subgraph MOTOR [Motor de decisión · decision/engine.py]
        direction TB
        C1[Carril textual determinista]
        C2[Carril numérico determinista]
        C3[Slots estructurados]
        C4[Composición clínica<br/>multidominio]
        C5[Riesgo histórico<br/>de la llamada]
        C6[Señal del modelo<br/>de lenguaje]
        C1 & C2 & C3 & C4 & C5 & C6 --> FUSION[Fusión conservadora<br/>nivel = máximo]
    end

    subgraph RAG [Conocimiento · rag/]
        direction TB
        Q[Consulta] --> EMB[FastEmbed<br/>MiniLM multilingüe 384d]
        EMB --> CHROMA[(ChromaDB)]
        CHROMA --> REC[Recuperador<br/>+ reordenamiento léxico]
        REC --> EV[Evidencias del turno<br/>evidence_id · sha256 · kb_version]
    end

    FUSION --> EJES
    subgraph EJES [Decisión de doble eje]
        direction LR
        R[RIESGO CLÍNICO<br/>verde · amarillo · rojo]
        E[ESTADO DE EVALUACIÓN<br/>completa · incompleta · fallida]
    end
    EJES --> ACC[ACCIÓN OPERATIVA<br/>continuar · repreguntar<br/>revisión humana · escalar]

    EV --> GEN[Generación estructurada<br/>oraciones + ids de evidencia]
    ACC --> GEN
    GEN --> GATE{{Evidence Gate<br/>conversation/gate.py}}
    GATE -->|aprobado| RESP[Respuesta final]
    GATE -->|rechazado| ABST[Abstención explícita]
    ABST --> RESP

    RESP --> TTS[Edge TTS · es-CO-SalomeNeural<br/>respaldo local: Piper]
    TTS -->|audio en streaming| P

    subgraph CONSOLA [Consola · /consola]
        direction TB
        SUB[Subir documento] --> KB
        DEL[Eliminar documento] --> KB
        VER[Verificar olvido] --> KB
        KB[(Base de conocimiento<br/>manifiesto + índice)]
        KB --> KBV[kb_version<br/>cambia al subir y al borrar]
    end
    KB -.-> CHROMA

    subgraph PERSIST [Persistencia y trazabilidad]
        direction LR
        AL[Alertas] & AC[Actas] & LOG[events.jsonl]
    end
    ACC --> AL
    RESP --> AC
    GATE --> LOG
    STT --> LOG
    TTS --> LOG

    classDef riesgo fill:#16283D,stroke:#3B9EFF,color:#EEF3F9
    classDef gate fill:#0C1725,stroke:#35C98A,color:#EEF3F9
    class R,E,ACC riesgo
    class GATE,EV gate
```

**Puntos que conviene mirar dos veces:**

- La **recuperación ocurre siempre**, antes de decidir si la pregunta es de
  misión. Decidirlo antes de mirar el conocimiento activo fue precisamente el
  fallo que dejaba inalcanzable un documento recién subido.
- El **Evidence Gate está antes del TTS**, no después. Lo que no pasa la
  compuerta nunca llega a pronunciarse.
- `kb_version` cambia **tanto al subir como al borrar**. Es lo que hace
  demostrable el olvido.

---

## 2. Flujo de seguridad y decisión

```mermaid
flowchart TD
    T[Transcripción del turno] --> CLASE{¿Qué hizo<br/>el paciente?}

    CLASE -->|respuesta clínica| SEN
    CLASE -->|pregunta lateral| LAT[Se responde con evidencia<br/>o se declara que no la hay]
    CLASE -->|no respuesta| NOR[Dominio marcado<br/>como NO EVALUADO]

    LAT --> RETOMA[Retomar el tema pendiente<br/>NO consume el intento clínico]
    RETOMA --> SEN

    NOR --> SEN
    SEN[Señales clínicas extraídas] --> CARRILES

    subgraph CARRILES [Seis carriles en paralelo]
        direction LR
        D1[Deterministas<br/>texto · números · slots]
        D2[Composición<br/>multidominio]
        D3[Histórico<br/>de la llamada]
        D4[Modelo de<br/>lenguaje]
    end

    CARRILES --> MAX[/nivel = MÁXIMO de los carriles/]
    MAX --> RIESGO{RIESGO CLÍNICO}
    SEN --> COB[Cobertura por dominio<br/>evaluado ≠ positivo]
    COB --> EVAL{ESTADO DE EVALUACIÓN}

    RIESGO -->|verde| RV[Verde]
    RIESGO -->|amarillo| RA[Amarillo]
    RIESGO -->|rojo| RR[Rojo]
    EVAL -->|completa| EC[Completa]
    EVAL -->|incompleta| EI[Incompleta]
    EVAL -->|fallida| EF[Fallida]

    RV & RA & RR & EC & EI & EF --> COMB[Combinación<br/>solo al cerrar la llamada]
    COMB --> ACCION{ACCIÓN OPERATIVA}

    ACCION --> A1[Continuar seguimiento]
    ACCION --> A2[Repreguntar una vez<br/>pregunta cerrada, sin bucles]
    ACCION --> A3[Revisión humana]
    ACCION --> A4[Escalar a enfermería<br/>+ acta de alerta inmediata]

    R1>**ROJO nunca se rebaja.**<br/>El modelo puede subir una alarma;<br/>jamás puede bajarla.]
    R2>**SIN RESPUESTA ≠ SIN SÍNTOMA.**<br/>Un dominio no evaluado queda<br/>desconocido, no descartado.]
    R3>**SIN EVIDENCIA → SIN AFIRMACIÓN CLÍNICA.**<br/>La compuerta corre antes del TTS.]

    MAX -.-> R1
    COB -.-> R2
    ACCION -.-> R3

    classDef regla fill:#0C1725,stroke:#EDB94A,color:#EEF3F9
    classDef rojo fill:#0C1725,stroke:#FF6262,color:#EEF3F9
    classDef verde fill:#0C1725,stroke:#35C98A,color:#EEF3F9
    class R1,R2,R3 regla
    class RR,A4 rojo
    class RV,A1 verde
```

### Las tres reglas que gobiernan el sistema

| Regla | Qué impide | Dónde vive |
|---|---|---|
| **ROJO nunca se rebaja** | Que el modelo tranquilice sobre una alarma detectada por reglas | `decision/engine.py` |
| **Sin respuesta ≠ sin síntoma** | Que un paciente no evaluado se pinte de verde | `decision/cobertura.py` |
| **Sin evidencia → sin afirmación** | Que se pronuncie contenido clínico inventado | `conversation/gate.py` |

---

## 3. Ciclo de conocimiento vivo

```mermaid
sequenceDiagram
    autonumber
    actor U as Personal clínico
    participant C as Consola
    participant I as Ingesta
    participant V as ChromaDB
    actor P as Paciente

    P->>+V: ¿Qué dice el protocolo sobre X?
    V-->>-P: sin evidencia suficiente → ABSTENCIÓN

    U->>+C: Subir documento
    C->>I: extraer, trocear, vectorizar
    I->>V: indexar fragmentos
    V-->>-C: kb_version cambia

    P->>+V: la MISMA pregunta
    V-->>-P: responde CITANDO documento y fragmento

    U->>+C: Eliminar documento
    C->>V: borrar vectores + lápida en el manifiesto
    V-->>-C: kb_version vuelve a cambiar

    U->>+C: Verificar olvido
    C->>V: sondear el almacén
    V-->>-C: 0 vectores restantes

    P->>+V: la MISMA pregunta otra vez
    V-->>-P: vuelve a ABSTENERSE
```

Este ciclo es reproducible de extremo a extremo con las pruebas de
`tests/test_g5_end_to_end.py`, que recorren la ruta humana completa desde
`POST /api/llamada/iniciar`.
