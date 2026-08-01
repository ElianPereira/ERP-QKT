# PROJECT_CONTEXT — ERP-QKT

Resumen de contexto para trabajar con este repo sin tener que cargar todo el
árbol de archivos. Generado para reducir el consumo de contexto en Claude
Projects — ver `.claudeignore` para lo que se excluye de lectura.

## Qué es

ERP interno de **Quinta Ko'ox Tanil** (organización de eventos / renta de
espacio + hospedaje vía Airbnb), construido sobre **Django 6** con el admin
de Django (tema **Jazzmin**) como interfaz principal — no hay frontend
separado tipo SPA. Incluye además un cotizador público y un portal de
cliente, ambos servidos con templates Django normales.

- **Backend**: Django 6, PostgreSQL en producción (`db.sqlite3` solo local/dev).
- **Admin theme**: `django-jazzmin`, con un sidebar agrupado a medida (ver
  `comercial/templatetags/qkt_sidebar.py` + `templates/admin/base.html`) que
  anida modelos sueltos en carpetas sintéticas (ej. "Ventas", "Pagos") no
  soportadas nativamente por Jazzmin.
- **PDFs**: `weasyprint` (contratos, cotizaciones, planes de pago, reportes).
- **Storage de archivos**: Cloudinary (`django-cloudinary-storage`).
- **Email**: `django-anymail`.
- **Pagos en línea**: integración directa con la API REST de Openpay
  (tarjeta, efectivo/Paynet, SPEI) — sin SDK, requests HTTP directas
  (`comercial/services_openpay.py`).
- **Deploy**: Railway (`railway.json`, `Dockerfile`, `gunicorn`).
- **CI**: GitHub Actions (`.github/workflows/ci.yml`) — corre la suite de
  tests de Django en cada push/PR.

## Apps Django (`INSTALLED_APPS`)

| App | Responsabilidad |
|---|---|
| `comercial` | Núcleo del negocio: cotizaciones, clientes, productos/inventario, pagos, portal de cliente, cotizador público, landing, descuentos. La app más grande con diferencia. |
| `contabilidad` | Catálogo de cuentas, pólizas, movimientos contables, conciliación bancaria, estados de cuenta. |
| `airbnb` | Reservas, pagos y calendario del hospedaje en Airbnb; sincronización iCal. |
| `nomina` | Empleados y recibos de nómina; integración con Jibble (checador). |
| `facturacion` | Solicitudes de factura y configuración del contador. |
| `comunicacion` | Registro/envío de comunicaciones al cliente (emails transaccionales, recordatorios). |
| `reportes` | Módulo centralizado de reportes PDF/Excel que reutiliza datos de las demás apps (`reportes/services/*.py` por dominio). |
| `core_erp` | Proyecto Django: `settings.py`, `urls.py` raíz, rate limiting (`ratelimit.py`), auth config. |

## Modelos principales por app

### `comercial` (el dominio central)

- **Catálogo / inventario**: `Insumo`, `SubProducto`, `RecetaSubProducto`,
  `Producto`, `ComponenteProducto`/`ProductoComponente` (paquetes),
  `PlantillaBarra`, `Proveedor`, `Compra`, `MovimientoInventario`,
  `ConstanteSistema`.
- **Ventas**: `Cliente`, `Cotizacion` (máquina de estados — el modelo más
  grande del proyecto, ~400 líneas), `ItemCotizacion`, `ContratoServicio`,
  `Espacio`, `AsignacionEspacio`, `AsignacionPersonal`, `TipoEvento`,
  `Temporada`, `Descuento`/`DescuentoAplicado`.
- **Pagos**: `Pago` ("Pagos Aprobados" en el admin — ya reflejados en
  cuenta), `PlanPago`/`ParcialidadPago`, `RecordatorioPago`,
  `OpenpayTransaccion` (log de cada cargo/webhook de Openpay),
  `PortalCliente` (token de acceso del portal público).
- **Landing / cotizador web**: `ImagenLanding`, `TestimonioLanding`,
  `EspacioLanding`, `PreguntaFrecuente`.
- **Gastos**: `Gasto`.

### `contabilidad`

`CuentaContable`, `UnidadNegocio`, `CuentaBancaria`, `Poliza`,
`MovimientoContable`, `ConciliacionBancaria`, `ConfiguracionContable`,
`SaldoApertura`, `EstadoCuentaBancario`, `MovimientoEstadoCuenta`.
Las pólizas se generan automáticamente vía signals cuando se registran
`Pago`s, comisiones de Openpay, etc. (ver `contabilidad/signals.py`).

### `airbnb`

`AnuncioAirbnb`, `ReservaAirbnb`, `PagoAirbnb`, `ConflictoCalendario`.

### `nomina`

`Empleado`, `ReciboNomina`.

### `facturacion`

`ConfiguracionContador`, `SolicitudFactura`.

### `comunicacion`

`ComunicacionCliente` (historial de emails/notificaciones enviadas).

