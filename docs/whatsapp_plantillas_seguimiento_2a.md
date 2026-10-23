# Plantillas de WhatsApp — Fase 2a (seguimiento en 3 pasos y visitas)

Todas son mensajes iniciados por el negocio, así que solo pueden ir por plantilla aprobada en Meta.
Sin plantilla configurada, ese paso **no se manda** (el ERP no falla).

Las tres de seguimiento son categoría **Marketing**, idioma **Español (MX)**, y comparten las mismas 5 variables
del cuerpo, en este orden: `{{1}}` nombre de pila · `{{2}}` folio (COT-007) · `{{3}}` fecha del evento (dd/mm/aaaa) ·
`{{4}}` total con IVA, sin signo de pesos · `{{5}}` enlace al portal. Pie: **«Responde BAJA si no quieres más mensajes»**.
Encabezado y botones vacíos. Solo se mandan a quien aceptó MARKETING y no se dio de baja (`services_bajas.dado_de_baja`).

Cuándo sale cada una (`python manage.py enviar_seguimientos`, a lo más un mensaje por cotización cada 20 h, nunca hacia atrás):

| Paso | Variable de Railway | Cuándo |
|---|---|---|
| Día 1 | `META_WA_PLANTILLA_SEGUIMIENTO_DIA1` | 1 día después de crear la cotización sin pago |
| Día 3 | `META_WA_PLANTILLA_SEGUIMIENTO` (la ya existente, ver `whatsapp_plantilla_seguimiento.md`) | `ERP_KOOXI_SEGUIMIENTO_DIAS` (3) días después |
| Fecha por ocuparse | `META_WA_PLANTILLA_SEGUIMIENTO_FECHA` | faltan ≤ 14 días para su fecha, **o** otra cotización de la misma fecha ya tiene pagos sin haber apartado la fecha (cotizaciones creadas hace 1 a 45 días) |

## 1. `seguimiento_dia1` → `META_WA_PLANTILLA_SEGUIMIENTO_DIA1`

```
Hola {{1}}, gracias por cotizar con Quinta Ko'ox Tanil. Tu cotización {{2}} para el {{3}} es de ${{4}} (IVA incluido) y puedes revisarla y apartar tu fecha aquí: {{5}}

¿Alguna duda? Contéstanos por aquí y Kooxi, nuestro asistente, te ayuda.
```

## 2. `seguimiento_cotizacion` → `META_WA_PLANTILLA_SEGUIMIENTO` (día 3)

Texto vigente en `docs/whatsapp_plantilla_seguimiento.md`; no cambia.

## 3. `seguimiento_fecha` → `META_WA_PLANTILLA_SEGUIMIENTO_FECHA`

```
Hola {{1}}, tu fecha del {{3}} en Quinta Ko'ox Tanil está por ocuparse. Tu cotización {{2}} (${{4}}, IVA incluido) sigue vigente, pero la fecha solo queda apartada con tu primer pago: {{5}}

Si necesitas otra fecha o tienes dudas, contéstanos por aquí.
```

## 4. `visita_aviso` → `META_WA_PLANTILLA_VISITA`

Confirmación, rechazo, cancelación y recordatorio de **visitas a la Quinta** cuando la ventana de 24 h ya se cerró.
Categoría **Utilidad** (responde a una solicitud del cliente). Una variable: `{{1}}` el texto completo del aviso
(el ERP lo manda aplanado, sin saltos de línea). Sin plantilla se intenta texto libre, que solo llega con la ventana abierta.

```
Quinta Ko'ox Tanil: {{1}}
```

## Activarlo en Railway

Definir las variables en `web` y en el servicio del Programador por referencia (`${{web.VARIABLE}}`); el comando
`enviar_recordatorios_visitas` ya está en la tabla del programador (diario 18:00). Probar antes con
`python manage.py enviar_seguimientos --dry-run`.
