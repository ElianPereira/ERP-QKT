# Plantilla de WhatsApp — Solicitud de factura al contador

El envío automático de la solicitud de factura al contador (`facturacion/services.py`)
manda el PDF por WhatsApp como mensaje tipo `document` directo. Eso funciona mientras
el contador le haya escrito al negocio en las últimas 24 horas; fuera de esa ventana,
Meta lo rechaza. La única forma de que un mensaje **iniciado por el negocio** (que es
justo este caso: se manda solo, sin que el contador haya escrito primero) llegue de
forma confiable fuera de esa ventana es con una plantilla aprobada.

A diferencia de la guía pre-evento (que manda un enlace de descarga para evitar el
trámite más lento de una plantilla con documento), aquí el pedido explícito es mandar
el PDF adjunto — así que sí hace falta someter una plantilla tipo **documento**, que
tarda un poco más en aprobarse que una de solo texto.

## Qué someter en Meta Business Manager

1. Entra a **Meta Business Manager → WhatsApp Manager → Plantillas de mensajes →
   Crear plantilla**.
2. Categoría: **Utilidad** (`UTILITY`) — es una notificación operativa a un proveedor
   de servicios (el contador), no marketing. Las plantillas de utilidad se aprueban
   más rápido y no requieren opt-in de marketing.
3. Nombre de la plantilla: **`solicitud_factura`** (en minúsculas, sin espacios —
   así lo espera el código; si usas otro nombre, ajusta la variable de entorno
   `WA_TEMPLATE_SOLICITUD_FACTURA` para que coincida, no hace falta tocar código).
4. Idioma: **Español (MX)** — mismo que `WA_TEMPLATE_LANGUAGE` (por defecto `es_MX`).
5. Encabezado: tipo **Documento**. Cuando Meta pida un archivo de muestra para la
   revisión, sube cualquier PDF de una solicitud de factura real ya generada (puedes
   descargar una con el botón "PDF" de `/admin/facturacion/solicitudfactura/`).
6. Cuerpo del mensaje — usa **exactamente estas dos variables**, en este orden
   (el código ya manda `{{1}}` = folio y `{{2}}` = nombre del cliente):

   ```
   Nueva solicitud de factura {{1}} — {{2}}.

   Se adjunta el PDF con los datos fiscales y el monto a facturar.
   ```

7. Pie de página (opcional): `Quinta Ko'ox Tanil`.
8. Envía a revisión. Meta suele tardar unas horas hasta 1-2 días para plantillas de
   documento (más que las de solo texto).

## Cuando Meta la apruebe

En Railway (o tu `.env` local), define:

```
WA_TEMPLATE_SOLICITUD_FACTURA=solicitud_factura
```

No hace falta ningún cambio de código ni redeploy adicional — la variable ya está
leída en `core_erp/settings.py` y `facturacion/services.py` la usa automáticamente
en el siguiente envío. Antes de que esté aprobada, o si la dejas vacía, todo sigue
funcionando exactamente igual que hoy (mensaje `document` directo).

## Si Meta rechaza la plantilla

Los rechazos más comunes para plantillas de utilidad con documento:
- El cuerpo suena a marketing/promoción → reescribe en tono puramente informativo,
  sin llamados a la acción ni ofertas.
- El PDF de muestra tiene datos que parecen inventados/placeholder → sube un PDF real
  (anonimiza el monto/RFC si te preocupa, pero que la estructura sea la real).
- Variables sin contexto → el texto de arriba ya deja claro qué es cada `{{1}}`/`{{2}}`
  para el revisor.
