---
titulo: Inspección y devolución del depósito
capitulo: Día del servicio
orden: 910
roles: [ENCARGADO, DIRECCION]
---
Después de cada servicio con depósito en garantía, el encargado dice si hubo daños. Con eso el sistema devuelve el depósito o retiene lo que corresponde.

### Cuándo te pregunta el sistema

Un día después del servicio, solo para depósitos con dinero en custodia. El aviso de WhatsApp trae el botón **Sin daños**.

### Si no hubo daños

Toca **Sin daños** en el aviso. El depósito se devuelve completo.

### Si hubo daños

1. Toma **al menos una foto** del daño.
2. Manda la foto al WhatsApp de la Quinta con este texto como pie: `daños 800 cot 145 vidrio roto de la ventana`. Cambia 800 por el monto, 145 por el folio y describe el daño (mínimo 5 caracteres).
3. Lo que se retiene lo decide la Dirección: ella recibe tu reporte y toca **Aprobar** (se retiene) o **Devolver todo**.

### Si nadie responde

Si pasan **{{ deposito_inspeccion_horas }} horas** sin reporte, el sistema da el depósito por devuelto completo. Reporta a tiempo.

:::nota
El depósito se devuelve por el mismo medio con el que se cobró cuando fue con tarjeta. Si se cobró en efectivo o transferencia, **no se devuelve solo**: el sistema avisa a la Dirección, que hace la devolución y la registra.
:::

:::nohagas
Reportar daños sin foto o sin descripción: el sistema no deja retener nada así, ni lo debes prometer al cliente.
:::

:::rol DIRECCION
### Para la Dirección

- Todo cargo por daños requiere tu aprobación mientras `ERP_DEPOSITO_TOPE_RETENCION` esté en 0 (valor por defecto).
- Lo retenido por daños se factura al cliente sin IVA como indemnización; lo retenido por un servicio (tiempo extra, limpieza) lleva IVA. El contador recibe esas solicitudes solas.
- Los depósitos pagados con SPEI o efectivo quedan en un pendiente tuyo para devolverlos a mano.
:::
