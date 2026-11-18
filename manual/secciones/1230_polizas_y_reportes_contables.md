---
titulo: Pólizas, cierre y reportes contables
capitulo: Contabilidad
orden: 1230
roles: [CONTADOR, DIRECCION]
---
### Cómo nacen las pólizas

Las pólizas **las generan solas** los pagos, las compras, los recibos de nómina y las reglas del estado de cuenta. No se capturan a mano desde ventas. Cada póliza tiene un estado: *Borrador*, *Aplicada* o *Cancelada*. **Solo las aplicadas** entran en saldos y reportes.

### Lo que sí haces tú

1. **Aplicar** pólizas en borrador: en **Contabilidad → Pólizas**, marca las que quieras y elige **Aplicar pólizas seleccionadas**.
2. **Cancelar** una póliza equivocada: **Cancelar pólizas seleccionadas** (con confirmación). La póliza queda en el historial.
3. Pólizas manuales (de diario) para lo que no genera el sistema: retiros del dueño, ajustes, traspasos.

:::rol DIRECCION
### Para la Dirección

- **Borrar definitivamente** una póliza solo lo hace la Dirección (excepción autorizada para limpiezas históricas); se pide motivo y queda una foto en **Contabilidad → Eliminadas**.
- La **regularización de la diferencia arrastrada** de una conciliación la propone el contador y **la autoriza la Dirección**.
- **Cerrar el histórico**: cancela las pólizas anteriores a una fecha de arranque. Se simula primero. Después hay que capturar el **saldo de apertura certificado** en **Contabilidad → Apertura**.
:::

### Reportes

Entra al **Centro de reportes** (menú **Reportes**). Todos salen en PDF y la mayoría también en Excel.

| Reporte | Para qué sirve |
|---|---|
| Balanza de comprobación, estado de resultados, balance general, libro mayor, auxiliar | La contabilidad del periodo |
| Facturas emitidas | Solicitudes por estado y su UUID |
| Gastos por categoría | A dónde se fue el dinero |
| Flujo de efectivo | Entradas y salidas por cuenta bancaria, con los traspasos entre cuentas propias aparte |
| Conciliación bancaria | Qué quedó emparejado y qué no |
| Depósitos en garantía | Lo que está en custodia |

:::nota
El estado de resultados reconoce el ingreso **cuando el evento se ejecuta**: lo cobrado antes está en Anticipos de clientes. Si falta ingreso, revisa que la cotización ya esté en *Evento ejecutado*.
:::
