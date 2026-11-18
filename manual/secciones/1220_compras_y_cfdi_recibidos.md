---
titulo: Compras y facturas recibidas (CFDI)
capitulo: Contabilidad
orden: 1220
roles: [CONTADOR, DIRECCION]
---
Una **compra** es un gasto de la Quinta con su factura (CFDI). Las compras entran solas desde el SAT; también se pueden cargar a mano.

### Cómo entran

1. **Descarga automática del SAT**: cada hora el sistema pide al SAT los CFDI recibidos y emitidos y crea la compra de cada uno. Se revisa en **Contabilidad → Descargas** y **Contabilidad → Comprobantes**.
2. **Carga manual de XML**: en **Eventos → Compras**, la opción de subir XML. Solo entran facturas a nombre del RFC del negocio con régimen 626; las de sueldos y salarios (605) no entran.

### Clasificación

Cada compra se clasifica por **categoría de gasto**, que decide la cuenta contable. La categoría nace del proveedor: clasifica al **Proveedor** una vez y sus facturas futuras entran ya clasificadas.

| Si la compra… | Va a |
|---|---|
| Es comisión bancaria o de cobro | Gasto financiero (602.03) |
| Es staff externo | Costo (501.03) |
| Es mobiliario | Activo (115.03) |
| Está sin clasificar | 601.99 «Gastos por clasificar» (cuenta transitoria: su saldo distinto de cero son compras pendientes) |

Una compra **sin CFDI** no es deducible: va a la subcuenta de no deducibles de su grupo, nunca a «Otros».

### Compras que no hay que duplicar

- La factura de comisiones de BBVA solo es comprobante: el cargo ya está en libros. Su proveedor está marcado para que **no genere póliza**.
- La factura mensual de Openpay se registra contra la cuenta puente 106.02, no contra Bancos.

### Qué hacer si…

:::quehacer Una factura de proveedor llega con un RFC o régimen incorrecto
Pide al proveedor que la cancele y emita otra. No la cargues: el sistema la rechaza o la clasifica mal.
:::

:::quehacer Una compra quedó en «Gastos por clasificar»
Abre la compra, elige su categoría y guarda: la póliza se reapunta sola a la cuenta correcta. Mejor aún, clasifica al proveedor para que las siguientes ya salgan bien.
:::
