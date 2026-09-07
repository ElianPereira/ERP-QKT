# Rutina Operativa/Contable — 2026-09-05

Análisis **solo de estructura** (campos y métodos de los modelos de Cotizaciones,
Cobranza, Reservas y Contabilidad). No se leyeron datos vivos ni se modificó
código. Todo lo que sigue es sugerencia estratégica para revisión del
propietario, no una implementación.

Alcance revisado: `comercial/models.py` (Cotizacion, ItemCotizacion, Pago,
PlanPago, ParcialidadPago, ContratoServicio, Descuento, DescuentoAplicado),
`contabilidad/models.py` + `contabilidad/signals.py`, `airbnb/models.py`,
`facturacion/models.py`, `legal/models.py`, `core_erp/impuestos.py`,
`reportes/services/*.py`.

---

## Riesgos

### R1 — ISH: el hospedaje directo no lo calcula y el de Airbnb no se contabiliza (fiscal)

`Cotizacion.calcular_totales()` (`comercial/models.py:791`) solo produce
`subtotal`, `iva`, `retencion_isr`, `retencion_iva` y `precio_final`. No existe
ningún campo ni cálculo de Impuesto al Hospedaje en `Cotizacion`, pese a que
`tipo_servicio='HOSPEDAJE'` ya es una línea de negocio real, con `fecha_salida`,
`noches` y habitaciones (`rol_cotizador='HABITACION_HOSPEDAJE'`).

En paralelo, `PagoAirbnb.impuesto_hospedaje` (`airbnb/models.py:259`) sí captura
el ISH del CSV, y `ConfiguracionContable` tiene la cuenta `IMPUESTO_HOSPEDAJE`
(208.04) dada de alta desde la migración `0005` — pero
`_asiento_pago_airbnb()` (`contabilidad/signals.py:671`) **no la usa en ninguna
línea de la póliza**: solo mueve banco, ingreso, retenciones, comisión e IVA
trasladado. El importe se guarda como dato y nunca llega a la contabilidad.

Efecto: para hospedaje directo no hay base para enterar un impuesto estatal que
el negocio probablemente causa, y para Airbnb el pasivo por ISH no aparece en
ningún saldo ni reporte. Ya está en la watchlist como "ISH Airbnb sin resolver";
esta revisión añade que el hueco es **más amplio que Airbnb**.

Decisión humana previa a cualquier implementación: confirmar con el contador si
QKT es sujeto del ISH de Yucatán por hospedaje directo y a qué tasa. Es cálculo
de impuestos → zona que requiere aprobación explícita.

### R2 — La retención de ISR asume RESICO sin consultar el régimen del emisor (fiscal)

`calcular_totales()` aplica `impuestos.ret_isr_de(base)` (1.25%, art. 113-J LISR)
en cuanto `cliente.tipo_persona == 'MORAL'`, sin mirar bajo qué régimen emite la
unidad de negocio. `UnidadNegocio.regimen_fiscal` (`contabilidad/models.py:139`)
existe y se llena, pero **ningún cálculo lo consulta**: un grep del campo solo
lo encuentra en admin, catálogo SAT y facturación, nunca en la ruta de dinero.

El propio `core_erp/impuestos.py` documenta que el 1.25% "corresponde al RFC
PECE010202IA0" bajo RESICO, y la watchlist registra ese régimen como **sin
confirmar** ("RESICO vs. arrendamiento"). Si el régimen real fuera
arrendamiento, una persona moral tendría que retener 10% de ISR y 2/3 del IVA
(arts. 106 LISR / 1-A LIVA), no 1.25% y cero IVA — desviación grande, en todas
las cotizaciones a persona moral ya emitidas. Además, desde el 2026-09-03 el
cotizador público infiere `MORAL` automáticamente por longitud de RFC, así que
esta rama se activa sola, sin que nadie la revise caso por caso.

No tocar el factor sin la confirmación del régimen; lo accionable hoy es cerrar
esa pregunta con el contador y, después, hacer que el cálculo lea
`UnidadNegocio.regimen_fiscal` en vez de asumirlo.

### R3 — El contrato de adhesión no guarda la versión del texto firmado (legal/PROFECO)

`ContratoServicio` (`comercial/models.py:1143`) dice en su docstring "Guarda
historial: versión, quién lo generó y cuándo", pero sus campos son `numero`,
`tipo_servicio`, `deposito_garantia`, `archivo`, `generado_por`, `generado_en`,
`enviado_email`, `notas`: **no hay campo de versión ni FK a `DocumentoLegal`**.

