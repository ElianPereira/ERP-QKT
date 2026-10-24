# Plantilla de WhatsApp — Alerta interna de pago acreditado

`alertar_equipo_pago()` (`comunicacion/services_notificaciones.py`) avisa al
ENCARGADO (y a Dirección si tiene copia en vivo) cada vez que se acredita un pago.
Sin `META_WA_PLANTILLA_ALERTA_PAGO` el aviso sale como texto libre y Meta solo lo
entrega si ese número escribió al 9191 en las últimas 24 h (por eso no llegó el
aviso del pago de COT-144 el 24/10/2026). Con la plantilla llega siempre.

## Qué someter en Meta Business Manager

1. **WhatsApp Manager → Plantillas de mensajes → Crear plantilla**.
2. Categoría: **Utilidad** (`UTILITY`): aviso operativo interno, no marketing.
3. Nombre: **`alerta_pago_equipo`** (si usas otro, solo cambia la variable de entorno).
4. Idioma: **Español (MX)** (`es_MX`, igual que `META_WA_PLANTILLA_IDIOMA`).
5. Encabezado: **ninguno**.
6. Cuerpo, con las 4 variables en este orden (el código las manda así:
   folio, cliente, monto, saldo; el `$` va en el cuerpo, no en la variable):

   ```
   Se acreditó un pago en la reservación {{1}}.

   Cliente: {{2}}
   Monto pagado: ${{3}}
   Saldo pendiente: ${{4}}

   Revisa el detalle en el ERP.
   ```

7. Pie de página (opcional): `Quinta Ko'ox Tanil`.
8. Botones: ninguno.

### Ejemplos que pide Meta al someterla

| Variable | Ejemplo |
|---|---|
| `{{1}}` | `COT-144` |
| `{{2}}` | `Ana Ruiz` |
| `{{3}}` | `1,500.00` |
| `{{4}}` | `1,500.00` |

Cuando el evento queda liquidado, el código manda en `{{4}}` el texto
`0.00 — evento totalmente pagado`, así que el mensaje dice
«Saldo pendiente: $0.00 — evento totalmente pagado».

## Cuando Meta la apruebe

En Railway, servicio **web** (y el Programador si lo lleva, como
`${{web.META_WA_PLANTILLA_ALERTA_PAGO}}`):

```
META_WA_PLANTILLA_ALERTA_PAGO=alerta_pago_equipo
```

Sin cambios de código: el siguiente pago ya usa la plantilla.
