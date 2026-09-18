# ISH — Impuesto Sobre Hospedaje

Qué hace el ERP con el impuesto estatal al hospedaje, qué falta para
activarlo y qué decisión de negocio queda pendiente.

## Resumen en tres líneas

1. **Airbnb no requiere nada**: la plataforma retiene y entera el ISH por su
   cuenta. El ERP solo lo guarda como dato informativo.
2. **El hospedaje vendido directo sí lo causa** y hasta ahora no lo cobraba.
   Ya está implementado, pero **apagado** hasta que se defina la tasa.
3. Para encenderlo: definir `TASA_ISH` en Railway. Nada más.

---

## Por qué Airbnb queda fuera

La columna del CSV de Airbnb se llama literalmente **"Impuesto liquidado por
Airbnb"** (`Taxes withheld by Airbnb`). Ese importe:

- lo cobra Airbnb al huésped,
- lo retiene y lo entera Airbnb al estado,
- **no llega al depósito** que Airbnb hace a la cuenta de la Quinta.

Por eso `PagoAirbnb.impuesto_hospedaje` es informativo —cuánto enteró la
plataforma a nombre de la Quinta— y **no** se contabiliza como pasivo: si se
abonara a la cuenta 208.04 se crearía una deuda con el estado que en realidad
ya está pagada, y que nadie podría conciliar después.

Esto ya era así antes de este cambio y sigue igual; está documentado en
`airbnb/models.py::diferencia_neto` y en
`contabilidad/signals.py::_asiento_pago_airbnb`.

## Qué cambió: el hospedaje directo

Una reserva de Ka'an u Otoch contratada por el cotizador (sin Airbnb de por
medio) la vende la Quinta directamente, así que es la Quinta quien causa y
debe enterar el ISH. Antes no se calculaba en ninguna parte.

Ahora, **solo** en cotizaciones con `tipo_servicio='HOSPEDAJE'` y **solo** si
hay tasa configurada:

| Paso | Qué ocurre |
|---|---|
| Cotizador público | El total exhibido incluye el ISH; la leyenda pasa a "IVA e ISH incluidos" |
| `Cotizacion` | Campo nuevo `impuesto_hospedaje`; `precio_final = base + IVA + ISH − retenciones` |
| Cada pago | El desglose proporcional reparte la parte de ISH que le toca a ese abono |
| Póliza | El ISH se abona a la cuenta **208.04** (pasivo por enterar), no a ingresos |
| Reembolso | Se reversa también el ISH: si el huésped no se hospeda, el impuesto deja de deberse |

La base del ISH es la contraprestación **sin IVA**: el impuesto estatal no se
calcula sobre el impuesto federal.

## Cómo se activa

Una sola variable de entorno en Railway, expresada como **proporción, no
porcentaje**:

```
TASA_ISH=0.05     # 5%
```

- Mientras valga `0` (el default) el ERP se comporta exactamente como antes:
  no calcula ISH, no lo exhibe y no lo contabiliza.
- Un valor mayor que 1 se rechaza con error al calcular, para que un `5` mal
  puesto en vez de `0.05` no cobre 500% de impuesto en silencio.
- Cambiarla no requiere desplegar código, solo reiniciar el servicio.

La tasa **la confirma el contador**: el ISH es ley estatal (Yucatán), cambia
sin que este repositorio se entere, y por eso no está escrita en el código
como sí lo está el 16% del IVA federal.

### Encenderla no toca lo ya cotizado

La tasa se **sella en cada cotización** (`Cotizacion.tasa_ish_aplicada`) al
crearla, y se puede re-sellar mientras siga en BORRADOR. A partir de ahí el
cálculo usa esa tasa sellada, no la de la configuración.

Sin ese sello, encender `TASA_ISH` le habría subido el precio a cualquier
cotización anterior en cuanto alguien la reguardara —editarla en el admin o
moverla a EJECUTADA basta—, y a una **ya pagada** le habría reabierto saldo
por el importe del impuesto:

```
ANTES    precio_final = 1,160.00 | ISH = 0.00  | saldo = 0.00   (pagada)
DESPUÉS  precio_final = 1,210.00 | ISH = 50.00 | saldo = 50.00  (saldo fantasma)
```

Con el sello, esa misma cotización se queda en $1,160.00 y saldo cero para
siempre. Consecuencia buscada: las cotizaciones que ya estaban COTIZADA o
CONFIRMADA cuando se encienda la tasa **no** cobrarán ISH. Si hiciera falta
cobrárselo a alguna, hay que rehacerla — no basta con reguardarla.

## La decisión que falta, y que no es técnica

Al activar la tasa, el ISH **se suma** al precio que ve el cliente. Con una
habitación de $1,160.00 (IVA incluido) y una tasa del 5%:

- **Hoy (ISH apagado):** el huésped paga $1,160.00.
- **Con ISH activo:** el huésped paga $1,210.00 — la Quinta recibe lo mismo y
  los $50.00 se enteran al estado.

Se eligió sumarlo porque el precio exhibido al consumidor debe llevar todos
los impuestos incluidos (art. 7 BIS de la LFPC), y porque el ISH es un
impuesto trasladable al huésped.

**La alternativa —no subir el precio y absorber el ISH— no requiere tocar
código**: basta recapturar el `precio_venta_fijo` de cada habitación en el
admin para que el total con ISH vuelva a dar el precio de siempre. En ese
caso el ingreso neto de la Quinta baja en el importe del impuesto.

Esa es una decisión comercial del propietario, no del ERP.

## Pendientes tras activarlo

- Avisar al contador: aparecerá saldo en la cuenta 208.04 que hay que enterar
  con la periodicidad que marque la ley estatal.
- Revisar si la solicitud de factura al contador debe mencionar el ISH: hoy
  `SolicitudFactura` no lo desglosa, porque el ISH es estatal y no viaja en
  el CFDI como un traslado federal.
