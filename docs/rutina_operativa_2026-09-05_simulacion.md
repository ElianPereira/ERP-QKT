# Simulación de los 6 casos del reporte — 2026-09-05

Anexo de `rutina_operativa_2026-09-05.md`. Cada riesgo y cada oportunidad de
ese reporte se reprodujo **contra el ERP corriendo de verdad**
(`manage.py runserver` + Chromium vía Playwright), no leyendo código: el
cotizador público se llenó y se envió como lo haría un cliente, y el admin se
operó pasando por el gate de 2FA como cualquier superusuario.

## Cómo se montó

| Elemento | Valor |
|---|---|
| Django | 6.1 (Python 3.12), SQLite local desechable |
| Navegador | Chromium headless (Playwright), viewport 1400×1100 |
| Datos | Sembrados para la prueba (2 habitaciones, 1 paquete, 1 insumo, 1 regla de cortesía). **Ningún dato vivo de producción.** |
| Autenticación | Usuario superusuario de prueba + TOTP real, tecleando los 6 dígitos en la pantalla de verificación |

Tres desviaciones del entorno, ninguna afecta la lógica evaluada:

1. **flatpickr** (calendario del cotizador) se sirve desde un CDN bloqueado por
   la política de red del sandbox, así que la fecha se escribió en el mismo
   `<input id="fecha">` que la app lee (`dateFormat: 'Y-m-d'`).
2. **Cloudflare R2** no tiene credenciales aquí y `STORAGES` no trae fallback a
   disco, así que el contrato falló con `Invalid endpoint` hasta correr el
   servidor con un settings local que guarda los archivos en el filesystem. El
   PDF se generaba bien; lo que faltaba era dónde ponerlo.
3. Los documentos legales del seed nacen `vigente=False`; hubo que publicarlos
   para que el registro de consentimiento funcionara (paso manual normal, no un
   defecto).

---

## R1 — ISH: ausente en hospedaje directo, y capturado pero no contabilizado en Airbnb

### R1a · Hospedaje directo (cotizador público)

Flujo completo de un cliente: Hospedaje → 2 noches (20–22 oct 2026) →
Habitación Ka'an → datos de contacto y fiscales → envío.

Lo que ve el cliente en el resumen y lo que queda guardado:

```
Total estimado          $2,399.99
· Habitación Ka'an (2 noches, check-in 2:00 p.m. — check-out 10:00 a.m.)
Precios en MXN, IVA incluido.

Cotización creada:  subtotal=2068.96  iva=331.03  ret_isr=0.00  precio_final=2399.99
Campos con "ISH"/"hospedaje" en el modelo Cotizacion: []
```

Precio y desglose salen completos **sin una sola línea de impuesto al
hospedaje**: el modelo no tiene dónde ponerlo. Capturas:
[`r1a_hospedaje_2_resumen.png`](img/simulacion_2026-09-05/r1a_hospedaje_2_resumen.png).

### R1b · Airbnb (póliza generada por el signal)

Se registró un `PagoAirbnb` con `impuesto_hospedaje = $100.00`. El signal emitió
su póliza y quedó APLICADA y cuadrada — con estas seis líneas:

| Cuenta | Nombre | Debe | Haber |
|---|---|---|---|
| 102.02.01 | BBVA Principal | 2,020.00 | — |
| 109.03 | ISR retenido por Airbnb | 80.00 | — |
| 109.04 | IVA retenido por Airbnb | 160.00 | — |
| 601.04.02 | Comisiones Airbnb | 60.00 | — |
| 401.02.01 | Hospedaje Habitación 1 | — | 2,000.00 |
| 208.01 | IVA trasladado | — | 320.00 |

**Ninguna cuenta 208.04.** El ISH de $100.00 quedó guardado en el registro del
pago y no llegó a la contabilidad: la cuenta "Impuesto estatal al hospedaje"
existe en `ConfiguracionContable` desde la migración `0005` y nunca se usa.
Captura: [`r1b_poliza_airbnb_sin_ish.png`](img/simulacion_2026-09-05/r1b_poliza_airbnb_sin_ish.png).

---

## R2 — La retención de ISR ignora el régimen fiscal del emisor

Dos envíos idénticos por el cotizador, cambiando solo el RFC:

| RFC capturado | Longitud | `tipo_persona` detectado | Retención ISR |
|---|---|---|---|
| XAXX010101000 | 13 | FISICA | $0.00 |
| ABC010101AB1 | 12 | MORAL | **$25.86** (1.25% de 2,068.96) |

La detección automática por longitud de RFC funciona como está documentada. La
prueba de fondo es la siguiente: en el admin real se cambió el régimen fiscal de
la unidad de negocio **QUINTA de `612` a `606 - Arrendamiento`** y se guardó
correctamente (captura [`r2_1_regimen_cambiado.png`](img/simulacion_2026-09-05/r2_1_regimen_cambiado.png)); al recalcular la
cotización de la persona moral:

```
régimen del emisor: 612 → 606 (Arrendamiento)
retención ISR de COT#4: 25.86 → 25.86
```

El importe **no se movió un centavo**. Bajo arrendamiento, una persona moral
debe retener 10% de ISR y 2/3 del IVA (arts. 106 LISR / 1-A LIVA), no 1.25%: el
cálculo aplica RESICO por diseño, sin leer nunca `UnidadNegocio.regimen_fiscal`.
Mientras el régimen real siga sin confirmarse, no hay forma de saber si las
cotizaciones a persona moral llevan la retención correcta.

