# Rutina Operativa/Contable — 2026-10-01

Solo análisis de código (modelos, signals, services). No se leyeron datos vivos
ni se tocó código. Se excluye lo ya resuelto o decidido en la Watchlist/Memoria
de `CLAUDE.md` y lo reportado el 2026-09-05 (margen por evento, reporte de
descuentos, bitácora de estados).

## Riesgos

### R1 — Devolver un depósito por Openpay puede reembolsar dos veces (dinero) — VERIFICADO

`liquidar()` (`comercial/services_deposito.py:98-103`) corre todo en un
`atomic()`, pero los reembolsos en Openpay se ejecutan **antes** de crear los
`MovimientoDeposito` (`services_deposito.py:138-157` y luego `:174-178`). Una
llamada externa no se deshace con el rollback:

- Con dos o más cargos con tarjeta, si el 1.º se reembolsa y el 2.º falla, se
  lanza `DepositoError` (`:152-153`), la transacción se revierte y no queda
  `DEVOLUCION` registrada: `en_custodia` sigue igual y reintentar vuelve a
  reembolsar el 1.º cargo.
- Con un solo cargo, `reembolsar_monto_openpay()` (`services_openpay.py:754-767`)
  no atrapa excepciones de `requests` (timeout, conexión). Si Openpay procesó
  el reembolso pero la respuesta no llegó, la excepción sube, revierte todo y
  `liquidar_view` (`comercial/admin.py:1687-1691`) solo atrapa `DepositoError`:
  el operador ve un error 500 y lo natural es volver a intentarlo.
- De paso: `'amount': float(monto)` (`services_openpay.py:758`) es un `float`
  en una ruta de dinero, contra el estándar del repo.

Toca `services_openpay.py` (zona restringida): requiere aprobación. Dirección
probable: registrar cada reembolso exitoso fuera del `atomic()` (mismo patrón
que el intento fallido de la firma) y tratar timeout como "estado desconocido".
**A confirmar con Openpay**: ¿un cargo admite más de un reembolso parcial y
existe una llave de idempotencia para `/refund`?

### R2 — El selector de habitaciones exhibe un precio por noche sin ISH — VERIFICADO (impacto legal A CONFIRMAR)

`api_habitaciones_cotizador` manda `precio_noche = con_iva(precio)`
(`comercial/views_cotizador.py:1125,1131`), sin ISH. El cotizador lo pinta como
precio por noche y subtotal (`cotizador/index.html:1133,1311-1312`). Pero
`calcular_totales()` sí suma el ISH (`comercial/models.py:1017-1023`). Con los
importes reales de la Memoria (2026-09-25), la tarjeta dice **$962.66 /noche**
y el total cobrado es **$1,000.00 por noche**. Lo mismo pasa con
`precio_persona_extra` de hospedaje (`views_cotizador.py:1142-1145`).

**Pregunta al abogado**: ¿un precio unitario exhibido sin el ISH, aunque el
total final sí lo incluya, cumple con la obligación de exhibir el monto total
con impuestos? Del lado del código el arreglo es exhibir `con_iva + ISH` con la
tasa vigente (lógica de precios: requiere aprobación).

### R3 — La retención de depósito "por servicio" es ingreso con IVA sin CFDI — VERIFICADO + A CONFIRMAR

`RETENCION_SERVICIO` genera una póliza con ingreso e IVA trasladado
(`contabilidad/signals.py:850-855`), pero `SolicitudFactura` solo nace de un
`Pago` (`facturacion/signals.py:67-68`); `facturacion/` no referencia el
depósito en ningún lado. Ese ingreso nunca llega al contador para timbrarlo,
y además no aplica retención de ISR a persona moral ni ISH si el servicio es
hospedaje (el cálculo solo vive en `calcular_totales()`).

**Pregunta al contador** (ya pendiente el trato de las retenciones, Watchlist):
¿la retención por servicio requiere CFDI propio, con qué concepto, y aplica
retención de ISR (persona moral) o ISH cuando el servicio fue hospedaje?

## Recomendaciones

### O1 — Confirmar la base del ISH antes de vender extras en Hospedaje

`calcular_totales()` calcula el ISH sobre toda la base de la cotización
(`comercial/models.py:996,1018-1019`), y el cotizador agrega a esa misma
cotización los extras con `cotizador_hospedaje=True`
(`views_cotizador.py:644-645`, `:864`). Un desayuno u otro extra paga ISH
igual que la habitación, y como la Quinta absorbe el ISH solo en las
habitaciones (precio recapturado), en los extras el ISH sube el precio al
cliente.

**Pregunta al contador**: ¿la base del ISH en Yucatán incluye alimentos y
servicios adicionales cobrados en la misma reserva, o solo el alojamiento? Si
es solo el alojamiento, acotar la base a las líneas `HABITACION_HOSPEDAJE`
(y persona extra) evita cobrar y enterar impuesto de más.
