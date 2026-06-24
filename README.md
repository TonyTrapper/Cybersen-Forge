<div align="center">
  <img src="server/app/static/img/cybersen-forge-logo.png" width="230" alt="Cybersen Forge logo">

# Cybersen Forge

**Command & Control Console**<br>
Equipo **Cybersen** · Operador **TonyTrapper**

[![Build agents](https://github.com/TonyTrapper/Cybersen-Forge/actions/workflows/build-agents.yml/badge.svg)](https://github.com/TonyTrapper/Cybersen-Forge/actions/workflows/build-agents.yml)

</div>

Cybersen Forge es una plataforma C2 desarrollada para el reto insignia **«Forja tu Yugo»**. Integra servidor web, tablero multi-sesión, consola por agente e implants reproducibles para Linux y Windows.

## Capacidades actuales

- Login de operador y NONCE visible en el tablero.
- Inventario multi-sesión con estados `online`, `idle` y `offline`.
- Actualización en tiempo real mediante Server-Sent Events.
- Dos vistas por sesión:
  - **Shell:** terminal persistente dentro del runtime aislado del laboratorio.
  - **System:** tareas de diagnóstico sobre el host del agente.
- La vista Shell conserva directorio actual, variables de entorno, pipes, redirecciones y encadenamientos entre comandos.
- Captura de output, exit code, duración, timeout y timestamps.
- Historial persistente con paneles que no se cierran durante el refresco.
- Implant Linux y Windows desde un único código Go.
- SQLite persistente mediante Docker Compose.
- Compilación automatizada de agentes con GitHub Actions.
- Arquitectura preparada para relay/pivote.

## Arquitectura

```text
                         Internet / laboratorio
                                  │
                                  ▼
                      ┌──────────────────────┐
                      │ Cybersen Forge       │
                      │ FastAPI + SQLite     │
                      │ Dashboard web        │
                      └──────────┬───────────┘
                                 │ HTTPS / polling
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
       Linux implant                    Windows implant
             │
             └── persistent isolated shell runtime
```

Más detalles en [`docs/architecture.md`](docs/architecture.md).

## Inicio rápido

### 1. Configurar el servidor

```bash
cp .env.example .env
```

Genera secretos independientes:

```bash
openssl rand -hex 24
openssl rand -hex 32
openssl rand -hex 32
```

Actualiza como mínimo:

```env
TEAM_NONCE=NONCE-REAL-DEL-EQUIPO
OPERATOR_PASSWORD=...
SESSION_SECRET=...
AGENT_ENROLLMENT_TOKEN=...
```

### 2. Levantar Cybersen Forge

```bash
docker compose up --build -d
docker compose ps
```

Dashboard local:

```text
http://127.0.0.1:8000
```

### 3. Compilar los agentes

```bash
make agent-linux
make agent-windows
```

Artefactos:

```text
bin/cybersen-forge-linux-amd64
bin/cybersen-forge-windows-amd64.exe
```

## Implant Linux con Shell

La vista Shell requiere Podman o Docker y una imagen local:

```bash
podman pull alpine:3.20
```

Ejecuta el agente:

```bash
export FORGE_SERVER='http://127.0.0.1:8000'
export FORGE_ENROLLMENT_TOKEN='EL_TOKEN_DE_TU_ENV'
export FORGE_ENABLE_SHELL='true'
export FORGE_SHELL_RUNTIME='docker'  # usa 'podman' cuando corresponda
export FORGE_SHELL_IMAGE='alpine:3.20'
export FORGE_SHELL_WORKSPACE="$HOME/.cybersen-forge/workspace"

./bin/cybersen-forge-linux-amd64
```

También se aceptan temporalmente `FORGE_ENABLE_SANDBOX`, `FORGE_SANDBOX_IMAGE` y `FORGE_SANDBOX_WORKSPACE` por compatibilidad con la versión anterior.

### Validación de persistencia

Ejecuta estos comandos desde la pestaña **Shell** como tareas separadas:

```bash
cd /tmp
pwd
export FORGE_DEMO=Cybersen
echo "$FORGE_DEMO"
```

`pwd` debe devolver `/tmp` y la variable debe conservarse entre tareas mientras la sesión Shell siga activa.

## Vista System

La pestaña **System** conserva las tareas de inventario del host, por ejemplo:

```text
hostname
whoami
uname -a
ip addr
ps aux
```

## Implant Windows

```powershell
$env:FORGE_SERVER = "http://IP-DEL-SERVIDOR:8000"
$env:FORGE_ENROLLMENT_TOKEN = "EL_TOKEN_DE_TU_ENV"
.\bin\cybersen-forge-windows-amd64.exe
```

La compilación Windows mantiene la vista System. La Shell persistente aislada de esta versión se habilita en agentes Linux con Podman o Docker.

## Pruebas

```bash
make test
```

Las pruebas incluyen persistencia de directorio y variables en Shell mediante un runtime simulado.

## Identidad visual

La paleta, usos del logo y lineamientos de interfaz están documentados en [`docs/branding.md`](docs/branding.md).

## Roadmap del reto

- [x] Código público y documentación reproducible.
- [x] Implant Linux con sesión recibida.
- [x] Implant Windows compilable como `.exe`.
- [x] Ejecución de tareas y visualización del output.
- [x] Tablero multi-sesión.
- [x] Shell persistente aislada para Linux.
- [ ] Relay autenticado para máquina interna sin egress.
- [ ] Exportación de evidencias para el walkthrough.

## Uso autorizado

Este proyecto se publica para laboratorios propios, CTF y entornos expresamente autorizados. Consulta [`SECURITY.md`](SECURITY.md) antes de desplegarlo.

## Licencia

BSD 2-Clause. Consulta [`LICENSE`](LICENSE).
