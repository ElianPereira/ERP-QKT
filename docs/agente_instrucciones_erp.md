# Sistema de Mejora Continua e Inteligencia de Negocio — ERP-QKT

## 0. Contexto del Proyecto
- **Negocio:** Quinta Ko'ox Tanil (QKT) — eventos, pasadía, hospedaje corto directo (Ka'an, Otoch). Unidad de negocio: QUINTA. Airbnb y Honey Sea House se retiraron del portafolio y del ERP (Issue #311).
- **Stack:** Django + PostgreSQL, Railway (`erp.quintakooxtanil.com`, `quintakooxtanil.com`), Cloudflare Pages (`quintakooxtanil.com`), Cloudflare R2 (storage de archivos), Openpay/BBVA, WhatsApp Cloud API, GitHub.
- **Apps relevantes:** ERP interno, portal cliente, landing pública.
- **Cuentas bancarias:** BBVA Maestra PYME → QUINTA.
- **Estándares de código obligatorios:**
  - `Decimal` + `ROUND_HALF_UP` en todo cálculo monetario. `float` en ruta de dinero = bug crítico, no sugerencia.
  - Modelos de auditoría inmutables: soft-deactivation, nunca `DELETE` físico.
  - `created_by` / `updated_by` / `created_at` / `updated_at` en toda operación sensible (ventas, cancelaciones, ajustes de precio/inventario).
  - IVA: conversión única sobre el subtotal, nunca por línea (tolerancia SAT PAC ±0.01/concepto — cuidado con `calcular_desglose_proporcional`).
  - Precios visibles al consumidor siempre IVA-incluido (LFPC Art. 7 BIS).

## 1. Reglas de Eficiencia de Tokens (obligatorio)
- Prohibido leer la BD completa o volcar archivos gigantes a consola.
- `grep` / `find` / AST antes de abrir archivos completos.
- Revisiones de código: solo `git diff` / `git log --stat` reciente, no el repo entero.
- Agente Técnico y Agente Operativo/Contable **nunca** corren en el mismo hilo.
- Si una tarea exige leer >3 archivos completos, pedir confirmación antes de seguir.

## 2. Agente Técnico — Código, Rendimiento, Seguridad
**Frecuencia:** semanal / por PR.

1. **DB:** detectar N+1 en vistas de finanzas, cotizador y reservas; proponer `select_related`/`prefetch_related` e índices concretos.
2. **Seguridad:** permisos por vista/endpoint, sanitización de inputs, XSS/CSRF/IDOR, aislamiento de datos entre unidades de negocio (hoy solo QUINTA) y sus cuentas bancarias.
3. **Cálculos monetarios:** auditar `Decimal`/`ROUND_HALF_UP` en todo el flujo de dinero; cualquier `float` se reporta como crítico.
4. **Deuda técnica e infraestructura:** dependencias vulnerables, pipeline de estáticos, config Railway/Cloudflare.
5. **Testing:** exigir cobertura en pagos (Openpay), cotizador, descuentos, conciliación bancaria — incluir casos límite de redondeo.

**Prompt disparador:**
> Ver el texto completo en "Routine 1 — QKT Rutina Técnica" (abajo). Clave: ventana de 7 días, y solo abre PR si hay un hallazgo de impacto ALTO; los de impacto BAJO se reportan sin tocar código.

## 3. Agente Operativo, Empresarial y Contable
**Frecuencia:** quincenal / mensual.

1. **Cumplimiento fiscal/legal:** precios IVA-incluido, estado PROFECO (NOM-174), ISH del hospedaje directo (`TASA_ISH`), vigencia de documentos legales (privacidad/T&C) y consentimientos.
2. **Flujo de caja y cobranza:** modelos de cuentas por cobrar, automatización de recordatorios de pago, detectar transacciones mal clasificadas o cuenta bancaria incorrecta.
3. **Pricing y rentabilidad:** validar que descuentos, aforo ampliado y add-ons no erosionen margen; señalar inconsistencias en mezcla de negocio.
4. **Logística/operación:** blindar validación de fechas (check-in 14:00 / check-out 10:00, ventanas de limpieza) para evitar sobreventas entre eventos y hospedaje.
5. **KPIs sugeridos:** margen bruto por unidad de negocio, DSO, ocupación pasadía/hospedaje, ticket promedio, ventas mes vs. cotizaciones EJECUTADA/CERRADA.

**Prompt disparador:**
> Ver el texto completo en "Routine 2 — QKT Rutina Operativa/Contable" (abajo). Clave: hasta 3 hallazgos (no una cuota), cada uno con archivo:línea y marcado VERIFICADO o A CONFIRMAR, sin repetir lo ya resuelto en la Watchlist/Memoria, y reporte en `docs/rutinas/`.

## 4. God Mode + Human-in-the-Loop

**Permitido sin aprobación (solo análisis/bajo riesgo):**
- Lectura total del repo y esquema de BD (no datos vivos sensibles), logs no productivos.
- Tests, linters, `makemigrations --check`, análisis estático.
- Crear ramas/PRs con fixes de performance, tests o lint.

**Requiere aprobación explícita antes de tocar código:**
- Lógica de precios, impuestos (IVA/ISH), descuentos o pagos (Openpay).
- Migraciones de datos o cambios de schema.
- Documentos legales o modelos de consentimiento/auditoría.
- `DELETE` físico o modificación de registros inmutables.

**Nunca autorizado:**
- Merge a `main`/`master`.
- Migraciones o deploys en producción.
- Exponer datos sensibles (financieros, personales) en logs o respuestas.

Toda sugerencia estratégica se entrega como documento en `/docs/` — nunca se implementa directo.

## 5. Watchlist Activa (revisar en cada rutina)
- [x] ~~Horario pasadía hardcodeado "10am–7pm"~~ — verificado en sesión: ya no existe en el código actual (`comercial/views_cotizador.py` usa 11:00 a.m.–7:00 p.m.). Ver detalle en `CLAUDE.md`.
- [ ] Precios sin IVA incluido en cualquier vista nueva.
- [ ] Régimen fiscal (RESICO vs. arrendamiento) sin confirmar → no tocar factor de retención 1.1475.
- [x] ~~Migración Cloudinary → DigitalOcean Spaces~~ — obsoleto: el storage ya es Cloudflare R2.
- [ ] Pixel de Meta no instalado (campañas en Traffic, no Conversions).
- [ ] Registro PROFECO NOM-174 pendiente.
- [ ] Definir `TASA_ISH` en Railway (ISH del hospedaje directo).
- [ ] Módulo de depósito en garantía ausente.

## 6. Ejecución y Configuración (Claude Code Routines)

Estas tres rutinas se implementan como **Routines** de Claude Code (`claude.ai/code/routines`), no como sesiones manuales ni scripts locales. Un Routine corre en la nube de Anthropic, con sesión nueva cada vez (sin memoria de corridas anteriores) y **sin pausas de aprobación** — por eso `CLAUDE.md` y `.claude/settings.json` son el único control real, no una sugerencia.

**Archivos a colocar en la raíz del repo (versionar en git):**
- `CLAUDE.md` — contexto del proyecto, estándares y watchlist. Se lee automático en cada corrida.
- `.claude/settings.json` — copiar `settings.json`. Bloquea `curl`/`wget`/`migrate`/push directo a `main` o `master`/force-push, y edición en `comercial/views_openpay.py`, `comercial/services_openpay.py` (pagos), `legal/` y `contabilidad/services.py` (rutas reales de este repo — no existen carpetas `payments/` ni `accounting/`). Permite push de ramas nuevas para que el Routine pueda abrir su Pull Request.

**Routine 1 — QKT Rutina Técnica**
- Modelo: Sonnet 5
- Repositorio: ERP-QKT
- Trigger: Scheduled, semanal
- Instructions:
```
Ejecuta la Rutina Técnica sobre ERP-QKT: revisa el git diff de los commits de main de los últimos 7 días.
Busca solo (1) N+1 o queries ineficientes, (2) float en cálculos monetarios, (3) deuda técnica crítica
(bug latente, error silenciado en una ruta de dinero, código muerto que confunde).

Criterio de impacto — antes de reportar un hallazgo, estima su costo real con el volumen del negocio
(decenas de cotizaciones y ~60 movimientos bancarios al mes). Clasifícalo:
- ALTO: float en dinero; query que crece por fila en una vista que carga el cliente o el staff en cada
  visita (listas del admin, portal, cotizador, APIs públicas) con más de 20 queries extra por request;
  bug que produce un resultado incorrecto.
- BAJO: todo lo demás (procesos manuales o mensuales, ahorro de milisegundos, micro-optimizaciones,
  cachés de regex —el módulo re de Python ya cachea—).

Solo si hay al menos un hallazgo ALTO con corrección clara y de bajo riesgo: crea la rama
opt/mejora-<fecha>, aplica el fix mínimo con un test que falle antes y pase después (para N+1, usa
assertNumQueries), corre `python manage.py test <app>` y `ruff check .`, y abre un Pull Request en
borrador contra main. Los hallazgos BAJO nunca abren PR: solo se listan en el reporte.

Si no hay hallazgos ALTO, no crees rama ni PR: responde "Sin hallazgos de impacto" más la lista breve
de los BAJO (una línea cada uno, con archivo:línea).

Nunca hagas merge ni edites comercial/views_openpay.py, comercial/services_openpay.py, legal/ o
contabilidad/services.py, ni migraciones. Sin teoría, solo hallazgos y código.
```

**Routine 2 — QKT Rutina Operativa/Contable**
- Modelo: Opus 5
- Repositorio: ERP-QKT
- Trigger: Scheduled, mensual (o quincenal)
- Instructions:
```
Ejecuta la Rutina Operativa/Contable sobre ERP-QKT. Solo análisis: no toques código ni datos vivos.

Antes de analizar, lee en CLAUDE.md la Watchlist y la Memoria, y los reportes previos en
docs/rutinas/. Todo lo que ya esté resuelto, decidido por el propietario o reportado antes no se
vuelve a reportar, salvo que el código haya cambiado y lo contradiga.

Analiza la estructura (campos, métodos, signals, services) de: Cotizacion/ItemCotizacion (evento,
pasadía y hospedaje directo), Pago/cobranza, DepositoGarantia, descuentos, facturacion y contabilidad
(pólizas, conciliación). Busca:
- Riesgos fiscales/legales: IVA incluido al consumidor, ISH del hospedaje directo, retenciones,
  CFDI, PROFECO/contratos, vigencia de documentos legales y consentimientos.
- Oportunidades de negocio viables en software: margen, descuentos, cobranza, KPIs faltantes.

Reglas de evidencia:
- Cada hallazgo cita archivo:línea y explica el mecanismo concreto en el código.
- Clasifícalo como VERIFICADO (el código lo demuestra) o A CONFIRMAR (depende de una norma o
  criterio del contador/abogado); en los A CONFIRMAR, formula la pregunta exacta para ellos.
- Prohibido simular con importes inventados o citar artículos de ley sin estar seguro.

Entrega hasta 3 riesgos y hasta 3 recomendaciones, ordenados por impacto en dinero o riesgo legal.
Si hay menos, entrega menos: mejor 1 hallazgo real que 3 de relleno. Si no hay nada nuevo, escribe
"Sin hallazgos nuevos" y no abras PR.

Formato: un solo archivo docs/rutinas/operativa_<AAAA-MM-DD>.md, máximo 80 líneas, en español.
Ábrelo como Pull Request en borrador contra main. Nunca hagas merge.
```

**Routine 3 — QKT Rutina de Seguridad**
- Modelo: Sonnet 5.5
- Repositorios: ERP-QKT y QKT-Pages
- Conector: Railway (solo lectura: estado de deploys, logs y nombres/booleanos de variables)
- Trigger: Scheduled, semanal (miércoles 02:50 Mérida), notificación push
- Origen: Issue #460 (auditoría del 2026-10-30)
- Instructions:
```
Ejecuta la Rutina de Seguridad sobre ERP-QKT y QKT-Pages. Solo análisis y, como máximo, un PR en
borrador. Nunca hagas merge, nunca cambies variables ni servicios de Railway, nunca escribas en la base
de producción y nunca imprimas el valor de un secreto (solo nombres, o True/False/vacío/no vacío).

Antes de analizar, lee CLAUDE.md (Watchlist y Memoria), docs/security/ y los reportes previos
docs/rutinas/seguridad_*.md. No vuelvas a reportar lo ya resuelto, decidido por el propietario o
reportado antes, salvo que haya empeorado o el código lo contradiga.

Revisa, en este orden:
1. Avisos nuevos de la semana: busca en la web vulnerabilidades o incidentes de los últimos 7 días que
   afecten a lo que usamos (Django, Python, Pillow, WeasyPrint, lxml, cryptography, pdfminer/pypdfium2,
   pandas/openpyxl, gunicorn, whitenoise, django-otp, django-storages/boto3, anthropic SDK, imagen
   python:3.12-slim-trixie, GitHub Actions usadas en .github/workflows, Railway, Cloudflare R2/Pages,
   Meta WhatsApp Cloud API, Openpay, Brevo). Cruza cada aviso con la versión real de requirements.lock
   o del Dockerfile: si no nos afecta, no lo reportes.
2. Dependencias: corre `pip-audit -r requirements.lock`. Lista los PR de Dependabot abiertos y su CI.
3. CI de main: revisa la última corrida de ci.yml en main (jobs test, security, docker). Cualquier
   falla o aviso de Trivy, pip-audit, gitleaks o semgrep es hallazgo.
4. Cadena de suministro: toda acción de .github/workflows debe ir fijada por SHA. Con
   `git ls-remote` confirma que el SHA corresponde al tag del comentario; un tag que cambió de SHA sin
   cambio de versión es CRÍTICO hasta demostrar lo contrario.
5. Código de los últimos 7 días (`git log --since=7.days` y su diff en main): vistas nuevas sin
   permiso, sin rate_limit si son públicas, o con csrf_exempt; mark_safe/|safe/format_html con datos
   del usuario; SQL crudo; FileField nuevo con datos personales o fiscales sin storage privado (y que no
   esté en ARCHIVOS_PROTEGIDOS); validación de archivos por content_type en vez de firma; secretos o
   lecturas de entorno fuera de core_erp/variables.env(); logs o respuestas con datos personales,
   tokens o str(e); cambios en settings.py que debiliten cookies, HSTS, CSP, ALLOWED_HOSTS o
   CSRF_TRUSTED_ORIGINS; herramientas nuevas del agente Kooxi que lean o muevan datos sin candado de
   teléfono/HMAC.
6. Producción (Railway, solo lectura): estado del último deploy de web y Programador;
   DJANGO_DEBUG es False; ERP_R2_PRIVADO_OBLIGATORIO es True y CLOUDFLARE_R2_PRIVADO_BUCKET no está
   vacío en ambos servicios; en los logs de 7 días, picos de 401/403/429 (fuerza bruta en
   /admin/login/), firmas inválidas de webhooks de WhatsApp/Openpay, errores 500 repetidos y
   violaciones recibidas en /csp-report/ agrupadas por directiva y dominio.
7. QKT-Pages: corre `python3 .github/scripts/check_page_security.py`.

Severidad: CRÍTICO (explotable hoy, o expone datos de clientes, dinero o credenciales), ALTO, MEDIO,
BAJO. Cada hallazgo lleva archivo:línea (o servicio/variable), el mecanismo concreto, si es
VERIFICADO o A CONFIRMAR, y la corrección propuesta con qué podría romper.

Corrección automática: solo si hay un hallazgo ALTO o CRÍTICO cuya corrección es subir una versión en
requirements.lock (regenerado con pip-compile) o fijar un SHA de una Action. Crea la rama
sec/<AAAA-MM-DD>, corre la suite completa de CLAUDE.md y `ruff check .`, y abre un PR en borrador.
Todo lo demás (código, settings, Docker, Railway, Cloudflare) va solo al reporte. Nunca edites
comercial/views_openpay.py, comercial/services_openpay.py, legal/, contabilidad/services.py ni
migraciones.

Entrega: docs/rutinas/seguridad_<AAAA-MM-DD>.md, máximo 80 líneas, en español, empezando por una línea
de resumen («Sin hallazgos nuevos» o «N hallazgos: X críticos…»). Si hay algún CRÍTICO, la primera línea
empieza con «CRÍTICO:» y el título del PR también. Si no hay nada nuevo, no abras PR: responde solo
«Sin hallazgos nuevos» con la lista de lo revisado.
```

Las tres rutinas entregan vía Pull Request — tú apruebas el merge a `main`. Ese PR es tu punto de control humano, ya que el Routine en sí no pausa a preguntar.
