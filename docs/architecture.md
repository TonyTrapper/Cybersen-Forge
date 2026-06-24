# Arquitectura de Cybersen Forge

```text
Operator browser
      │
      │ authenticated HTTP / SSE
      ▼
FastAPI dashboard ─── SQLite
      ▲
      │ enroll / check-in / task result
      │
Linux and Windows implants
      │
      └── Linux: persistent isolated shell runtime
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

### Shell persistente Linux

Al habilitar `FORGE_ENABLE_SHELL=true`, el agente inicia una shell `/bin/sh` dentro de un contenedor aislado y mantiene abiertos sus pipes durante la sesión del agente.

Esto permite conservar entre tareas:

- directorio actual (`cd`);
- variables exportadas;
- estado de la shell;
- workspace montado en `/workspace`.

Cada comando termina con un marcador interno que permite recuperar el exit code sin cerrar la shell. Si el comando supera el timeout, el agente elimina el contenedor y crea una shell limpia en la siguiente tarea.

### Vista System

La vista System utiliza tareas de diagnóstico del host y permanece separada de la Shell del laboratorio.

### Fase de pivote

La siguiente fase añadirá un relay autenticado que transporte mensajes del protocolo Cybersen Forge entre un agente interno sin egress y el servidor central.
