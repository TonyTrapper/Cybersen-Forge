# Arquitectura de Cybersen Forge

```text
Operator browser
      │
      │ authenticated HTTP/SSE
      ▼
FastAPI dashboard ─── SQLite
      ▲
      │ enroll / check-in / task result
      │
Linux and Windows implants
```

## Componentes

### Servidor

- FastAPI para dashboard, enrolamiento y tareas.
- SQLite con WAL para persistencia sencilla.
- Server-Sent Events para refresco del tablero.
- Docker Compose para despliegue reproducible.

### Implant

- Código único en Go.
- Compilación cruzada Linux/Windows.
- Identidad local persistente en `~/.cybersen-forge/identity.json`.
- Polling configurable y reporte de salida estructurada.

### Fase de pivote

La siguiente fase añadirá un relay autenticado que transporte únicamente mensajes del protocolo Cybersen Forge entre un agente interno sin egress y el servidor central.
