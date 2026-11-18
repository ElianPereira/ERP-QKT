---
titulo: Resolver una cancelación o un cambio de fecha
capitulo: Cancelaciones
orden: 810
roles: [DIRECCION]
---
Las solicitudes de cambio y cancelación llegan a ti ya analizadas. Esta sección explica cómo decidir y qué hace el sistema al ejecutar.

### Cómo llega

El cliente la solicita desde su portal o por WhatsApp y valida con un código al correo registrado. El sistema te manda un pendiente urgente con el análisis: lo pagado, el porcentaje que corresponde según los días que faltan, el reembolso, los reembolsos previos y qué tan probable es revender la fecha.

### Qué decides

Con el aviso de WhatsApp (o en **Eventos → Cancelaciones**), tienes tres botones:

- **Aprobar** la cifra propuesta.
- **Rechazar**.
- **Contraoferta**.

El botón **solo vale desde tu teléfono**, dura **10 minutos** y se usa una sola vez. Si pasó el plazo o el saldo cambió desde que se propuso, el sistema no ejecuta con la cifra vieja: te manda una propuesta nueva.

### Qué ejecuta el sistema al aprobar

1. **Tarjeta pagada en Openpay**: reembolsa sobre el cargo original, un `Pago` de reembolso por cada cargo, y cancela la cotización.
2. **Transferencia, efectivo o terminal**: no mueve dinero. Te deja un pendiente urgente **«Transferir $X»** para que hagas la devolución a una cuenta a nombre del titular; el sistema no guarda datos bancarios del solicitante.
3. **Cambio de fecha**: valida disponibilidad y mueve la fecha sin recotizar.

La penalización retenida se reconoce como ingreso (conserva su IVA) y **lo devuelto genera una nota de crédito** para el contador.

:::nota
Si una ejecución queda a medias (por ejemplo, un reembolso ya enviado a Openpay), el sistema **no la repite**: marca error y te pide verificar en Openpay antes de reintentar.
:::

### Por si falla la ventana de WhatsApp

**Eventos → Cancelaciones** tiene las mismas acciones (aprobar, rechazar, reintentar) con confirmación.

### Descuentos y cortesías

Los descuentos y cortesías los aplicas tú desde la cotización (acción de descuentos), nunca la vendedora. Una cortesía al 100% confirma la fecha sin pago.
