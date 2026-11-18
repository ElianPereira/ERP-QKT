---
titulo: Qué explicar del contrato, la firma y el depósito
capitulo: Contrato y depósito
orden: 700
roles: [VENTAS, ENCARGADO, DIRECCION]
---
El cliente va a preguntar qué firma y cuánto deja de depósito. Tú no generas el contrato ni cobras el depósito: solo explicas, con las reglas del sistema.

### El contrato

- El contrato se **genera solo** en cuanto la cotización queda *Confirmada* (el cliente ya pagó el anticipo). Al cliente le llega un correo "listo para firmar".
- Lo firma **desde su portal**: lee el contrato, el sistema le manda un **código de 6 dígitos** a su correo (o a su WhatsApp, si no dio correo), lo escribe y firma con el dedo o el mouse. El contrato firmado queda guardado en el sistema.
- Antes de firmar, el portal le pide aceptar el **Aviso de Privacidad**; sin eso no puede firmar ni pagar en línea.
- Hay un contrato propio para cada servicio: Evento, Pasadía y Hospedaje.

### El depósito en garantía

El depósito **no es un pago de la venta**: es dinero que se devuelve si no hay daños. Se aplica así:

| Servicio | Depósito |
|---|---|
| Evento | {{ deposito_evento_pct }}% del precio final |
| Hospedaje | ${{ deposito_por_habitacion }} por cada habitación contratada (más el recargo por mascota que marca el contrato, si aplica) |
| Pasadía | No lleva |

- Se devuelve dentro de **{{ dias_devolucion_deposito }} días naturales** después del servicio, una vez que el encargado revisa la Quinta.
- Si hubo daños, se descuenta lo que cuesten, y el cliente lo ve desglosado.

### Qué decir cuando el cliente pregunta

:::correcto
"El contrato se genera solo cuando confirmas con tu anticipo y lo firmas desde tu portal con un código que te llega al correo. El depósito se te devuelve después del evento si todo queda en orden."
:::

:::nohagas
Prometer que el depósito "siempre se devuelve completo" o cambiar su monto. Depende de la inspección posterior y solo la Dirección puede modificar condiciones.
:::

### Qué hacer si…

:::quehacer El cliente no recibió el correo de firma
Revisa que su correo esté bien en **Eventos → Clientes**. Dile que también puede entrar con el enlace de su portal, que le puedes mandar por WhatsApp desde la cotización.
:::
