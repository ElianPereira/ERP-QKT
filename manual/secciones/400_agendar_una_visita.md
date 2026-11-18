---
titulo: Agendar una visita a la Quinta
capitulo: Visitas
orden: 400
roles: [VENTAS, ENCARGADO, DIRECCION]
---
Una visita es una cita para que un posible cliente conozca la Quinta. Las citas solo se pueden dar en los **bloques de visita**, que se configuran en **Comunicación → Horarios**. Hoy están abiertos:

{% for h in horarios_visita %}- {{ h }}
{% empty %}- (Todavía no hay horarios capturados. Sin horarios no se pueden agendar visitas.)
{% endfor %}

Kooxi también agenda visitas por WhatsApp, pero esas nacen **por confirmar**; en cambio, la que das de alta tú queda confirmada al instante.

### Agendar una visita a mano

:::rol VENTAS, DIRECCION
1. En el menú lateral entra a **Comunicación → Visitas** y toca **Agregar Visita**.
2. Escribe el **nombre** de quien visita y su **teléfono** (10 dígitos).
3. Indica cuántas **personas** vienen.
4. Elige la **fecha y hora** de la cita. Solo se aceptan horas dentro de los bloques abiertos y que no estén ocupadas.
5. Guarda.

:::correcto
La visita aparece en la lista con estado *Confirmada* y tuya como vendedora. El cliente recibe en su WhatsApp la fecha, el domicilio y el mapa.
:::

### Reglas que el sistema revisa por ti

| Si… | El sistema… |
|---|---|
| La hora no está en un bloque abierto, o ya la ocupó otra visita | Rechaza la cita y dice que ese horario no está disponible |
| La persona ya tiene una visita agendada | Rechaza: solo puede tener una vigente |
| Ese día la Quinta tiene un evento, pasadía o bloqueo | Rechaza; solo Dirección puede agendar sobre un día ocupado |
| La hora ya pasó | Rechaza: la cita debe ser futura |

### Cancelar una visita

1. En **Comunicación → Visitas** marca la visita.
2. En el menú de acciones elige **Cancelar visitas** y confirma. El horario se libera y el cliente recibe el aviso.
:::

:::rol ENCARGADO
### Tu parte

Cuando Kooxi agenda una visita, el sistema te manda un aviso con botones **Confirmar** y **No disponible**. También puedes decidir desde **Comunicación → Visitas**: marca la visita y elige **Confirmar visitas** o **Marcar como no disponible**. Si no la decides en 4 horas (o llega la hora de la cita), el aviso vence.
:::

### Qué recibe el cliente y tú

- **La víspera, a las 6:00 p.m.**: el cliente recibe un recordatorio y el equipo recibe el resumen de las visitas de mañana.
- **Dos horas antes**: el cliente recibe un recordatorio, si la confirmaron con más anticipación.
- **45 minutos antes**: aviso al equipo.