### `reportes`

`ReporteGenerado` (registro de reportes exportados; la lógica de cada
reporte vive en `reportes/services/{airbnb,comercial,contabilidad,facturacion}.py`).

## Rutas / arquitectura clave

Todo se enruta desde `core_erp/urls.py` (ver ese archivo para el listado
completo). Grupos principales:

- **`/admin/`** — Admin de Django + Jazzmin. `admin/` en sí está sobreescrito
  para mostrar un dashboard de KPIs (`ver_dashboard_kpis`) en vez del index
  default. Cada app registra sus modelos en su propio `admin.py`.
- **Portal del cliente (público, sin login)** — `/mi-evento/<token>/...`:
  acceso por token (`PortalCliente`), descarga de PDFs (cotización, plan de
  pagos, contrato) y checkout de pagos Openpay
  (`portal_procesar_pago_openpay`). Vistas en `comercial/views_portal.py`.
- **Cotizador público** — `/cotizar/...`: formulario de cotización
  autoservicio + APIs JSON de disponibilidad/productos/paquetes
  (`comercial/views_cotizador.py`).
- **Landing pública** — `/` (`comercial/views_portal.py::landing_publico`).
- **Webhook Openpay** — `POST /pagos/openpay/webhook/`, protegido con Basic
  Auth (no CSRF, es servidor-a-servidor). Confirma cargos asíncronos
  (efectivo/SPEI) que se iniciaron como `in_progress` en el checkout.
  Ver `comercial/views_openpay.py` + `comercial/services_openpay.py`.
- **Reportes** — `/admin/reportes/...` (`reportes/urls.py`, namespace
  `reportes`): selector + un endpoint por tipo de reporte (balanza, estado
  de resultados, CxC, ocupación Airbnb, facturas, etc.), todos exportan PDF.
- **Contabilidad** — `/admin/contabilidad/reportes/...` (namespace
  `contabilidad`): balanza y estado de resultados con filtros propios
  (fuera del módulo genérico de reportes).
- **Airbnb** — `/airbnb/...` (namespace `airbnb`): iCal público para
  sincronización de calendario + bloqueo manual de fechas. Vistas
  adicionales de Airbnb (calendario unificado, reportes de pago, reporte
  fiscal) están montadas directamente en `core_erp/urls.py`, no en el
  namespace.
- **Nómina / Facturación** — rutas puntuales montadas en `core_erp/urls.py`
  (carga de nómina, sync con Jibble, alta de solicitud de factura); ambas
  apps también se anidan como submenú de "Contabilidad" en el sidebar (ver
  `SUBMENU_PARENTS` en `qkt_sidebar.py`) aunque son apps Django separadas.

## Convenciones notables

- **Sidebar del admin agrupado a mano**: Jazzmin solo soporta un nivel
  (app → modelos). `comercial/templatetags/qkt_sidebar.py` +
  `templates/admin/base.html` agregan agrupación sintética de 2 niveles
  extra (`MODEL_SUBGROUPS`, con soporte para `children` anidados) para
  carpetas como "Ventas" (con "Clientes" anidado dentro) y "Pagos".
- **Pagos de Openpay siempre traducidos**: Openpay regresa `description` en
  inglés; nunca se muestra ese texto crudo al cliente — se traduce por
  `error_code` o se usa un mensaje genérico en español
  (`_mensaje_error_openpay` en `services_openpay.py`).
- **Idempotencia de pagos**: `OpenpayTransaccion.openpay_id` es único;
  tanto el checkout como el webhook usan `update_or_create` para tolerar
  reintentos sin duplicar cargos. Un candado corto en cache
  (`portal_procesar_pago_openpay`) además evita doble cobro por envíos
  casi simultáneos del mismo formulario.
- **Pólizas contables automáticas**: se generan vía signals al crear un
  `Pago` o al confirmar una comisión de Openpay — `contabilidad` nunca se
  toca a mano desde `comercial`.
- **Rate limiting simple** basado en cache de Django (`core_erp/ratelimit.py`,
  decorador `@rate_limit`), usado en endpoints públicos sensibles (checkout).
- **Tests**: cada app trae sus propios `test_*.py`/`tests.py` (no hay
  carpeta `tests/` centralizada); `comercial` es la que más suite tiene por
  ser el núcleo del negocio.

## Qué NO está en este resumen

- Detalle campo por campo de cada modelo (leer el `models.py` de la app
  correspondiente si se necesita).
- Migraciones (`*/migrations/`) — excluidas explícitamente en `.claudeignore`;
  no aportan contexto de arquitectura, solo historial de esquema. Para ver
  el estado actual de un modelo, leer su `models.py`.
- Contenido de templates HTML individuales.
- `graphify-out/` — reporte de análisis de dependencias generado
  automáticamente (ver `GRAPH_REPORT.md` ahí dentro si se necesita ese nivel
  de detalle); excluido de `.claudeignore` por tamaño.
