# Plantilla de WhatsApp — Módulo de operaciones (colaboradores)

El aviso de horario especial y el checklist operativo (`operaciones/services.py`)
se mandan **al colaborador**, iniciados por el sistema — no hay garantía de que le
haya escrito al número del negocio en las últimas 24 horas. Fuera de esa ventana,
Meta rechaza el texto libre (error 131047). Por eso cada uno de esos dos mensajes va
precedido de una plantilla aprobada que abre la conversación; el contenido real
(la lista de tareas, que Meta no deja meter en una plantilla por los saltos de
línea) sale justo después como texto libre, ya con la ventana abierta.

Es la misma plantilla para los dos casos (aviso de horario y checklist) — el código
solo le pasa **una variable**: el título de lo que viene a continuación.

## Qué someter en Meta Business Manager

1. Entra a **Meta Business Manager → WhatsApp Manager → Plantillas de mensajes →
   Crear plantilla**.
2. Categoría: **Utilidad** (`UTILITY`) — es una notificación operativa interna a un
   colaborador, no marketing. Se aprueba más rápido y no requiere opt-in de
   marketing.
3. Nombre de la plantilla: **`aviso_operaciones`** (en minúsculas, sin espacios —
   si usas otro nombre, ajusta la variable de entorno `WA_TEMPLATE_OPERACIONES`
   para que coincida, no hace falta tocar código).
4. Idioma: **Español (MX)** — mismo que `WA_TEMPLATE_LANGUAGE` (por defecto `es_MX`).
5. Encabezado: **ninguno** (solo cuerpo — no hace falta adjuntar nada, el detalle
   llega en el mensaje de texto libre inmediato).
6. Cuerpo del mensaje — usa **exactamente esta variable**, en este orden (el
   código manda `{{1}}` = título de la plantilla que originó el aviso, ej.
   "Preparación — Hospedaje", "Preparación — Evento" o "Revisión semanal"):

   ```
   Tienes un aviso nuevo: {{1}}.

   Te mando el detalle enseguida en este mismo chat.
   ```

   **Ejemplo de variable de muestra para la revisión de Meta** (te la va a pedir
   al someterla): `{{1}}` → `Preparación — Hospedaje`.

7. Pie de página (opcional): `Quinta Ko'ox Tanil`.
8. Botones: **ninguno** (no aplica, es solo apertura).
9. Envía a revisión. Las plantillas de solo texto suelen aprobarse en minutos a
   pocas horas.

## Cuando Meta la apruebe

En Railway (o tu `.env` local), define:

```
WA_TEMPLATE_OPERACIONES=aviso_operaciones
```

No hace falta ningún cambio de código ni redeploy adicional — la variable ya está
leída en `core_erp/settings.py` y `operaciones/services.py` la usa automáticamente
en el siguiente envío. Antes de que esté aprobada, o si la dejas vacía, el sistema
sigue funcionando igual, solo que el aviso/checklist va directo como texto libre
(funciona mientras el colaborador te haya escrito en las últimas 24 h).

## Si Meta rechaza la plantilla

- El cuerpo suena a marketing/promoción → reescribe en tono puramente informativo,
  sin llamados a la acción ni ofertas (el texto de arriba ya es así).
- Variable sin contexto → el texto ya deja claro qué es `{{1}}` para el revisor
  ("Tienes un aviso nuevo: {{1}}").
- Piden un ejemplo más específico → usa el de arriba (`Preparación — Hospedaje`)
  o cualquier otro título real de una `PlantillaChecklist` que ya tengas
  capturada en `/admin/operaciones/plantillachecklist/`.
