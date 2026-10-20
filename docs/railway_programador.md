# Guía de Railway: Programador único

Para el propietario. Reemplaza los 11 servicios de cron por **uno** que corre
`python manage.py programador` cada 5 minutos. Lo que se programa vive en
`core_erp/programador.py`; cada corrida queda en **Admin → Operaciones →
Ejecuciones**. Nada de esto lo hace el código: son pasos en Railway, uno por uno.

## 1. Crear las Shared Variables del proyecto

1. Railway → proyecto → **Settings → Shared Variables**.
2. Copia **todas** las variables del servicio `web` (mismo nombre, mismo valor). No pegues
   valores en chats ni en Issues.
3. Excepciones: no copies `PORT` ni las `RAILWAY_*` (las pone Railway).

## 2. Crear el servicio «Programador»

1. **New → GitHub Repo →** este repo (misma rama que `web`).
2. **Settings → Deploy:**
   - *Cron Schedule:* `*/5 * * * *`
   - *Start Command:* `python manage.py programador`
3. **Variables:** agrega cada variable como referencia a la compartida, `${{shared.NOMBRE}}`,
   más `DATABASE_URL=${{Postgres.DATABASE_URL}}`. Nunca un valor copiado: rotar una
   credencial en Shared Variables la rota en `web` y en el Programador.
4. Dispara una corrida (**Run now**) y revisa en **Ejecuciones** que aparezcan filas
   `Éxito`. Antes de desactivar nada, prueba: `python manage.py programador --simular`
   (lista qué correría sin ejecutar ni registrar).

## 3. Desactivar los crons viejos, uno por uno

Un cron por día, no todos juntos. Para cada uno:

1. Confirma en **Ejecuciones** que su tarea (mismo nombre que el comando) corrió con `Éxito`
   en su horario del Programador.
2. Desactiva el cron viejo (**Settings → Cron Schedule** vacío; no lo borres todavía).
3. Al día siguiente vuelve a revisar que la ranura corrió sola.

| Cron viejo (comando) | Horario en el Programador (hora de Mérida) |
|---|---|
| `cerrar_cotizaciones` | diario 00:10 |
| `enviar_recordatorios` | diario 18:00 |
| `enviar_guias` | diario 09:00 |
| `enviar_recordatorios_contador` | diario 18:00 |
| `procesar_tareas_operativas` | cada 15 min |
| `avisar_vencimientos --aplicar` | diario 07:05 |
| `sincronizar_sat` | cada hora |
| `sincronizar_comisiones_openpay --aplicar` | día 2, 08:45 |
| `purgar_conversaciones_whatsapp --aplicar` | diario 03:00 |
| `enviar_seguimientos` | diario 10:52 |
| `resumir_preguntas_agente --aplicar` | lunes 08:52 |
| `revisar_pendientes` (nuevo) | cada 15 min |
| `enviar_resumen_diario` (nuevo) | diario 08:00 |

`revisar_pendientes` y `enviar_resumen_diario` no existían como cron: solo se agendan aquí.

## 4. Borrar los crons viejos

Después de **2 semanas** sin fallos en **Ejecuciones** ni pendientes de tipo «Tarea
programada», borra los servicios viejos.

## Cómo te enteras si algo falla

- Una tarea que falla, o que no corrió dentro de su tolerancia, abre un **pendiente urgente**
  para Dirección (WhatsApp) con el nombre de la tarea y su ranura. Se avisa una sola vez por ranura.
- Si el propio Programador lleva más de 30 min sin registrar una corrida, el admin muestra un
  aviso rojo («El programador no está corriendo») a los superusuarios, y el resumen diario
  trae «Programador: última vuelta hh:mm».
- Una ranura atrasada se corre una sola vez, solo si sigue dentro de su tolerancia
  (3 h – 24 h según la tarea); si no, no se recupera y te llega el pendiente.

## Rollback

Reactivar el cron viejo (ponerle de nuevo su *Cron Schedule*) y desactivar la fila equivalente
quitando el Programador o su schedule. Un cron viejo y el Programador corriendo a la vez no
rompen datos (los comandos son idempotentes), pero sí duplican avisos, por eso se pasa uno por uno.
