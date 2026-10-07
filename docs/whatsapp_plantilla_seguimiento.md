# Plantilla de WhatsApp — Seguimiento de cotización sin pago

El cron `enviar_seguimientos` (Issue #366) le escribe **una sola vez** a quien
cotizó y no ha pagado, a los `WA_SEGUIMIENTO_DIAS` (3 por defecto) de creada la
cotización. Es un mensaje iniciado por el negocio, así que solo puede ir por
plantilla aprobada.

Solo se manda a clientes que **aceptaron la finalidad MARKETING** («Promociones y
disponibilidad de fechas») en el cotizador, a cotizaciones BORRADOR/COTIZADA sin
pagos, con fecha futura y todavía libre. Si el abogado confirma que el
seguimiento de la propia solicitud es finalidad necesaria, el filtro está en
`comunicacion/services_notificaciones.py::motivo_para_no_seguir`.

## Qué someter en Meta Business Manager

1. **WhatsApp Manager → Plantillas de mensajes → Crear plantilla**.
2. Categoría: **Marketing** (es una invitación a concretar una compra).
3. Nombre: **`seguimiento_cotizacion`** (si usas otro, ponlo en
   `WA_TEMPLATE_SEGUIMIENTO`).
4. Idioma: **Español (MX)**.
5. Cuerpo, con estas variables en este orden:
   - `{{1}}` nombre de pila
   - `{{2}}` folio (COT-007)
   - `{{3}}` fecha (dd/mm/aaaa)
   - `{{4}}` total con IVA, sin signo de pesos (12,500.00)
   - `{{5}}` enlace al portal del cliente

```
Hola {{1}}, tu cotización {{2}} para el {{3}} en Quinta Ko'ox Tanil sigue disponible por ${{4}} (IVA incluido). La fecha se aparta con tu primer pago desde tu portal: {{5}}

¿Tienes dudas? Contéstanos por aquí y Kooxi, nuestro asistente, te ayuda.
```

6. Pie sugerido (Meta lo pide en marketing): «Responde BAJA si no quieres más mensajes».

## Activarlo en Railway

- Servicio `web` y un **Cron Job diario** nuevo con
  `python manage.py enviar_seguimientos`, con las variables por referencia
  (`${{web.VARIABLE}}`): `WA_TEMPLATE_SEGUIMIENTO`, `WA_SEGUIMIENTO_DIAS`,
  `WA_CLOUD_API_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_TEMPLATE_LANGUAGE`, `PORTAL_URL`
  y las de base de datos.
- Prueba antes con `python manage.py enviar_seguimientos --dry-run`.
