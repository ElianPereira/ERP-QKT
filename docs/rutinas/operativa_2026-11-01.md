# Rutina Operativa/Contable — 2026-11-01

Solo análisis de estructura (código en `main` a7b9ce1). No se leyeron datos vivos
ni se modificó código. Se descartó todo lo ya resuelto o decidido en la Watchlist,
la Memoria de `CLAUDE.md` y `operativa_2026-09-05.md` (R3 de ese reporte, versión
del contrato, queda parcialmente cubierta por las huellas SHA-256 de `FirmaContrato`
y no se repite).

## Riesgos

### R1 — Al cancelar una reservación, el depósito en garantía se queda en custodia sin nadie que lo devuelva (VERIFICADO)

- `services_cancelacion.calcular()` (`comercial/services_cancelacion.py:305`) solo mira
  `total_pagado_neto()` y los cargos `destino='SERVICIO'` (`:290-295`). El depósito no es
  un `Pago` (Memoria 2026-09-25), así que no entra al reembolso ni al análisis que ve
  Dirección.
- `_ejecutar_cancelacion()` (`:531-606`) reembolsa la tarjeta, deja «Transferir $X» del
  servicio y cancela; no aparece `DepositoGarantia` en ningún punto del archivo, ni en
  `cambiar_estado('CANCELADA')` (`comercial/models.py:932`).
- El único camino automático de devolución es la inspección, y
  `depositos_para_inspeccion()` (`comunicacion/services_inspeccion.py:168-174`) filtra
  `cotizacion__estado__in=ESTADOS_SERVICIO_PRESTADO` = CONFIRMADA/EJECUTADA/CERRADA (`:45`).
  Una cotización CANCELADA nunca entra.
- El depósito se puede pagar desde que se genera el contrato (`asegurar_deposito`,
  `comercial/services_deposito.py:27`; el portal lo cobra en `views_portal.py:226-237`),
  o sea, antes del evento. Cliente que pagó depósito y luego cancela: recibe el aviso
  «cancelada, reembolso $X» sin el depósito, el dinero sigue en 205.03 y no se abre ningún
  pendiente. Solo se devuelve si alguien se acuerda y lo liquida a mano en el admin.
- Impacto: dinero del cliente retenido sin causa (el depósito garantiza daños de un
  servicio que ya no ocurrirá) → reclamo PROFECO y pasivo que no se concilia. Además, si
  se cobró con tarjeta, cuanto más tarde se detecte, más probable que Openpay ya no admita
  el reembolso al cargo de origen y haya que transferir.

### R2 — El ISH de la parte retenida al cancelar un Hospedaje se queda como pasivo (A CONFIRMAR)

- Cada pago de Hospedaje abona su parte proporcional de ISH a 208.04
  (`contabilidad/signals.py:229-236`); el reembolso solo revierte el ISH de lo devuelto
  (`:563`, `:605-610`).
- Para Hospedaje la penalización es un % de lo pagado, ISH incluido
  (`services_cancelacion.py:319-321`), y la póliza de penalización solo mueve Anticipos →
  Ingreso «el IVA no se mueve» (`signals.py:674`, `:735-744`); el ISH tampoco.
- Resultado: en una estancia cancelada con retención, la 208.04 conserva ISH por una
  estancia que nunca ocurrió. La Memoria 2026-10-23 fijó el criterio del **IVA** de la
  pena (art. 18 LIVA) pero no dice nada del ISH.
- **Pregunta para el contador**: «Cuando cancelan un hospedaje y retenemos una pena, ¿esa
  pena causa el Impuesto sobre Hospedaje de Yucatán (se entera lo que hoy queda en 208.04)
  o, como no hubo hospedaje, el ISH de la parte retenida debe revertirse y la pena
  quedar solo con IVA?» Según la respuesta, o no hay nada que cambiar, o la póliza de
  penalización debe mover también el ISH (toca `contabilidad/signals.py`, no
  `services.py`; requiere aprobación por ser impuestos).

## Recomendaciones

### O1 — Incluir el depósito en la solicitud de cancelación (cierra R1)

Que `calcular()` agregue `deposito_recibido` y `deposito_a_devolver` al análisis, que
la propuesta a Dirección lo muestre, y que `_ejecutar_cancelacion()` llame a
`services_deposito.liquidar()` sin retenciones (tarjeta → Openpay; SPEI/efectivo →
pendiente «Transferir», mismo criterio que el reembolso del servicio). Como respaldo para
cancelaciones hechas desde el admin, un pendiente diario para todo `DepositoGarantia`
con `recibido > 0`, no liquidado, cuya cotización esté CANCELADA o EXPIRADA. Toca
dinero y Openpay: requiere aprobación del propietario antes de implementar.

---
Sin más hallazgos nuevos: el resto de lo revisado (IVA incluido, ISH directo,
retención RESICO, nota de crédito, descuentos, depósito con retención) coincide con
lo ya decidido en la Memoria.
