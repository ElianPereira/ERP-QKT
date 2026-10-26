# Plantillas de WhatsApp — versión en bloques

Issue #450. El ERP ya manda en bloques (párrafos cortos, viñetas, enlace solo en
su renglón) todo lo que sale como texto libre. Las plantillas aprobadas en Meta
tienen el cuerpo fijo, así que **solo el propietario puede mejorarlas**: este
documento trae una versión en bloques del cuerpo de cada una.

Reglas para que se pueda volver a someter **sin tocar el código**:

- Cada versión conserva **las mismas variables y en el mismo orden** que manda el ERP
  (las de abajo se leen del código vigente). Ni se agregan, ni se quitan, ni se reordenan.
- Los saltos de línea van en el **cuerpo**; Meta no los admite dentro de una variable.
- Meta no acepta un cuerpo que empiece o termine con una variable: siempre lleva texto fijo antes y después.
- Idioma `es_MX`, mismo nombre de plantilla (o uno nuevo y se cambia la variable de Railway; el código no cambia).
- Mientras la nueva no esté aprobada, la vigente sigue funcionando igual.

## Al cliente

### Cotización → `META_WA_PLANTILLA_COTIZACION` (Utilidad)
Variables: `{{1}}` nombre de pila · `{{2}}` tipo de evento · `{{3}}` fecha del evento · `{{4}}` total con IVA, sin `$` · `{{5}}` enlace al portal.

```
Hola {{1}} 👋 Esta es tu cotización en Quinta Ko'ox Tanil:

• Tipo de evento: {{2}}
• Fecha del evento: {{3}}
• Total a pagar: ${{4}} (IVA incluido)

Revísala y apártala desde tu portal:
{{5}}

Si tienes dudas, respóndenos por aquí.
```

### Confirmación de pago → `META_WA_PLANTILLA_PAGO` (Utilidad)
Variables: `{{1}}` nombre de pila · `{{2}}` monto pagado · `{{3}}` fecha del pago · `{{4}}` saldo pendiente (si está liquidado, el ERP manda el texto de «totalmente pagado») · `{{5}}` enlace al portal.

```
Hola {{1}}, recibimos tu pago ✅

• Monto: ${{2}}
• Fecha: {{3}}
• Saldo pendiente: ${{4}}

Consulta tu detalle aquí:
{{5}}

Gracias por confiar en Quinta Ko'ox Tanil.
```

### Recordatorio de pago → `META_WA_PLANTILLA_RECORDATORIO` (Utilidad)
Variables: `{{1}}` nombre de pila · `{{2}}` monto de la parcialidad · `{{3}}` fecha límite · `{{4}}` saldo total pendiente · `{{5}}` enlace al portal.

```
Hola {{1}}, te recordamos tu siguiente pago en Quinta Ko'ox Tanil:

• Parcialidad: ${{2}}
• Fecha límite: {{3}}
• Saldo pendiente: ${{4}}

Págalo desde tu portal:
{{5}}

Si ya lo hiciste, ignora este mensaje.
```

### Guía pre-evento → `META_WA_PLANTILLA_GUIA` (Utilidad)
Variables: `{{1}}` nombre de pila · `{{2}}` fecha del evento · `{{3}}` enlace de descarga de la guía.

```
Hola {{1}}, tu evento del {{2}} en Quinta Ko'ox Tanil ya está muy cerca 🌴

Aquí tienes la guía con lo que necesitas saber antes de llegar:
{{3}}

Si tienes dudas, respóndenos por aquí.
```

### Seguimiento, día 1 → `META_WA_PLANTILLA_SEGUIMIENTO_DIA1` (Marketing)
Variables (las 5 de todo seguimiento): `{{1}}` nombre de pila · `{{2}}` folio · `{{3}}` fecha del evento · `{{4}}` total con IVA, sin `$` · `{{5}}` enlace al portal. Pie: «Responde BAJA si no quieres más mensajes».

```
Hola {{1}}, gracias por cotizar con Quinta Ko'ox Tanil.

• Cotización: {{2}}
• Fecha: {{3}}
• Total: ${{4}} (IVA incluido)

Revísala y aparta tu fecha aquí:
{{5}}

¿Alguna duda? Contéstanos por aquí y Kooxi, nuestro asistente, te ayuda.
```

### Seguimiento, día 3 → `META_WA_PLANTILLA_SEGUIMIENTO` (Marketing)
Mismas variables y pie.

