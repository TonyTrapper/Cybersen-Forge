## 0.5.0 - Navegación operativa

- Consola de sesión simplificada a una única vista de ejecución.
- Historial global de tareas trasladado al menú Auditoría.
- Menú Pivoting habilitado con todos los agentes conectados y sus redes.
- Gestión centralizada de pivotes y listeners desde una página dedicada.

# Changelog

## 0.4.0 - 2026-06-24

- Añade una página web para descargar agentes Linux y Windows.
- Compila ambos artefactos dentro de la imagen del servidor.
- Genera tokens temporales, por plataforma y de un solo uso para enrolamiento.
- Genera comandos de instalación usando la URL actual, una URL privada o un dominio público editable.
- Publica hashes SHA256 y tamaño de cada agente desde el panel.

## 0.3.0 - Pivoting SSH administrado

- Inventario automático de redes IPv4 directamente conectadas por agente.
- Panel de Redes y Pivoting dentro de cada sesión.
- Relays SSH controlados a TCP/22 con listener enlazado a localhost en el host Forge.
- Estados de pivote solicitados, activos, detenidos y fallidos.


## 0.2.0 — Persistent lab shell

- Nueva interfaz por sesión con pestañas **Shell** y **System**.
- Shell persistente dentro del runtime aislado del laboratorio.
- Persistencia de directorio actual y variables entre tareas.
- Soporte de pipes, redirecciones, encadenamientos y comandos multilínea en Shell.
- Campo de comando multilínea con `Enter` para ejecutar y `Shift+Enter` para nueva línea.
- Prompt, historial y etiquetas actualizados para diferenciar Shell y System.
- Compatibilidad temporal con el modo `sandbox` de la versión anterior.
- Prueba automática de persistencia de la shell.

## 0.1.0 — Initial public release

- Renombre oficial a **Cybersen Forge**.
- Integración del logo de Cybersen Forge en login, navegación y favicon.
- Nueva identidad visual roja, negra, blanca y gris.
- Dashboard multi-sesión y consola por agente.
- Agentes reproducibles para Linux y Windows.
- Docker Compose, SQLite y GitHub Actions.
