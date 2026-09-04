# Sistema de Mejora Continua e Inteligencia de Negocio — ERP-QKT

## 0. Contexto del Proyecto
- **Negocio:** Quinta Ko'ox Tanil (QKT) — eventos, pasadía, hospedaje corto (Ka'an, Otoch, Honey Sea House). Unidades de negocio: QUINTA, PASADÍA, AIRBNB.
- **Stack:** Django + PostgreSQL, Railway (`erp.quintakooxtanil.com`, `clientes.quintakooxtanil.com`), Cloudflare Pages (`quintakooxtanil.com`), Cloudinary (en evaluación → candidato DigitalOcean Spaces), Openpay/BBVA, WhatsApp Cloud API, GitHub.
- **Apps relevantes:** ERP interno, portal cliente, landing pública.
- **Cuentas bancarias:** BBVA Maestra PYME → QUINTA / BBVA Libretón Básico → AIRBNB (corte día 14).
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
2. **Seguridad:** permisos por vista/endpoint, sanitización de inputs, XSS/CSRF/IDOR, aislamiento de datos entre unidades de negocio (QUINTA/PASADÍA/AIRBNB) y sus cuentas bancarias.
3. **Cálculos monetarios:** auditar `Decimal`/`ROUND_HALF_UP` en todo el flujo de dinero; cualquier `float` se reporta como crítico.
4. **Deuda técnica e infraestructura:** dependencias vulnerables, pipeline de estáticos, config Railway/Cloudflare.
5. **Testing:** exigir cobertura en pagos (Openpay), cotizador, descuentos, conciliación bancaria — incluir casos límite de redondeo.

**Prompt disparador:**
> "Inicia Rutina Técnica. Revisa `git diff` de los últimos 3 commits. Reporta solo: (1) N+1 o queries ineficientes, (2) cualquier `float` en cálculos monetarios, (3) deuda técnica crítica. Si hay corrección clara y de bajo riesgo, crea rama `opt/mejora-[fecha]` con el fix y tests. Sin teoría, solo hallazgos y código."

## 3. Agente Operativo, Empresarial y Contable
**Frecuencia:** quincenal / mensual.

1. **Cumplimiento fiscal/legal:** precios IVA-incluido, estado PROFECO (NOM-174), ISH en unidades Airbnb, vigencia de documentos legales (privacidad/T&C) y consentimientos.
2. **Flujo de caja y cobranza:** modelos de cuentas por cobrar, automatización de recordatorios de pago, detectar transacciones mal clasificadas entre QUINTA/AIRBNB o cuenta bancaria incorrecta.
3. **Pricing y rentabilidad:** validar que descuentos, aforo ampliado y add-ons no erosionen margen; señalar inconsistencias en mezcla de negocio.
4. **Logística/operación:** blindar validación de fechas (check-in 13:00 / check-out 10:00, ventanas de limpieza) para evitar sobreventas entre eventos y hospedaje.
5. **KPIs sugeridos:** margen bruto por unidad de negocio, DSO, ocupación pasadía/hospedaje, ticket promedio, ventas mes vs. cotizaciones EJECUTADA/CERRADA.

**Prompt disparador:**
> "Inicia Rutina Operativa/Contable. Analiza solo estructura (campos y métodos, NO datos vivos) de modelos de Cotizaciones, Cobranza, Reservas y Contabilidad. Entrega reporte breve en markdown: 3 riesgos fiscales/legales detectados + 3 recomendaciones de negocio viables en software. Guarda en `/docs/`."

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
- [ ] Horario pasadía hardcodeado "10am–7pm" → corregir a "11am–7pm".
- [ ] Precios sin IVA incluido en cualquier vista nueva.
- [ ] Régimen fiscal (RESICO vs. arrendamiento) sin confirmar → no tocar factor de retención 1.1475.
- [ ] Migración Cloudinary → DigitalOcean Spaces (pendiente).
- [ ] Pixel de Meta no instalado (campañas en Traffic, no Conversions).
- [ ] Registro PROFECO NOM-174 pendiente.
- [ ] ISH Airbnb sin resolver.
- [ ] Módulo de depósito en garantía ausente.

## 6. Ejecución y Configuración (Claude Code Routines)

Estas dos rutinas se implementan como **Routines** de Claude Code (`claude.ai/code/routines`), no como sesiones manuales ni scripts locales. Un Routine corre en la nube de Anthropic, con sesión nueva cada vez (sin memoria de corridas anteriores) y **sin pausas de aprobación** — por eso `CLAUDE.md` y `.claude/settings.json` son el único control real, no una sugerencia.

**Archivos a colocar en la raíz del repo (versionar en git):**
- `CLAUDE.md` — contexto del proyecto, estándares y watchlist. Se lee automático en cada corrida.
- `.claude/settings.json` — copiar `settings.json`. Bloquea `curl`/`wget`/`migrate`/push directo a `main` o `master`/force-push, y edición en `payments/`, `legal/`, `accounting/services.py`. Permite push de ramas nuevas para que el Routine pueda abrir su Pull Request.

**Routine 1 — QKT Rutina Técnica**
- Modelo: Sonnet 5
- Repositorio: ERP-QKT
- Trigger: Scheduled, semanal
- Instructions:
```
Ejecuta la Rutina Técnica: revisa el git diff de los últimos 3 commits. Reporta solo (1) N+1 o
queries ineficientes, (2) cualquier float en cálculos monetarios, (3) deuda técnica crítica. Si hay
corrección clara y de bajo riesgo, crea rama opt/mejora-<fecha> con el fix y tests, y abre un Pull
Request contra main. Nunca hagas merge directo ni edites payments/, legal/ o accounting/services.py.
Sin teoría, solo hallazgos y código.
```

**Routine 2 — QKT Rutina Operativa/Contable**
- Modelo: Opus 5
- Repositorio: ERP-QKT
- Trigger: Scheduled, mensual (o quincenal)
- Instructions:
```
Ejecuta la Rutina Operativa/Contable: analiza solo estructura (campos y métodos, NO datos vivos) de
los modelos de Cotizaciones, Cobranza, Reservas y Contabilidad. Detecta riesgos fiscales/legales (IVA,
ISH, PROFECO, vigencia de documentos legales) y oportunidades de negocio (margen, descuentos, KPIs
faltantes). Esto es solo análisis, no toques código. Entrega un reporte breve en markdown con 3
riesgos + 3 recomendaciones, guárdalo en /docs/ y ábrelo como Pull Request para revisión.
```

Ambas rutinas entregan vía Pull Request — tú apruebas el merge a `main`. Ese PR es tu punto de control humano, ya que el Routine en sí no pausa a preguntar.