```
Hola {{1}}, tu cotización en Quinta Ko'ox Tanil sigue disponible:

• Cotización: {{2}}
• Fecha: {{3}}
• Total: ${{4}} (IVA incluido)

La fecha se aparta con tu primer pago desde tu portal:
{{5}}

¿Tienes dudas? Contéstanos por aquí y Kooxi, nuestro asistente, te ayuda.
```

### Seguimiento, fecha por ocuparse → `META_WA_PLANTILLA_SEGUIMIENTO_FECHA` (Marketing)
Mismas variables y pie.

```
Hola {{1}}, tu fecha del {{3}} en Quinta Ko'ox Tanil está por ocuparse.

• Cotización: {{2}}
• Total: ${{4}} (IVA incluido)

La fecha solo queda apartada con tu primer pago:
{{5}}

Si necesitas otra fecha o tienes dudas, contéstanos por aquí.
```

### Visita → `META_WA_PLANTILLA_VISITA` (Utilidad)
Una variable: `{{1}}` el texto del aviso, **ya aplanado** por el ERP (Meta no admite saltos de línea en una variable), así que el formato en bloques solo se puede lograr en la parte fija del cuerpo.

```
Quinta Ko'ox Tanil · Tu visita

{{1}}

¿Necesitas cambiarla? Responde a este mensaje.
```

### Encuesta → `META_WA_PLANTILLA_ENCUESTA` (Utilidad)
Variables: `{{1}}` nombre de pila · `{{2}}` tipo de evento. Botones de respuesta rápida: `Excelente` · `Regular` · `Mala` (sin cambios).

```
Hola {{1}}, gracias por vivir tu {{2}} en Quinta Ko'ox Tanil.

¿Cómo calificarías tu experiencia?

También puedes responder con un número del 1 al 5.
```

### Reactivación por aniversario → `META_WA_PLANTILLA_REACTIVACION` (Marketing)
Variables: `{{1}}` nombre de pila · `{{2}}` tipo de evento · `{{3}}` fecha del evento · `{{4}}` enlace del cotizador.

```
Hola {{1}}, hace un año celebraste tu {{2}} con nosotros el {{3}} en Quinta Ko'ox Tanil.

Si quieres volver a reunir a los tuyos, cotiza aquí:
{{4}}

Responde BAJA si no quieres recibir más mensajes.
```

## Al equipo

### Alerta de nueva cotización → `META_WA_PLANTILLA_ALERTA_INTERNA` (Utilidad)
Variables: `{{1}}` tipo de evento · `{{2}}` fecha · `{{3}}` total, sin `$` · `{{4}}` enlace al portal.

```
🔵 Nueva cotización web

• Tipo de evento: {{1}}
• Fecha: {{2}}
• Total a pagar: ${{3}}

Ábrela aquí:
{{4}}
```

### Alerta de pago acreditado → `META_WA_PLANTILLA_ALERTA_PAGO` (Utilidad)
Variables: `{{1}}` folio · `{{2}}` cliente · `{{3}}` monto, sin `$` · `{{4}}` saldo (puede decir «0.00 — evento totalmente pagado»).

```
💰 Se acreditó un pago en la reservación {{1}}

• Cliente: {{2}}
• Monto pagado: ${{3}}
• Saldo pendiente: ${{4}}

Revisa el detalle en el ERP.
```

### Alerta de contracargo → `META_WA_PLANTILLA_ALERTA_CONTRACARGO` (Utilidad)
Variables: `{{1}}` folio · `{{2}}` estado del contracargo · `{{3}}` monto, sin `$`.

```
🔴 Contracargo de Openpay en {{1}}

• Estado: {{2}}
• Monto: ${{3}}

Revisa el caso y los plazos en el ERP (Comercial → Contracargos).
```

### Aviso del equipo (4 parámetros) → `META_WA_PLANTILLA_AVISO_EQUIPO` (Utilidad)
Sin cambios: ya está en bloques y es la que usan todos los avisos de `Aviso` fuera de la ventana de 24 h (`docs/whatsapp_plantilla_aviso_equipo.md`). Variables: `{{1}}` encabezado · `{{2}}` título · `{{3}}` datos · `{{4}}` enlace.

### Aviso de operaciones (1 parámetro) → `META_WA_PLANTILLA_OPERACIONES`
Sin cambios: la apertura de conversación con el colaborador. Variable: `{{1}}` título de lo que viene.

```
Tienes un aviso nuevo: {{1}}.

Te mando el detalle enseguida en este mismo chat.
```

## Fuera de este documento

- `META_WA_PLANTILLA_SOLICITUD_FACTURA` es una plantilla de documento al contador, con su propio texto (`whatsapp_plantilla_solicitud_factura.md`).
