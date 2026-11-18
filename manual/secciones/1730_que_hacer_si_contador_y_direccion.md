---
titulo: Qué hacer si… (casos del contador y la Dirección)
capitulo: Qué hacer si…
orden: 1730
roles: [CONTADOR, DIRECCION]
---
:::quehacer Un abono del banco no tiene pago
Un cliente pudo transferir directo. Si el concepto trae el folio (COT-123) y cabe en el saldo, el sistema ya lo registró solo. Si no, abre el movimiento en **Contabilidad → Estados** y busca la cotización por el nombre y el monto; registra el pago en la cotización. Si el sistema propuso un candidato, la Dirección recibe un aviso con botones.
:::

:::quehacer Un depósito de Openpay o de la terminal no cuadra con el cobro
El banco deposita **neto de comisión**, y Openpay junta varios cobros en un solo depósito. El sistema ya prueba «cobro menos comisiones» y combinaciones únicas. Si hay varias posibles no adivina: elige tú el asiento a mano.
:::

:::quehacer Llegó la factura mensual de comisiones de BBVA y el mes ya cuadraba
Esa factura es solo comprobante: el cargo ya está en libros. Su proveedor está marcado para **no generar póliza ni buscar cargo**. Si te duplicó algo, revisa que el proveedor tenga esa marca.
:::

:::quehacer Un CFDI emitido no encuentra su solicitud
El sistema liga por RFC, monto (±$0.01) y fecha. Con 0 o varias candidatas queda **«Emitido sin solicitud»**: asígnalo en **Contabilidad → Comprobantes**, campo **Solicitud de factura**.
:::

:::quehacer La descarga del SAT marca «Con error» en varias solicitudes
Revisa el mensaje y código de la solicitud. Un código 5004 (sin información) es normal y no es error; un rechazo por cuota (5002) se vuelve a pedir un día antes. Lo que falla de verdad vuelve a pedirse solo, hasta con 8 intentos por tramo. Si el SAT está caído, no hagas nada: el sistema lo reintenta cada hora.
:::

:::quehacer La cuenta puente de traspasos no está en cero
Un traspaso entre las dos cuentas propias tiene una punta sin registrar. Carga el estado de cuenta de la otra cuenta del mismo mes y usa **Volver a emparejar y aplicar reglas**.
:::

:::rol DIRECCION
:::quehacer Aparece un aviso rojo de que el programador no corre
Revisa en Railway el servicio del Programador y su última corrida en **Operaciones → Ejecuciones**. Si falló una tarea, el mensaje de error está en esa fila.
:::

:::quehacer Cambiaste de celular y no entras con el código
Pide a otro superusuario que borre tu dispositivo en **Autenticación (2FA)**, o hazlo desde el servidor; al entrar de nuevo el sistema te pide dar de alta el nuevo.
:::

:::quehacer Meta dejó de recibir las conversiones o el gasto no se descarga
Es casi siempre un **token vencido o revocado**: el sistema abre un pendiente urgente. Genera uno nuevo en Meta, actualízalo en Railway y vuelve a correr la sincronización.
:::
:::