---

## R3 — El contrato no guarda la versión del texto firmado

Con la cotización ya CONFIRMADA (anticipo del 50% registrado), se generó el
contrato real desde el ERP: **CONT-2026-0001**, PDF incluido, depósito en
garantía $1,000.00.

Sus campos, tal como los muestra el admin (captura [`r3_2_contrato_admin.png`](img/simulacion_2026-09-05/r3_2_contrato_admin.png)):

```
id, cotizacion, numero, tipo_servicio, deposito_garantia, archivo,
generado_por, generado_en, enviado_email, notas
→ campos de versión o FK a DocumentoLegal: NINGUNO
```

El contraste con lo que el mismo repo sí sabe hacer, medido en la misma corrida:

```
AceptacionLegal #1 fisica@example.com snapshot=
  [{'tipo': 'AVISO_PRIVACIDAD',      'version': '2.3', 'hash': '8caac951912f…'},
   {'tipo': 'POLITICA_CANCELACION',  'version': '2.0', 'hash': '9785e2dbb43f…'},
   {'tipo': 'TERMINOS',              'version': '2.1', 'hash': 'f4d18ba26d56…'}]

ContratoServicio CONT-2026-0001 → atributos de versión/hash: ninguno
```

El consentimiento del cotizador queda probado con versión y hash SHA-256 por
documento; el contrato de adhesión —el que rige la operación y el que PROFECO
pediría acreditar— solo deja un PDF suelto.

---

## O1 — El costo se lee en vivo: el margen histórico se mueve solo

`ItemCotizacion` guarda `cantidad` y `precio_unitario`, nada de costo:

```
campos de ItemCotizacion: id, cotizacion, producto, insumo, descripcion,
                          cantidad, precio_unitario
```

Se subió el costo del insumo de $60.00 a $120.00 desde el admin
(captura [`o1_1_insumo_actualizado.png`](img/simulacion_2026-09-05/o1_1_insumo_actualizado.png)) y, sin tocar ningún producto ni ninguna
cotización:

```
insumo $60.00  → costo del producto $120.00
insumo $120.00 → costo del producto $240.00
```

El costo de todo lo vendido en el pasado se duplicó con un solo cambio de
catálogo. Cualquier margen calculado hoy sobre eventos ya ejecutados es el
margen a precios de hoy, no el que dejó el evento.

---

## O2 — La cortesía se audita completa y ningún reporte la consume

Se aplicó la regla "Cortesía Dirección" (15%, `es_cortesia=True`) pulsando el
botón **Aplicar** de la pantalla real de descuentos
(capturas `o2_0_pantalla_descuentos.png` y [`o2_1_cortesia_aplicada.png`](img/simulacion_2026-09-05/o2_1_cortesia_aplicada.png)).
Lo que quedó registrado:

```
DescuentoAplicado: cotización 4 · "Cortesía Dirección (sim)" · $310.34 ·
                   15.00% equivalente · MANUAL · es_cortesia=True · activo=True
Cotización: descuento=$310.34 → precio_final de $2,374.13 a $2,018.02
```

Es un registro de auditoría completo: monto, porcentaje equivalente, modo,
autor, fecha y si fue cortesía o promoción. Acto seguido, el Centro de Reportes
(`/admin/reportes/`, sesión iniciada — captura [`o2_2_selector_reportes.png`](img/simulacion_2026-09-05/o2_2_selector_reportes.png)):

```
¿menciona "descuento"? False
¿menciona "cortesía"?  False
```

Los diez reportes que ofrece son balanza, estado de resultados, balance general,
libro mayor, auxiliar, cartera (CxC), cotizaciones, ocupación, comparativo
Airbnb y facturas. El ingreso cedido está medido al centavo en la base y no
aparece en ninguna salida: hoy no hay forma de contestar "¿cuánto regalamos este
mes y bajo qué regla?" sin consultar la base a mano.

---

## O3 — Las transiciones de estado no dejan fecha

Se avanzó la cotización por su máquina de estados real:

```
BORRADOR → COTIZADA:   True | Estado cambiado a 'Cotización Enviada'
COTIZADA → CONFIRMADA: True | Estado cambiado a 'Venta Confirmada'
estado final=CONFIRMADA   created_at=2026-09-05 16:48:25   updated_at=2026-09-05 16:57:04
```

Campos de fecha del modelo: `fecha_evento`, `fecha_salida`, `fecha_cancelacion`,
`created_at`, `updated_at`. El único que fecha una transición es
`fecha_cancelacion`, y no existe ningún modelo de historial de estados en todo
el repo. `updated_at` no sirve de sustituto: lo pisa cualquier edición posterior
—de hecho ya lo hizo en esta misma corrida—. La ficha de la cotización confirmada
(captura [`o3_cotizacion_confirmada_sin_fecha.png`](img/simulacion_2026-09-05/o3_cotizacion_confirmada_sin_fecha.png)) no muestra en ninguna parte
cuándo se confirmó.

Sin ese dato no se puede calcular tasa de conversión, tiempo de cierre ni tasa
de cancelación por período, y es información que se pierde en el momento en que
no se registra.

---

## Qué queda igual que en el reporte

La simulación no cambió ninguna conclusión: confirmó las seis. Las tres
decisiones que siguen pendientes del propietario y del contador —sujeción al
ISH, régimen fiscal real del emisor, alcance del registro PROFECO— siguen
siendo requisito antes de tocar una línea de código en esas zonas.
