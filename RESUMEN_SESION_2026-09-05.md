# Resumen de sesión — 2026-09-05

## A. Pasadía: aforo ampliado (21-30 personas)

- 20 personas incluidas en la tarifa base de Pasadía; 21-30 es "aforo ampliado"
  con $180 MXN/persona extra (incluye mobiliario, sin línea aparte).
- `comercial/models.py`: nuevo rol `PERSONA_EXTRA_PASADIA` en
  `Producto.ROL_COTIZADOR_CHOICES` (migración `0079`).
- `comercial/views_cotizador.py`: tope de personas 20→30 para Pasadía,
  línea automática `PERSONA_EXTRA_PASADIA` cuando `num_personas > 20`,
  `precio_persona_extra` expuesto en la API.
- `comercial/templates/cotizador/index.html`: slider hasta 30 para Pasadía.
- `comercial/services.py`: cláusula del contrato de Pasadía actualizada con
  el cargo de $180/persona y el máximo de 30.
- Tests nuevos: `comercial/test_cotizador_lineas.py::PersonaExtraPasadiaTest`
  (20/25/30/31 personas, total exhibido = real, IVA incluido).
- **Bug de PR #269 encontrado y corregido de paso** (durante un merge
  conflict): el slider de Pasadía dejaba `max=30` pero clamped el valor a
  20 (copy-paste del clamp de Hospedaje) — corregido a `v > 30 ? 30 : ...`.

## B. Openpay: análisis de pagos duplicados (cliente COT-129)

- Analizados 5 payloads reales de Openpay de un cliente que generó varias
  referencias de pago duplicadas.
- CLABE del payload: normal, no es señal de alerta.
- Los intentos fallidos fueron responsabilidad del banco/cliente, no del
  sistema — pero se detectó un hueco real de UX: cada visita generaba una
  referencia SPEI/efectivo nueva sin invalidar ni mostrar las anteriores.
- **Fix**: `comercial/services_openpay.py`
  - `datos_referencia_pendiente(transaccion)`: reconstruye el dict de una
    `OpenpayTransaccion` ya guardada.
  - `transacciones_pendientes(cotizacion)`: devuelve las referencias
    vigentes y no pagadas (más reciente por método), validando `due_date`
    contra `timezone.localtime()`.
- `comercial/views_portal.py`: pasa `pagos_pendientes_openpay` al contexto.
- `templates/portal/evento.html`:
  - Tarjeta nueva "Ya tienes un pago en proceso" que renderiza las
    referencias pendientes.
  - Botones de monto rápido (mínimo / saldo total).
  - Recordatorio de que el enlace del portal es reutilizable.
- Tests nuevos: `comercial/test_openpay.py::TransaccionesPendientesTest`.

## C. Verificación con Playwright

- Se verificó el flujo del portal en navegador real (no solo tests).
- Workaround necesario: la MCP tool de Playwright no podía lanzar Chromium
  como root sin `--no-sandbox`; se usó el paquete Node `playwright`
  directo con el binario en `/opt/pw-browsers/chromium` y
  `args: ['--no-sandbox']`.
- Se interceptó `js.openpay.mx` (dominio inalcanzable en sandbox) con
  `page.route()` y un `window.OpenPay` falso para que el script inline no
  abortara.

## D. Bug real encontrado a partir de una pregunta del usuario

- El usuario preguntó por un fragmento de texto suelto al final de una
  captura de pantalla y por un código de barras ausente.
- **Código de barras ausente**: normal, limitación de red del sandbox
  (no llega a la API real), no es un bug.
- **Texto suelto**: sí era un bug real de producción. Django **no soporta
  comentarios `{# #}` multilínea** — un comentario de varias líneas se
  imprime literal en el HTML renderizado en vez de omitirse.
- Auditoría con regex en todo el repo encontró 3 ocurrencias:
  - `templates/portal/evento.html`
  - `templates/errores/_base.html` (página de error 400/403/404/500,
    pública, se sirve en cada error del sitio)
  - `airbnb/templates/admin/airbnb/importar_csv.html` (solo admin)
- Fix: convertir los tres a `{% comment %}...{% endcomment %}`, que sí
  soporta multilínea.

## E-H. Configuración de Claude Code Routines

- **Tarea E**: el usuario pidió fusionar un bloque `permissions` nuevo en
  `.claude/settings.json` (que ya tenía `hooks`). Se corrigió
  `defaultMode` de `"plan"` a `"acceptEdits"` (un Routine desatendido se
  quedaría colgado esperando una aprobación que nunca llega). Cambio
  quedó sin commitear a la espera de confirmación explícita (nunca
  llegó — ver más abajo).
- **Tarea F**: el usuario pidió colocar 3 archivos exactos
  (`CLAUDE.md`, `.claude/settings.json`, `docs/agente_instrucciones_erp.md`)
  sin modificar su contenido, en una rama nueva
  (`setup/claude-routines-config`) vía PR (**PR #274**).
  - Efecto colateral no detectado a tiempo: el `settings.json` dado por el
    usuario solo traía `permissions`, así que el `Write` reemplazó el
    archivo completo y **borró silenciosamente el bloque `hooks`**
    existente.
  - El `CLAUDE.md` dado por el usuario también reemplazó por completo la
    "Memoria" técnica acumulada (~2150 líneas).
- **Tarea G**: el usuario pidió explícitamente "fusiona, no perder la
  memoria técnica" + "que no haya errores y que funcionen las routines".
  - `CLAUDE.md`: se restauró la versión original completa (2099 líneas) y
    se insertaron por edición quirúrgica las secciones nuevas (Contexto
    del negocio, Estándares de código, Reglas para Routines, Watchlist),
    sin perder ninguna entrada de Memoria. Resultado: 2178 líneas, 64
    entradas de Memoria confirmadas intactas.
  - `settings.json`: se corrigieron rutas inventadas (`payments/`,
    `accounting/services.py`, que no existen en este repo) a las reales
    (`comercial/views_openpay.py`, `comercial/services_openpay.py`,
    `contabilidad/services.py`), y `defaultMode` de vuelta a
    `"acceptEdits"`.
  - `docs/agente_instrucciones_erp.md`: mismas correcciones de rutas.
- **Tarea H (PR #275)**: se detectó que el bloque `hooks` seguía sin
  restaurarse tras el PR #274. Se creó un PR de fix que lo restauró
  byte-a-byte desde el commit anterior a #274, mergeado junto con el
  bloque `permissions` corregido.
- **Estado final en `main`**: `.claude/settings.json` tiene ambos bloques
  completos (`hooks` restaurado + `permissions` con rutas reales y
  `acceptEdits`); `CLAUDE.md` conserva toda la Memoria original más las
  secciones nuevas de Routines.
- **PRs #274 y #275 mergeados** (a pedido explícito del usuario, "mergea").
- **Pendiente sin resolver**: el cambio local no commiteado de la Tarea E
  en la rama `claude/quinta-activity-organization-module-wrvuok` nunca se
  confirmó si debía commitearse — probablemente ya es redundante frente al
  estado actual de `main` tras los PRs #274/#275, pero no se verificó con
  un diff explícito.

## Lección de proceso (autocrítica documentada)

Al reemplazar un archivo de configuración completo bajo instrucción de "no
modificar el contenido", falta diferenciar entre "no alterar lo que se
entrega" y "no revisar qué se pierde en el camino" — un diff contra el
archivo existente antes de escribir habría detectado la pérdida de `hooks`
antes de mergear, no después.
