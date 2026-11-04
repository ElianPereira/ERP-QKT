# Rutina de Seguridad 2026-11-04

**3 hallazgos: 0 críticos, 0 altos, 0 medios, 3 bajos.** Sin PR (ninguna corrección de versión/SHA necesaria).

## Revisado sin novedad
- `pip-audit -r requirements.lock`: sin vulnerabilidades conocidas. Sin PR de Dependabot abiertos (los abiertos son de docs/otros).
- Acciones de `.github/workflows`: todas fijadas por SHA; `git ls-remote` confirma checkout v7.0.1, create-github-app-token v3.3.0, setup-python v7.1.0, trivy-action v0.36.0 y codex-action v1 (tag desreferenciado) = SHA fijado.
- CI main (47cd1ee): jobs `security` (pip-audit, gitleaks, semgrep) y `docker` (Trivy imagen+fs) en verde.
- Código 7 días (marketing #469, estado de cuenta por correo #445, CSP #462/#463): vistas de marketing solo superusuario+POST; webhook de correo con HMAC fail-closed, ±5 min, tope 15 MB, rate_limit; `/csp-report/` con rate_limit y sin query; `archivo_eml` en storage privado y en ARCHIVOS_PROTEGIDOS; sin `os.environ`/`config()` fuera de `variables.env`; sin SQL crudo ni `mark_safe` nuevo con datos de usuario.
- Railway (solo lectura): último deploy de `web` y `Programador` SUCCESS (47cd1ee). `ERP_R2_PRIVADO_OBLIGATORIO` y `CLOUDFLARE_R2_PRIVADO_BUCKET` existen en ambos servicios. HTTP 4xx/5xx: solo escaneo de bots (wp-login, stripe_secret_key.txt, etc.) con 404; sin picos 401/403/429 ni fuerza bruta en /admin/login/. Sin firmas inválidas de webhooks en deploy logs.
- QKT-Pages: `check_page_security.py` OK.

## Hallazgos
1. **BAJO · VERIFICADO** — CI de main en rojo: job `test`, `comunicacion/tests/test_llegadas.py:141` (`test_el_contexto_de_hoy_solo_aparece_si_viene_hoy`, `contexto_de_hoy` devolvió ''). Corrió a las 04:13 UTC (22:13 Mérida): probable dependencia de fecha UTC vs local. Aun en rojo se desplegó. Corrección: fijar la fecha con `localdate`/reloj inyectado en el test; riesgo nulo.
2. **BAJO · VERIFICADO** — La CSP pública bloquea `static.cloudflareinsights.com/beacon.min.js` en `quintakooxtanil.com/` (varios `/csp-report/` diarios, `script-src-elem`). Es la única violación vista; no es riesgo, pero Web Analytics no mide. Corrección: añadir ese host a `script-src` y `cloudflareinsights.com` ya está en connect-src, o apagar Web Analytics en Cloudflare. Es el único hallazgo de CSP; el portal/admin siguen en Report-Only (`ERP_CSP_*_ACTIVO` no definidas en Railway) sin violaciones registradas.
3. **BAJO · VERIFICADO** — `core_erp/views_csp.py:33-38` registra `effective-directive`/`blocked-uri` truncados pero sin quitar saltos de línea (inyección de líneas en logs por quien postee al endpoint público). Corrección: `.replace('\n',' ')`/`repr()`; sin riesgo.

## Notas
- No verificable: valor de `DJANGO_DEBUG` (conector devuelve valores redactados); la evidencia indirecta es el 500 de `/admin/comercial/preguntafrecuente/` (19:45 UTC, tracebacks en logs, no en respuesta) — ver abajo.
- Informativo (no seguridad): ese 500 es un `ValidationError` (`[COMPLETAR]`) no capturado al activar preguntas desde `list_editable`; 3 veces hoy.
- `anthropics/claude-code-action@v1` es un tag móvil de mayor versión; el SHA fijado (d70510b) ya no es el actual del tag. Esperado; sin acción.
- `ERP_ESTADOS_CORREO_SECRET` no está en `web`: el webhook responde 403 (fail-closed) hasta definirlo.
