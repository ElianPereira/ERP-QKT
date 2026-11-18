---
titulo: Cargar y conciliar el estado de cuenta
capitulo: Contabilidad
orden: 1210
roles: [CONTADOR, DIRECCION]
---
La conciliación compara lo que el banco dice que pasó con lo que el ERP tiene registrado. Se hace **una vez al mes, por cada cuenta bancaria**.

### Cuentas del negocio

La Quinta usa dos cuentas BBVA: **Ingresos** (recibe los cobros: clientes, Openpay y terminal) y **Egresos** (paga proveedores, pauta, suscripciones y reembolsos, y se fondea con transferencias propias desde la primera). Todo estado de cuenta se concilia de cada una.

### Paso a paso

1. Confirma que el estado del mes esté en **Contabilidad → Estados**. Normalmente llega solo por correo los días 1 y 2 (ver *Qué hacer si el estado de cuenta del mes no llega*).
2. Si está *Subido, sin procesar*: marca el renglón y elige **Procesar (extraer movimientos y emparejar)**.
3. El sistema empareja solo cada movimiento con su pago, compra o regla. Abre el estado y revisa la columna **Emparejados**: hay que llegar al 100%.
4. Para los movimientos sin asiento, elige a mano el asiento correcto o crea la póliza que falta. Si ya cargaste lo que faltaba, usa **Volver a emparejar y aplicar reglas (sin releer el PDF)**.
5. Cuando todo esté emparejado, elige **Generar conciliación preliminar**.
6. Revisa en **Contabilidad → Conciliaciones** que la **diferencia** sea cero. Si no lo es, ve a la sección de abajo.

### Cosas que el sistema hace por ti

- **Transferencias de clientes**: un abono con el folio de la cotización (COT-123) se registra solo como pago. Si el sistema solo sospecha de quién es, manda una propuesta a la Dirección con botones.
- **Depósitos de Openpay y de la terminal**: llegan netos de comisión; el sistema los empareja con el cobro y su comisión.
- **Traspasos entre cuentas propias**: cada punta se asienta contra la cuenta puente 102.03. Un saldo distinto de cero ahí es un traspaso con una punta pendiente.
- **Comisiones del banco y de la terminal**: se asientan solas con las reglas del sistema.

### Si la diferencia no es cero

| Síntoma | Causa más común |
|---|---|
| Diferencia arrastrada de meses anteriores | Faltan pólizas o estados de periodos anteriores al primero cargado |
| Un depósito sin cobro | El cliente pagó directo al banco: registra el pago en la cotización |
| Un cargo sin asiento | Una compra sin registrar, o un retiro del dueño: crea la póliza |
| Saldo en 106.02 (Openpay por comprobar) | Falta la factura mensual de Openpay |

La **diferencia arrastrada** (lo heredado de periodos anteriores) se muestra aparte de la diferencia del periodo. Para cancelarla hay que proponer una **regularización**; la autoriza solo la Dirección.

:::nohagas
Borrar una póliza para "hacer cuadrar" el mes. Se cancela, no se borra: la póliza cancelada conserva su historial y queda fuera de los saldos.
:::
