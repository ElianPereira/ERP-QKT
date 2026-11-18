---
titulo: Nómina semanal: generar y aprobar los recibos
capitulo: Nómina y pagos
orden: 1310
roles: [DIRECCION]
---
La nómina del personal por horas se genera cada semana a partir de las checadas de **Jibble** (o de un Excel) y deja un recibo por persona.

### Generar los recibos

1. Entra a **Nómina → Recibos**.
2. Si Jibble está configurado, arriba aparece el bloque de sincronización con el rango de la **semana pasada** (de lunes a domingo) ya capturado. Revisa las fechas y toca el botón de **sincronizar**.
3. El sistema te dice cuántos recibos generó. Si no encuentra datos de ese periodo, lo avisa sin crear nada.
4. Sin Jibble, usa la opción de **cargar nómina** con el Excel.

Cada recibo tiene su PDF (con el desglose de días y horas) y su póliza contable.

### Aprobar la semana

Cada **viernes a las 12:00** el sistema te manda por WhatsApp el resumen de la nómina de la semana (recibos no cancelados) con el botón **Aprobar**. También puedes hacerlo en **Nómina → Aprobaciones**.

:::nota
**Aprobar solo deja constancia** de quién la revisó y cuándo. No genera pólizas, no hace transferencias y no toca los recibos.
:::

### Marcar un recibo como pagado

Cuando pagas en efectivo, en **Nómina → Recibos** marca el recibo y elige **Marcar como pagado en efectivo (solo administrativo)**. Es solo un registro: no mueve dinero.

### Qué hacer si…

:::quehacer Jibble no trae datos de la semana
Revisa que el rango sea de lunes a domingo y que el personal haya checado. Si falta la configuración, aparece el aviso de que faltan las llaves de Jibble: se definen en las variables del servicio.
:::

:::quehacer Un empleado no recibe sus avisos o tareas de operación
Su teléfono en **Nómina → Empleados** está vacío o mal escrito. Corrígelo.
:::
