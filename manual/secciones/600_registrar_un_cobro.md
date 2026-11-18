---
titulo: Registrar un cobro
capitulo: Cobros
orden: 600
roles: [VENTAS, ENCARGADO, DIRECCION]
---
Un cobro es el dinero que el cliente entrega por su reservación. Se registra **una sola vez**, en cuanto llega, y siempre contra una cotización. El sistema calcula solo el saldo, avisa si algo no cuadra y, cuando el cliente ya pagó lo suficiente, aparta la fecha.

### Antes de empezar

- Ten a la mano el **folio de la cotización** (COT-123) o el nombre del cliente.
- Cuenta el dinero o confirma que la transferencia ya aparece en la cuenta del banco. No registres un cobro "porque ya va a llegar".
- Con un pago acumulado de **{{ porcentaje_anticipo }}%** o más del total, la cotización se confirma sola y la fecha queda apartada.
- A quien paga desde su portal, el sistema le pide el saldo completo cuando falta poco para el servicio: Eventos a {{ dias_pago_total.EVENTO }} días o menos, Pasadías y Hospedaje a {{ dias_pago_total.PASADIA }} días o menos. Si el cliente te lo paga a ti, aplica la misma regla de sentido común: cobra el saldo completo.

### Registrar el cobro

1. En el menú lateral entra a **Eventos → Pagos** y toca **Agregar Pago Aprobado**.

    ![Lista de pagos con el botón para agregar](img/cobro-lista.webp)

2. El formulario tiene pestañas. En **Tipo** revisa que **Tipo** sea *Ingreso* y **Concepto** sea *Pago de la venta* (ya vienen así).
3. Pasa a la pestaña **Datos**. En **Cotización** elige la cotización: puedes escribir el folio o el nombre del cliente para encontrarla.
4. Captura la **fecha** en que se recibió el dinero y el **monto** exacto.
5. Elige el **Método** según cómo pagó el cliente:

    | Si el cliente pagó… | Método |
    |---|---|
    | Con billetes o monedas | Efectivo |
    | Con una transferencia o SPEI | Transferencia electrónica |
    | Con tarjeta en la terminal de la Quinta | Tarjeta de débito o de crédito |
    | Depositando efectivo en ventanilla del banco | Depósito en efectivo (ventanilla) |

6. En **Referencia** escribe el dato que sirva para encontrarlo después: la clave de rastreo de la transferencia, o los últimos dígitos de la autorización de la terminal. En efectivo puede quedar vacía.
7. Deja **Cuenta bancaria** vacía. El sistema usa la cuenta principal, y si el banco muestra otra, se corrige al cargar el estado de cuenta.
8. Si el cliente pidió factura, pasa a la pestaña **Facturación** y marca **¿Solicitar factura?**, solo si ya diste los datos fiscales del cliente.
9. Toca **Guardar**.

    ![Formulario para registrar un pago](img/cobro-formulario.webp)

:::correcto
Después de guardar, la cotización muestra el pago y el saldo baja por el mismo monto. Si con este cobro el cliente ya cubrió el anticipo, la cotización pasa sola a **Confirmada** y la fecha queda apartada.
:::

:::rol VENTAS
### Lo que no puedes registrar

El sistema no te deja registrar **reembolsos** ni **condonaciones o cortesías**: son decisiones de la Dirección. Si un cliente pide que "se le perdone" una parte o se le devuelva algo, no lo captures; pásalo a la Dirección.
:::

### Efectivo

:::nota
El efectivo no pasa por el banco: el sistema lo toma como dinero en caja hasta que alguien lo deposita. Cuando se deposita, el movimiento se empareja solo al cargar el estado de cuenta.
:::

[CONFIRMAR: ¿Quién recibe el efectivo de los clientes, dónde se guarda mientras tanto y a quién se entrega el sobre con el recibo? ¿Se le da un comprobante al cliente y de qué tipo?]

### Qué hacer si…

:::quehacer El sistema dice que el monto excede el saldo
Revisa que sea la cotización correcta y que el pago no se haya capturado antes. Si el cliente realmente dio más de lo que debe, no lo captures como pago de la venta: avisa a la Dirección.
:::

:::quehacer El cliente dice que ya pagó con tarjeta en línea y no lo ves
Los pagos hechos desde el portal del cliente se registran solos; no los captures a mano. Espera unos minutos y revisa en **Pagos**. Si no aparece, avisa a la Dirección con el folio.
:::

:::quehacer Capturaste un pago con datos equivocados
No lo borres ni lo vuelvas a capturar. Avisa a la Dirección con el folio y qué debía decir; ella lo corrige para que el historial quede completo.
:::

:::nohagas
Capturar la **comisión de la terminal** del banco. Esa comisión se asienta sola al cargar el estado de cuenta; capturarla aquí la duplica.
:::
