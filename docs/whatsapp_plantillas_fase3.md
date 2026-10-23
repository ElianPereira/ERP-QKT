# Plantillas de WhatsApp de la Fase 3 (para someter a Meta)

Issue #434. Idioma `es_MX`. Los parámetros no admiten saltos de línea ni más de 4 espacios seguidos.
Hasta que Meta las apruebe y se defina su variable en Railway, el comando correspondiente no manda nada.

## `encuesta_postevento` → `META_WA_PLANTILLA_ENCUESTA`
Categoría: **Utilidad** (satisfacción de un servicio ya prestado). Se manda un día después del servicio, solo a
quien aceptó MARKETING y no pidió la BAJA.

- **Cuerpo:** `Hola {{1}}, gracias por vivir tu {{2}} en Quinta Ko'ox Tanil. ¿Cómo calificarías tu experiencia? También puedes responder con un número del 1 al 5.`
  - `{{1}}` nombre de pila del cliente · `{{2}}` tipo de evento (p. ej. «Boda», «Pasadía»).
- **Botones de respuesta rápida (3):** `Excelente` · `Regular` · `Mala`. El ERP les asigna el payload
  `enc:<id>:5`, `enc:<id>:3` y `enc:<id>:1` al enviar; no se configuran en Meta.

Si califica 4–5 y existe `ERP_RESENA_GOOGLE_URL`, el ERP contesta (dentro de la ventana de 24 h que abre su
respuesta) con el agradecimiento y el enlace de reseña. Si califica 1–3, abre un pendiente URGENTE a Dirección.

## `reactivacion_aniversario` → `META_WA_PLANTILLA_REACTIVACION`
Categoría: **Marketing**. Se manda en el aniversario del evento, una sola vez por cliente y año, solo con
MARKETING aceptado, sin BAJA y sin otra reservación en curso.

- **Cuerpo:** `Hola {{1}}, hace un año celebraste tu {{2}} con nosotros el {{3}} en Quinta Ko'ox Tanil. Si quieres volver a reunir a los tuyos, cotiza aquí: {{4}}. Responde BAJA si no quieres recibir más mensajes.`
  - `{{1}}` nombre de pila · `{{2}}` tipo de evento · `{{3}}` fecha del evento (dd/mm/aaaa) · `{{4}}` enlace del cotizador.
