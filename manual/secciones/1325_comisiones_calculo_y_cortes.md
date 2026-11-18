---
titulo: Comisiones: cálculo, cortes y pago
capitulo: Nómina y pagos
orden: 1325
roles: [DIRECCION]
---
Cómo se calcula la comisión de ventas, cómo se hace cada corte y cómo se paga. Todos los montos, porcentajes y topes salen de las **Condiciones** aceptadas de cada persona (**Nómina → Condiciones**); el sistema nunca los trae fijos.

### Qué se devenga

- **Visita atendida**: la cierra quien atendió (botón de WhatsApp o acción en **Comunicación → Visitas**). Las primeras visitas de la semana, hasta el tope de las Condiciones, pagan el monto por visita; las demás quedan en $0.00 con la nota *Sobre el tope semanal* y **igual atribuyen la venta**. Una visita que cambia a *No llegó* se revierte.
- **Contratación**: una cotización no histórica, con precio mayor a cero, que llega a *Confirmada* con vendedora. Una cortesía al 100 % no genera comisión. El monto fijo es el del escalón del número que le toca en el año; una cancelada con devolución total no cuenta para el escalón siguiente. Los valores se **sellan** con las Condiciones vigentes ese día.
- **Extras**: el porcentaje de las Condiciones sobre todo lo que no es la base del servicio (horas y personas extra incluidas), sin IVA y neto de descuento.
- **Proporción al cobro**: se devenga en proporción a lo pagado neto de reembolsos. No cuentan depósito en garantía, propinas ni condonaciones. Quién registró el pago no importa: manda la vendedora de la cotización.
- **Baja**: se paga lo devengado hasta la fecha de baja; lo que el cliente pague después no devenga y el saldo en contra a esa fecha se absorbe.

El motor corre solo cuando cambia un pago, una cotización o una visita, y hay un barrido diario de respaldo. En la cotización, el panel **Comisión** (solo Dirección) muestra el detalle y **Seguimiento** muestra qué le ha pasado a la venta, quién y cuándo.

### Cortes

- **Visitas**: el domingo por la mañana, semana de lunes a domingo; se pagan ese domingo.
- **Comisiones**: los días 16 y 1, de la quincena cerrada; se pagan el domingo siguiente.

Cada corte nace en *Borrador* y te llega un pendiente urgente con el botón **Aprobar**. Aprobar **no paga**. Revisa antes las visitas de alta manual, que se listan aparte, y los totales.

1. En **Ventas → Cortes**, aprueba el corte (si no usaste el botón del WhatsApp).
2. Usa **Marcar pagado** (solo Dirección): elige efectivo o transferencia (y la cuenta, o deja vacía la cuenta pagadora) y captura la referencia. Se genera la póliza del gasto no deducible, el PDF del estado y el aviso a la persona. Si la póliza no puede asentarse, el pago no queda registrado.
3. Si te equivocaste: **Cancelar pago** (cancela la póliza y el corte vuelve a *Aprobado*) o **Cancelar corte** (libera sus movimientos para el siguiente).

### Cancelaciones y reembolsos

La comisión se ajusta a lo que la Quinta conserva. **Nunca se le pide dinero a la persona**: el saldo en contra se compensa contra comisiones futuras, hasta el porcentaje máximo de las Condiciones por cada pago; el resto se arrastra y un corte nunca queda negativo. Si la cancelación es imputable a la Quinta, abre la comisión y usa **Absorber** (motivo obligatorio): cancela el saldo en contra sin pagarse y cuenta como costo de ventas.

### Tablero Ventas

**Ventas**, en la barra superior, muestra por persona y periodo: visitas (agendadas, atendidas, no llegó, sobre el tope), cotizaciones y contrataciones con su tasa de cierre, venta sin IVA, avance al escalón del año, comisión devengada, pagada y por pagar, saldo en contra, absorbido, lo no devengado por una baja y el **efectivo por entregar**, con una tabla por contratación. El mismo detalle sale en PDF y Excel en **Reportes → Comisiones por periodo**.

### Efectivo de clientes

En **Ventas → Entregas** registras el efectivo que recibes de cada vendedora y qué cobros cubre. No genera póliza (el cobro ya cargó Caja) y **no existe forma de compensarlo contra comisiones**.

### Dónde más se ve

- **Reportes → Rentabilidad**: columna de comisión devengada por evento; **Nómina por periodo**: renglón aparte *Comisiones y pagos a ventas* y costo total de personal.
- **Indicadores**: la comisión devengada entra al costo variable de cada servicio, así que margen, equilibrio y plan del año ya la consideran.
- **Marketing**: el prospecto muestra la vendedora de su cotización; los clientes registrados por la vendedora tienen su propia fuente (*Vendedor*), no se acreditan a un anuncio, y el costo de ventas aparece junto al gasto de pauta sin entrar al CAC.
- **WhatsApp**: el lunes la vendedora recibe su resumen de la semana y tú una línea en el resumen semanal.
