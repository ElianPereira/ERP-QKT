# Rutina de Seguridad — 2026-11-11
**2 hallazgos: 0 críticos, 0 altos, 1 medio (a confirmar), 1 bajo.** Primer reporte de seguridad en `docs/rutinas/`.

## Hallazgos
1. **MEDIO (A CONFIRMAR) — Django 6.1.2 sin el parche del 3/nov/2026** (`requirements.lock`: `django==6.1.2`).
   Django publicó 6.1.3 / 6.0.10 / 5.2.19 con seis CVE (CVE-2026-108147, -111211, -111362, -113593, -113733, -114582):
   SSRF en GIS `SpatialReference` (no aplica: no usamos GIS), agotamiento de memoria, log injection, colisión de caché y
   divulgación de hash de contraseña a usuarios admin «solo vista» con modelos de usuario personalizados. Fuente: búsqueda web
   (oss-security 2026/11/03); no pude abrir djangoproject.com (host fuera de la red permitida), así que severidad y
   aplicabilidad exactas no están verificadas. `pip-audit` aún no lo marca (la base de datos va atrasada).
   Corrección: `pip-compile` con `django>=6.1.3`, suite completa + `ruff check .`. Riesgo: bajo (parche).
   No abrí PR automático porque no pude confirmar que sea ALTO/CRÍTICO; si Dirección lo pide, es un cambio de una línea.
2. **BAJO — `anthropics/claude-code-action` fijada a un SHA anterior al tag flotante `v1`** (`.github/workflows/ai-*.yml`):
   SHA fijado `d70510b…`; `v1` hoy apunta a `4fa93a8…`. Es un tag mayor flotante que se mueve por diseño, no manipulación
   (el resto de SHA coincide con su tag). Corrección: actualizar vía Dependabot. Riesgo: ninguno.

## Revisado sin novedades
- `pip-audit -r requirements.lock`: sin vulnerabilidades conocidas. Resto de versiones (Pillow 13.0.0, WeasyPrint 70.0,
  lxml 6.1.1, cryptography 50.0.0, gunicorn 26.0.0, whitenoise 6.12.0, django-otp 1.7.0) sin avisos encontrados.
- Cadena de suministro: todas las Actions fijadas por SHA; `checkout v7.0.1`, `setup-python v7.1.0`,
  `create-github-app-token v3.3.0`, `trivy-action v0.36.0` y `codex-action v1` coinciden con `git ls-remote`.
- CI de `main`: última corrida (#1818, be008a9) en verde; 3 corridas previas también.
- Código de 7 días (17 commits): `views_seo.py` (robots/sitemap públicos, solo lectura), CSP ampliada al beacon de
  Cloudflare (`static.cloudflareinsights.com`, `cloudflareinsights.com`; no debilita cookies/HSTS/ALLOWED_HOSTS),
  `services_abonos.py` (transferencias por folio; cambio ya revisado por el propietario), herramientas Kooxi con
  candado por conversación/teléfono. Sin `csrf_exempt` ni `mark_safe` nuevos con datos de usuario.
- Railway (solo lectura): deploys de `web` y `Programador` en SUCCESS con be008a9. Variables presentes por nombre:
  `DJANGO_DEBUG`, `ERP_R2_PRIVADO_OBLIGATORIO`, `CLOUDFLARE_R2_PRIVADO_BUCKET` en `web`.
  **No verificado**: el conector devuelve valores redactados, así que no pude confirmar `DJANGO_DEBUG=False` ni
  `ERP_R2_PRIVADO_OBLIGATORIO=True`, ni leer las variables de `Programador`. Logs HTTP del 4–11/nov: solo sondeos
  a WordPress/Joomla (404), sin ráfagas de 401/403/429 en la muestra; el filtro por estado no se aplicó, la muestra
  no es exhaustiva. `ERP_CSP_*_SOLO_REPORTE` siguen presentes (CSP en Report-Only, ya conocido).
- QKT-Pages: `check_page_security.py` OK.
