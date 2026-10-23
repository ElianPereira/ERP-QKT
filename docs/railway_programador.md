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
| `enviar_resumen_semanal` (nuevo, Fase 4) | lunes 08:30 |
| `revisar_desviacion` (nuevo, Fase 4) | diario 09:30 |
| `enviar_nomina_semanal` (nuevo, Fase 4) | viernes 12:00 |
| `preguntar_cfdi_contador` (nuevo, Fase 4) | diario 10:00 |
| `enviar_recordatorios_visitas` (nuevo, Fase 2a) | diario 18:00 |

`revisar_pendientes`, `enviar_resumen_diario`, `enviar_recordatorios_visitas` y los cuatro de la Fase 4 no existían como cron: solo se agendan aquí
(el programador ya los corre solo; no se dan de alta en Railway).

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

---

# Corte de nombres de variables (Issue #424)

Los nombres nuevos (`ORIGEN_SERVICIO_DATO`, catálogo completo en `docs/variables_entorno.md`) se
estrenan al pasar a Shared Variables. Mientras los nombres viejos sigan definidos el ERP los usa
(respaldo `legado=`) y deja un aviso en el log; **no hay prisa ni orden obligatorio**, y no cambia ningún valor.

## Pasos

1. **Crear las Shared Variables con los nombres nuevos y los mismos valores.** La tabla
   `docs/variables_entorno.md` trae el nombre nuevo y el legado de cada una. Copia el valor del
   nombre viejo al nuevo sin modificarlo (en particular `DJANGO_SECRET_KEY` = el `SECRET_KEY` actual: si
   cambia, se cierran todas las sesiones, y `ERP_SAT_FIEL_CIFRADO_SECRET` = el `SAT_FIEL_ENCRYPTION_KEY`
   actual: si cambia, la e.firma guardada deja de descifrarse). **No borres los nombres viejos todavía.**
2. **Desplegar** `web` y el Programador (`${{shared.NOMBRE}}`, nunca valores copiados).
3. **Revisar el log de arranque** de `web` y de la primera vuelta del Programador: no debe aparecer ninguna
   línea «usando nombre legado …». Si aparece, esa variable aún no está con el nombre nuevo en ese servicio.
4. **Verificar lo que mueve dinero** antes de borrar nada: abrir el portal de una cotización (Openpay.js carga con
   `OPENPAY_PAGOS_*`), un cobro de prueba si hay un entorno de prueba, y un WhatsApp de prueba desde Comunicación.
5. **Borrar los nombres viejos**, un servicio a la vez, y volver a revisar el log. También se pueden borrar de una vez
   `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY` y `CLOUDINARY_API_SECRET` (el código ya no las lee) y cualquier
   variable que no aparezca en el catálogo. `WA_NUMERO_NEGOCIO` pasa a llamarse `ERP_WA_NEGOCIO_NUMERO` y se
   conserva hasta que el directorio de responsables sea la única fuente.

## Trampas

- Una referencia `${{web.NOMBRE}}` a una variable renombrada en `web` queda **vacía** en el cron: por eso el
  corte se hace con Shared Variables, no renombrando solo en `web`.
- Un nombre nuevo mal escrito no falla: cae al nombre viejo (si sigue ahí) y, si ya se borró, al valor por
  defecto del código. Por eso el paso 3 antes del 5.
- El workflow de CI y los de IA siguen usando `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS`; funcionan por el respaldo
  y se actualizan junto con el retiro del respaldo.

## Rollback

Mientras no se hayan borrado los nombres viejos, basta con quitar el nombre nuevo: el ERP vuelve solo al viejo.
El respaldo `legado=` se retira en un PR posterior, 2 semanas después del corte.
