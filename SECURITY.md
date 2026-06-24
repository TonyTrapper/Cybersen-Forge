# Política de seguridad

Cybersen Forge está diseñado para laboratorios propios, CTF y sistemas donde exista autorización explícita del propietario.

## Reporte de vulnerabilidades

No publiques credenciales, tokens de enrolamiento ni secretos de sesión en Issues. Reporta de forma privada al mantenedor y adjunta únicamente la información necesaria para reproducir el problema.

## Secretos

- Nunca publiques `.env`.
- Rota `OPERATOR_PASSWORD`, `SESSION_SECRET` y `AGENT_ENROLLMENT_TOKEN` si aparecen en una captura o log.
- Usa HTTPS y `COOKIE_SECURE=true` al desplegar fuera de localhost.
- Restringe los puertos administrativos mediante firewall o security groups.

## Shell del laboratorio

La vista Shell se ejecuta en un runtime de contenedores separado del host y usa:

- filesystem raíz read-only;
- workspace dedicado montado en `/workspace`;
- red deshabilitada;
- capabilities eliminadas;
- `no-new-privileges`;
- límites de procesos, memoria y CPU.

La shell es persistente durante la vida del agente: conserva `cwd` y variables entre tareas. Un timeout reinicia únicamente la shell aislada.

## Alcance

La rama pública está orientada a demostraciones autorizadas del reto «Forja tu Yugo». No debe desplegarse en sistemas de terceros sin consentimiento verificable.
