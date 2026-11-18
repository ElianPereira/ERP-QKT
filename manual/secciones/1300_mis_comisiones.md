---
titulo: Mis comisiones: qué ganas y cuándo se paga
capitulo: Nómina y pagos
orden: 1300
roles: [VENTAS, DIRECCION]
---
**Mis comisiones** es tu pantalla personal: solo muestra lo tuyo (tus visitas, tus contrataciones y tus pagos). Ábrela desde **Mis comisiones**, en la barra superior. Funciona igual en el celular.

### Qué ves en la pantalla

![Pantalla Mis comisiones en el celular](img/mis-comisiones.webp)

- **Contrataciones del año**: cuántas llevas y cuánto paga la siguiente. Si está cerca un cambio de escalón, te dice cuántas contrataciones faltan.
- **Ganado este mes**: lo que ya devengaste (visitas y contrataciones).
- **Por cobrar**: lo ganado que todavía no se te paga.
- **Pagado en el año**: lo que ya recibiste.
- **Visitas**: las del mes (atendidas, no llegó y las que quedaron sobre el tope semanal) y tus próximas visitas confirmadas.
- **Mis contrataciones**: una fila por venta, con el número que lleva en el año, tu comisión, lo ganado y lo que falta por ganar.
- **Mis estados de cuenta**: el detalle de cada corte, con su PDF para descargar. Un estado solo aparece cuando el corte ya se pagó.

### Cómo se gana cada cosa

1. **Visita atendida**: cuando cierras la visita como *Atendida* se te devenga el pago por visita de tus condiciones. Hay un tope de visitas pagadas por semana (de lunes a domingo); las que pasan del tope ya no pagan, pero **sí te atribuyen la venta**. Una visita en la que el cliente no llegó no paga.
2. **Contratación**: cuando una venta tuya queda confirmada se fija tu comisión: un monto por contratación (según el número del año en que vas, con los escalones de tus condiciones) más un porcentaje sobre los extras de la venta.
3. **Se gana en proporción a lo que el cliente paga**: si el cliente lleva la mitad pagada, llevas la mitad de la comisión; cada abono te devenga su parte. Un depósito en garantía, una propina o un saldo condonado no cuentan como pagado.

:::nota
Las ventas que cierran solos Kooxi o la web **no** son tuyas. Cuenta lo que tú cotizas, lo que sale de una visita que atendiste y lo que un cliente tuyo reserva dentro del plazo de atribución de tus condiciones.
:::

### Cuándo se paga

- **Visitas**: corte cada semana, se pagan el **domingo**.
- **Comisiones de contratación**: corte quincenal (los días 15 y último del mes) y se pagan el domingo siguiente.
- Antes de pagar, la Dirección revisa y aprueba el corte. Cuando se paga recibes un WhatsApp con el total y tu estado de cuenta por correo.

### Si una venta se cancela o se reembolsa

Tu comisión se ajusta a lo que la Quinta conserva. Si ya te habían pagado, **nunca se te pide dinero**: el ajuste se descuenta de tus próximas comisiones, con un máximo por pago (el porcentaje acordado en tus condiciones). Si algo queda pendiente lo ves como *Ajuste por cancelación* en tu pantalla. El pago por visita no se revierte.

### Efectivo que cobras a clientes

Si registras un cobro en efectivo, ese dinero es de la Quinta: hay que entregarlo a la Dirección. En tu pantalla aparece el aviso *Efectivo por entregar* mientras falte. **Nunca se descuenta de tus comisiones.**

:::quehacer No veo una visita o una venta mía
Primero revisa que la visita esté cerrada como *Atendida* y que la cotización muestre tu nombre en **Atendido por**. Si ya está así y no aparece, avisa a la Dirección con el folio.
:::

:::rol DIRECCION
Las condiciones de pago de cada vendedora (porcentajes, escalones y plazos) se capturan en **Nómina → Condiciones** y se aceptan antes de empezar. Para ver lo que ve cada vendedora, no uses su cuenta: el tablero **Ventas** (sección *Comisiones: cálculo, cortes y pago*) trae sus cifras. La pantalla *Mis comisiones* resuelve a la persona solo desde su usuario: ningún parámetro de la dirección muestra a otra.

Para que el enlace aparezca en el menú, el equipo técnico corre `crear_grupos_permisos` (el grupo Vendedor recibe el permiso de ver su propia pantalla y nada más del módulo).
:::
