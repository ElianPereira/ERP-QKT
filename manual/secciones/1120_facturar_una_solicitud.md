---
titulo: Facturar una solicitud (para el contador)
capitulo: Facturación
orden: 1120
roles: [CONTADOR, DIRECCION]
---
Cada pago registrado en el ERP crea una **solicitud de factura**. Tú la recibes, la timbras fuera del ERP y subes el resultado.

### Qué recibes

Por cada solicitud llega un correo y un WhatsApp con el PDF de la solicitud. Trae:

- Los datos fiscales del receptor (o *Público en general* si el cliente no los dio).
- La **línea de negocio**: el RFC emisor bajo el que debes timbrar.
- El monto con su desglose, la forma de pago y el concepto.
- Si es un reembolso, el tipo es **nota de crédito (E)**, relacionada a los CFDI de ingreso de esa cotización.

### Timbrar y subir la factura

1. Entra a **Facturación → Solicitudes** y abre la solicitud (o filtra por estado *Enviada al contador*).
2. Timbra la factura en tu sistema con los datos del PDF.
3. En **Archivos de Factura**, sube el **ZIP** con el PDF y el XML, o los dos archivos por separado, y captura el **UUID** y la **fecha de factura**.
4. Cambia el **Estado** a *Factura recibida* y guarda.

:::correcto
Al quedar *Factura recibida*, el cliente recibe sus archivos por correo (a *Público en general* no se le manda nada).
:::

### El sistema también te ayuda

El ERP descarga solo del SAT los CFDI emitidos y los liga a su solicitud (mismo RFC receptor, monto y fecha). Si no puede decidir entre varias candidatas, te manda **un solo mensaje por WhatsApp al día** con botones para elegir. Los que no tienen candidata los ligas tú en **Contabilidad → Comprobantes**.

### Reglas de la forma de pago

| Cómo pagó el cliente | Forma de pago que se pone |
|---|---|
| Efectivo o depósito en ventanilla | 01 Efectivo |
| Transferencia o SPEI | 03 Transferencia |
| Tarjeta de débito | 28 |
| Tarjeta de crédito | 04 |

Si el contador corrige a mano una solicitud (por ejemplo, el monto), el PDF y el desglose se recalculan con lo que quedó guardado.

### Qué hacer si…

:::quehacer La solicitud trae RFC y razón social pero el SAT rechaza el timbrado
Revisa el régimen fiscal y el uso de CFDI: el sistema los ajusta cuando no son compatibles (por ejemplo, el régimen 616 solo admite el uso S01), pero un dato mal capturado por el cliente puede seguir siendo inválido. Corrige los campos en la solicitud y deja una nota.
:::

:::quehacer Una solicitud se creó por error
Cancélala: en **Facturación → Solicitudes**, marca la solicitud y elige **Cancelar solicitudes**. No se borra, queda marcada como cancelada.
:::
