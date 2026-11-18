---
titulo: Cambios de fecha y cancelaciones: qué decir y a quién pasarlas
capitulo: Cancelaciones
orden: 800
roles: [VENTAS, ENCARGADO, DIRECCION]
---
Tú **no** cancelas, no devuelves dinero y no cambias fechas pagadas. Tu trabajo es escuchar, explicar las reglas con calma y pasar el caso a la Dirección.

### Cómo llega una solicitud

El cliente pide el cambio o la cancelación **desde su portal** o por WhatsApp con Kooxi. En los dos casos el sistema le manda un código de 6 dígitos a su correo, que debe escribir para comprobar que es él. Con el código, la solicitud llega a la Dirección con el análisis: cuánto pagó, qué porcentaje aplica y qué reembolso corresponde.

Si el cliente te lo dice **a ti** (en persona o por tu llamada), dile que lo haga desde su portal o por el WhatsApp de la Quinta, y avisa a la Dirección.

### Lo primero: ofrece cambiar la fecha

Siempre ofrece primero **mover la fecha** sin cancelar. Es mejor para el cliente y para la Quinta.

### Qué se devuelve si cancela

La penalización depende de cuánto falta para la fecha.

**Evento y Pasadía** (la penalización se calcula sobre el valor total del servicio):

| Cuándo cancela | Penalización | Reembolso |
|---|---|---|
{% for tramo, pena, reembolso in tabla_cancelacion.SERVICIO %}| {{ tramo }} | {{ pena }} | {{ reembolso }} |
{% endfor %}

**Hospedaje** (el reembolso es sobre lo que el cliente ya pagó):

| Cuándo cancela | Penalización | Reembolso |
|---|---|---|
{% for tramo, pena, reembolso in tabla_cancelacion.HOSPEDAJE %}| {{ tramo }} | {{ pena }} | {{ reembolso }} |
{% endfor %}

Además, si el cliente cancela dentro de los **5 días hábiles posteriores a firmar el contrato**, se le devuelve el 100%.

:::nota
Estas cifras salen del sistema, que es la misma fuente del contrato. Si la Política de Cancelación cambia, esta tabla cambia sola.
:::

### Qué decir

:::correcto
"Entiendo. Antes de cancelar, ¿te ayudo a cambiar la fecha? Si prefieres cancelar, la solicitud la revisa la Dirección y te contestan con el monto exacto, según la fecha en que cancelas."
:::

:::nohagas
Prometer un reembolso, un porcentaje o un plazo de entrega del dinero. Solo la Dirección aprueba el reembolso, y se devuelve por el mismo medio en que pagó el cliente.
:::

### Qué hacer si…

:::quehacer El cliente cancela por una causa de fuerza mayor (enfermedad grave, clima extremo)
Pásalo a la Dirección con el folio y el motivo. Solo ella decide si aplica el caso de fuerza mayor documentado.
:::

:::quehacer El cliente reclama que ya canceló y no le han devuelto el dinero
No lo contradigas ni prometas fecha. Avisa a la Dirección con el folio; ella ve el estado de la solicitud en **Eventos → Cancelaciones**.
:::