El contraste con el propio repo es la señal: `AceptacionLegal`
(`legal/models.py:190`) sí guarda `documentos`, `snapshot_documentos` (JSON),
`hash_contenido` por versión, IP y user-agent — evidencia sólida de qué texto
aceptó el cliente en el cotizador. El contrato de servicio, que es el documento
de adhesión que de verdad rige la operación, queda solo como PDF sin ancla a la
plantilla que lo generó. Si la plantilla cambia, no hay forma de acreditar qué
condiciones aplicaban a un contrato viejo salvo abrir el PDF a mano.

Esto se cruza con el pendiente de watchlist "Registro PROFECO NOM-174": el
trámite de contrato de adhesión exige poder acreditar el texto registrado
vigente en cada operación, y hoy el ERP no lo sabe decir por consulta.

---

## Recomendaciones

### O1 — Snapshot de costo en `ItemCotizacion` para tener margen real por evento

`ItemCotizacion` guarda `cantidad` y `precio_unitario`, pero ningún costo.
El costo se calcula en vivo con `Producto.calcular_costo()` →
`SubProducto.costo_insumos()` → `Insumo.costo_unitario`, un campo **mutable**:
cuando sube el precio de un insumo, el margen histórico de todos los eventos
pasados cambia retroactivamente. Hoy el único reporte de rentabilidad es el
Estado de Resultados (`reportes/services/contabilidad.py`), agregado por cuenta
contable — nunca por evento, servicio ni producto.

Con un `costo_unitario_snapshot` congelado al guardar la línea (mismo criterio
de inmutabilidad que ya usa `DescuentoAplicado`) se desbloquean margen por
cotización, por tipo de servicio y por producto, sin recalcular nada. Es el
insumo que falta para saber si Pasadía Premium a $3,000 deja más que Básico a
$2,000, o si el arrendamiento de mobiliario sostiene su precio.

### O2 — Explotar `DescuentoAplicado`: hoy se audita y no se reporta

`DescuentoAplicado` ya registra `monto_aplicado`, `porcentaje_equivalente`,
`modo_aplicacion`, `aplicado_por`, `fecha_aplicacion`, `activo` y, vía la regla,
`es_cortesia`. Es un dataset de fuga de ingreso completo y limpio — y **ningún
PDF ni Excel de `reportes/` lo consume** (ya se había verificado con grep en la
sesión del 2026-08-28; sigue igual).

Un reporte de descuentos por período respondería, sin desarrollo de modelo:
cuánto se regaló como cortesía vs. cuánto se cedió como promoción comercial,
qué reglas se llevan el grueso del descuento, y quién las aplica. Es la
diferencia entre "vendimos X" y "vendimos X cediendo Y".

### O3 — Fechar las transiciones de estado para poder medir conversión

`cambiar_estado()` (`comercial/models.py:625`) valida bien la máquina de estados
pero solo persiste evidencia de la cancelación (`motivo_cancelacion`,
`cancelada_por`, `fecha_cancelacion`). No hay `fecha_confirmacion`, ni un modelo
de historial: `updated_at` se pisa con cualquier edición posterior. No existe
ningún `HistorialEstado*` en el repo.

Consecuencia: hoy no se puede calcular **tasa de conversión** (COTIZADA →
CONFIRMADA), ni tiempo de cierre, ni tasa de cancelación por período o por canal
de origen, aunque `Cliente.origen` ya clasifica de dónde llegó el cliente. Es el
KPI más caro de reconstruir después, porque el dato se pierde en el momento en
que no se registra. Un modelo mínimo de bitácora de transiciones (estado
anterior, nuevo, usuario, timestamp), escrito desde el propio `cambiar_estado()`,
lo cierra de una vez para todos los reportes futuros.

---

## Notas de método

- No se ejecutó ningún query contra datos vivos; el análisis es sobre
  definiciones de modelo, signals y servicios.
- No se modificó código. R1 y R2 tocan cálculo de impuestos y R3 documentos
  legales: las tres son zonas que, según `CLAUDE.md`, requieren aprobación
  humana explícita antes de implementar.
- Las decisiones de negocio pendientes (sujeción al ISH, régimen fiscal del
  emisor, alcance del registro PROFECO) no se asumen aquí: quedan planteadas
  como preguntas al propietario/contador.
