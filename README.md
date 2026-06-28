<div align="center">

<img src="server/app/static/img/cybersen-forge-logo.png" width="260" alt="Cybersen Forge">

# Cybersen Forge

### Command & Control Platform

Desarrollado por el equipo **Cybersen**
Operador: **TonyTrapper**

[![Build Agents](https://github.com/TonyTrapper/Cybersen-Forge/actions/workflows/build-agents.yml/badge.svg)](https://github.com/TonyTrapper/Cybersen-Forge/actions/workflows/build-agents.yml)
[![License](https://img.shields.io/badge/license-BSD%202--Clause-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/agents-Linux%20%7C%20Windows-informational)](https://github.com/TonyTrapper/Cybersen-Forge)

</div>

---

## Descripción

**Cybersen Forge** es una plataforma Command & Control desarrollada desde cero para centralizar la administración de agentes, sesiones, ejecución remota de comandos y operaciones de pivoting desde una interfaz web.

El proyecto nació inspirado por los retos técnicos presentados durante **SecOpsDay** y por el trabajo de investigación y desarrollo que realizamos como equipo dentro de **Cybersen**.

A partir de esa experiencia decidimos llevar el aprendizaje más allá de un desafío puntual y construir una herramienta propia, funcional y extensible.

Forge no es únicamente una interfaz visual ni una demostración conceptual. La plataforma permite desplegar agentes reales en Linux y Windows, administrar múltiples sesiones, ejecutar comandos directamente sobre los hosts, detectar redes conectadas y crear relays hacia servicios internos.

## Características principales

* Dashboard web centralizado.
* Autenticación para el operador.
* Administración simultánea de múltiples agentes.
* Estados de sesión:

  * `online`
  * `idle`
  * `offline`
* Actualización en tiempo real mediante Server-Sent Events.
* Agentes nativos para:

  * Linux AMD64.
  * Windows AMD64.
* Agente desarrollado desde una única base de código en Go.
* Enrolamiento mediante tokens temporales de un solo uso.
* Ejecución remota de comandos directamente sobre el host.
* Consola unificada para cada sesión.
* Historial persistente de tareas y resultados.
* Inventario automático del sistema.
* Detección de interfaces y redes conectadas.
* Visualización de posibles rutas de pivoting.
* Creación y administración de relays SSH.
* Acceso a servicios ubicados detrás de un agente.
* Distribución de binarios desde el servidor.
* Verificación de integridad mediante SHA-256.
* Compilación automática mediante GitHub Actions.
* Persistencia de datos mediante SQLite.
* Despliegue reproducible mediante Docker Compose.

## Información recopilada por los agentes

Durante el enrolamiento y las actualizaciones de sesión, los agentes pueden registrar información como:

* Hostname.
* Nombre de usuario.
* Sistema operativo.
* Arquitectura.
* Direcciones IP.
* Interfaces de red.
* Redes directamente conectadas.
* Estado de la sesión.
* Última comunicación con el servidor.
* Versión del agente.

## Ejecución de tareas

Cada agente dispone de una única consola desde la que el operador puede enviar comandos y consultar los resultados.

Las tareas registran:

* Comando ejecutado.
* Salida estándar.
* Salida de error.
* Código de salida.
* Estado de ejecución.
* Duración.
* Timeout.
* Fecha de creación.
* Fecha de inicio.
* Fecha de finalización.

Los comandos se ejecutan directamente sobre el sistema operativo donde está desplegado el agente.

No se requiere Podman, Docker, Alpine ni ningún runtime adicional para ejecutar comandos en el host.

## Arquitectura

```text
                             Operador
                                │
                                ▼
                  ┌─────────────────────────┐
                  │    Cybersen Forge       │
                  │                         │
                  │  FastAPI                │
                  │  Dashboard web          │
                  │  SQLite                 │
                  │  Task Manager           │
                  │  Relay Manager          │
                  └────────────┬────────────┘
                               │
                        HTTP / HTTPS
                               │
               ┌───────────────┴───────────────┐
               │                               │
               ▼                               ▼
     ┌───────────────────┐           ┌───────────────────┐
     │ Linux Agent       │           │ Windows Agent     │
     │ AMD64             │           │ AMD64             │
     └─────────┬─────────┘           └─────────┬─────────┘
               │                               │
               ├── Remote commands             ├── Remote commands
               ├── Host inventory              ├── Host inventory
               ├── Network discovery           └── Network discovery
               │
               ▼
     ┌─────────────────────────┐
     │ Internal Network        │
     │                         │
     │ SSH / TCP services      │
     └────────────┬────────────┘
                  │
                  ▼
       Relay disponible para
       el equipo del operador
```

El servidor de Forge administra las sesiones y las tareas.

Los agentes consultan periódicamente el servidor, reciben comandos, los ejecutan directamente en el host y devuelven los resultados.

Cuando un agente tiene acceso a una red interna, Forge puede utilizar esa sesión como punto de tránsito para alcanzar servicios que no son accesibles directamente desde el equipo del operador.

Más detalles sobre el diseño se encuentran en:

[`docs/architecture.md`](docs/architecture.md)

## Requisitos

### Servidor

* Linux.
* Docker.
* Docker Compose.
* OpenSSL.
* Make, para las tareas de compilación.
* Go, cuando los agentes se compilan localmente.

### Agentes

Los agentes no requieren dependencias externas adicionales.

Plataformas actualmente soportadas:

| Sistema | Arquitectura | Binario                            |
| ------- | ------------ | ---------------------------------- |
| Linux   | AMD64        | `cybersen-forge-linux-amd64`       |
| Windows | AMD64        | `cybersen-forge-windows-amd64.exe` |

## Inicio rápido

### 1. Clonar el repositorio

```bash
git clone https://github.com/TonyTrapper/Cybersen-Forge.git
cd Cybersen-Forge
```

### 2. Crear el archivo de configuración

```bash
cp .env.example .env
```

### 3. Generar secretos

Genera valores diferentes para cada variable sensible:

```bash
openssl rand -hex 24
openssl rand -hex 32
openssl rand -hex 32
```

Actualiza como mínimo las siguientes variables dentro de `.env`:

```env
TEAM_NONCE=NONCE-REAL-DEL-EQUIPO
OPERATOR_PASSWORD=CONTRASENA-SEGURA
SESSION_SECRET=SECRETO-DE-SESION
AGENT_ENROLLMENT_TOKEN=TOKEN-DE-ENROLAMIENTO
```

El archivo `.env` no debe publicarse ni incluirse dentro del repositorio.

### 4. Levantar el servidor

```bash
docker compose up --build -d
```

Verifica los contenedores:

```bash
docker compose ps
```

Consulta los logs:

```bash
docker compose logs -f
```

El dashboard estará disponible localmente en:

```text
http://127.0.0.1:8000
```

Para detener la plataforma:

```bash
docker compose down
```

## Compilación de agentes

### Compilar todos los agentes

```bash
make agents
```

### Compilar el agente Linux

```bash
make agent-linux
```

### Compilar el agente Windows

```bash
make agent-windows
```

Los binarios se generan en:

```text
bin/cybersen-forge-linux-amd64
bin/cybersen-forge-windows-amd64.exe
```

## Agente Linux

Configura la dirección del servidor y el token de enrolamiento:

```bash
export FORGE_SERVER='http://127.0.0.1:8000'
export FORGE_ENROLLMENT_TOKEN='TOKEN-CONFIGURADO-EN-EL-SERVIDOR'
```

Asigna permisos de ejecución:

```bash
chmod +x ./bin/cybersen-forge-linux-amd64
```

Ejecuta el agente:

```bash
./bin/cybersen-forge-linux-amd64
```

Después del enrolamiento, el sistema aparecerá en el dashboard de Forge.

El agente podrá:

* Registrar la sesión.
* Recibir comandos.
* Ejecutar comandos directamente en Linux.
* Enviar resultados al servidor.
* Actualizar su estado.
* Reportar interfaces y redes.
* Participar en operaciones de relay y pivoting.

## Agente Windows

Abre PowerShell y configura las variables:

```powershell
$env:FORGE_SERVER = "http://IP-DEL-SERVIDOR:8000"
$env:FORGE_ENROLLMENT_TOKEN = "TOKEN-CONFIGURADO-EN-EL-SERVIDOR"
```

Ejecuta el agente:

```powershell
.\bin\cybersen-forge-windows-amd64.exe
```

Después del enrolamiento, el equipo aparecerá en el dashboard.

El agente Windows podrá:

* Registrar la sesión.
* Recibir tareas.
* Ejecutar comandos directamente en Windows.
* Recopilar información del sistema.
* Detectar interfaces de red.
* Reportar redes conectadas.
* Enviar resultados al servidor.
* Mantener actualizado su estado.

## Consola de comandos

Cada sesión dispone de una consola unificada.

No existe una separación entre las vistas Shell y System. Los comandos, el inventario y los resultados se administran desde la misma consola asociada con el agente seleccionado.

### Ejemplos para Linux

```bash
hostname
whoami
uname -a
id
pwd
ip addr
ip route
ss -lntup
ps aux
```

### Ejemplos para Windows

```powershell
hostname
whoami
systeminfo
ipconfig /all
route print
Get-NetIPAddress
Get-NetRoute
Get-Process
```

## Descubrimiento de redes

Forge recopila las interfaces reportadas por cada agente y presenta las redes directamente conectadas.

Ejemplo:

```text
Interface: eth0
Address:   172.31.32.248
Network:   172.31.32.0/20

Interface: eth1
Address:   10.20.30.5
Network:   10.20.30.0/24
```

Esta información permite identificar agentes que pueden actuar como puntos de acceso hacia otras redes.

## Pivoting y relays

Forge permite crear relays hacia servicios que son alcanzables desde un agente, pero no directamente desde el equipo del operador.

Ejemplo de topología:

```text
Operator
127.0.0.1:22000
        │
        ▼
Cybersen Forge
        │
        ▼
Linux Agent
10.20.30.5
        │
        ▼
Internal SSH Server
10.20.30.10:22
```

El destino interno:

```text
10.20.30.10:22
```

puede asociarse con un puerto local:

```text
127.0.0.1:22000
```

Después de crear el relay desde el dashboard, el operador puede utilizar el puerto configurado para comunicarse con el servicio interno.

Ejemplo:

```bash
ssh -p 22000 usuario@127.0.0.1
```

El tráfico es transportado mediante la sesión del agente hasta el servicio situado en la red interna.

Desde el dashboard se puede:

* Crear un relay.
* Seleccionar el agente.
* Definir la dirección de destino.
* Definir el puerto de destino.
* Consultar el estado del relay.
* Visualizar el puerto local asignado.
* Detener el relay.

## Distribución de agentes

Forge puede publicar los binarios compilados para facilitar su despliegue.

Los agentes disponibles pueden consultarse desde el dashboard junto con:

* Plataforma.
* Arquitectura.
* Nombre del archivo.
* Tamaño.
* Hash SHA-256.
* Comando de descarga.
* Comando de ejecución.

La verificación SHA-256 permite comprobar la integridad del binario antes de ejecutarlo.

Ejemplo:

```bash
sha256sum ./cybersen-forge-linux-amd64
```

En Windows:

```powershell
Get-FileHash .\cybersen-forge-windows-amd64.exe -Algorithm SHA256
```

## GitHub Actions

El repositorio incluye un workflow para compilar automáticamente los agentes.

Archivo:

```text
.github/workflows/build-agents.yml
```

Artefactos generados:

```text
cybersen-forge-linux-amd64
cybersen-forge-windows-amd64.exe
```

El estado de la compilación puede verificarse desde el badge ubicado en la cabecera de este README.

## Pruebas

Ejecuta la suite de pruebas con:

```bash
make test
```

Las pruebas incluyen componentes relacionados con:

* Autenticación.
* Registro de agentes.
* Enrolamiento.
* Creación de sesiones.
* Administración de tareas.
* Recepción de resultados.
* Estados de ejecución.
* Manejo de códigos de salida.
* Manejo de timeouts.
* Inventario del sistema.
* Detección de interfaces.
* Persistencia de datos.
* Administración de relays.

## Estructura del proyecto

```text
Cybersen-Forge/
├── agent/
│   └── Código fuente del agente en Go
├── bin/
│   ├── cybersen-forge-linux-amd64
│   └── cybersen-forge-windows-amd64.exe
├── docs/
│   ├── architecture.md
│   └── branding.md
├── server/
│   └── app/
│       ├── static/
│       │   └── img/
│       │       └── cybersen-forge-logo.png
│       ├── templates/
│       ├── routes/
│       ├── services/
│       └── models/
├── tests/
├── .github/
│   └── workflows/
│       └── build-agents.yml
├── docker-compose.yml
├── .env.example
├── Makefile
├── SECURITY.md
├── LICENSE
└── README.md
```

## Estado del proyecto

* [x] Servidor C2 desarrollado con FastAPI.
* [x] Dashboard web.
* [x] Autenticación de operador.
* [x] Persistencia mediante SQLite.
* [x] Administración de múltiples sesiones.
* [x] Estados `online`, `idle` y `offline`.
* [x] Actualización mediante Server-Sent Events.
* [x] Agente Linux AMD64.
* [x] Agente Windows AMD64.
* [x] Enrolamiento mediante tokens temporales.
* [x] Ejecución directa de comandos sobre el host.
* [x] Consola unificada por agente.
* [x] Visualización del output.
* [x] Captura de códigos de salida.
* [x] Manejo de timeouts.
* [x] Historial persistente.
* [x] Inventario automático del sistema.
* [x] Detección de interfaces.
* [x] Detección de redes conectadas.
* [x] Visualización de rutas potenciales.
* [x] Distribución de agentes.
* [x] Verificación SHA-256.
* [x] Compilación mediante GitHub Actions.
* [x] Creación de relays SSH.
* [x] Administración de relays desde el dashboard.
* [x] Pivoting hacia redes internas.
* [ ] Cifrado extremo a extremo de tareas y resultados.
* [ ] Control de acceso basado en roles.
* [ ] Exportación estructurada de evidencias.
* [ ] Soporte para arquitecturas adicionales.
* [ ] Sistema modular de extensiones.

## Identidad visual

La identidad visual de Forge forma parte del proyecto desarrollado por el equipo **Cybersen**.

La paleta de colores, el uso del logotipo y los lineamientos de interfaz están documentados en:

[`docs/branding.md`](docs/branding.md)

El logotipo principal utilizado en este README se encuentra en:

```text
server/app/static/img/cybersen-forge-logo.png
```

## Seguridad

Antes de desplegar Forge:

* Cambia todas las credenciales predeterminadas.
* Utiliza secretos independientes y aleatorios.
* No publiques el archivo `.env`.
* No publiques tokens de enrolamiento.
* Utiliza HTTPS para proteger las comunicaciones.
* Restringe el acceso al dashboard mediante firewall o VPN.
* Limita los puertos expuestos.
* Revisa los agentes enrolados.
* Elimina las sesiones que ya no sean necesarias.
* Rota periódicamente las credenciales.
* Mantén registros de las operaciones realizadas.
* Despliega la plataforma únicamente en entornos autorizados.

Consulta:

[`SECURITY.md`](SECURITY.md)

## Uso autorizado

Cybersen Forge está orientado a:

* Investigación en ciberseguridad.
* Formación técnica.
* CTF.
* Laboratorios controlados.
* Simulaciones de Red Team.
* Evaluaciones de seguridad autorizadas.
* Desarrollo y validación de capacidades ofensivas.
* Estudio de arquitecturas Command & Control.
* Prácticas de pivoting y segmentación de redes.

No utilices esta herramienta contra sistemas, redes, dispositivos o servicios sin autorización previa y explícita.

El usuario es responsable de cumplir las leyes y regulaciones aplicables.

## Equipo

Proyecto desarrollado por el equipo **Cybersen**.

Operador principal:

```text
TonyTrapper
```

Repositorio:

```text
https://github.com/TonyTrapper/Cybersen-Forge
```

## Licencia

Cybersen Forge se distribuye bajo la licencia **BSD 2-Clause**.

Consulta [`LICENSE`](LICENSE) para revisar los términos completos.

---

<div align="center">

<img src="server/app/static/img/cybersen-forge-logo.png" width="150" alt="Cybersen Forge">

**Built by Cybersen**

[GitHub Repository](https://github.com/TonyTrapper/Cybersen-Forge)

</div>
