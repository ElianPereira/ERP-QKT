# Plan v2 — segunda cuenta BBVA (pagadora) · ERP-QKT

> Issue: ElianPereira/ERP-QKT#499 · Sustituye al plan v1. Este documento ya está **verificado contra el código**
> (rama `main` al 14/11/2026, commit `a0a4ea5`). Quien lo implemente **no necesita re-auditar**: sigue los PR en orden.
> Responde en español y breve. Lee `CLAUDE.md` antes de empezar (estándares, Memoria).

## 0. Reglas de la sesión que implementa

- **Mueve dinero y contabilidad**: cada PR va como **draft** a revisión del propietario. **No** hagas merge.
- **Zonas restringidas: no se tocan.** El diseño de abajo NO requiere editar `comercial/views_openpay.py`,
  `comercial/services_openpay.py`, `legal/` ni `contabilidad/services.py`. Si crees que hace falta, detente y pregunta.
- No corras `migrate`/`dbshell` contra producción. Migraciones solo aditivas.
- No escribas CLABE ni número de cuenta completos en código, tests, commits ni PR: en tests usa datos ficticios;
  la cuenta real es «terminación 7930».
- Antes de cada push: `ruff check .`, `python manage.py check`, `python manage.py makemigrations --check --dry-run`
  y los tests de las apps tocadas con `DJANGO_DEBUG=True DJANGO_ALLOWED_HOSTS='*'`; antes de abrir el PR, la suite
  completa (comando en `CLAUDE.md`).
- Al final de cada PR, agrega una entrada en la **Memoria** de `CLAUDE.md` (formato existente) y corrige el párrafo
  «Cuenta bancaria: una sola…» de «Contexto del negocio».

## 1. Contexto de negocio (resumen)

- Cuenta actual BBVA Maestra PYME → QUINTA = **cobros** (clientes, Openpay y terminal depositan ahí). Nueva BBVA
  Maestra PYME (term. 7930) = **pagos** (pauta, suscripciones, proveedores, reembolsos, nómina), fondeada con
  transferencias propias desde la actual. Ya opera transferencias; **la tarjeta aún no llega** (mientras tanto se
  paga con la actual).
- **Requisito**: el rol es preferencia, nunca restricción. Cualquiera de las dos puede recibir o pagar.
- El estado de cuenta de la nueva llega por el mismo correo y formato.

## 2. Principios (cambios respecto al v1)

1. **El estado de cuenta manda.** El rol solo propone un valor por defecto al crear el registro; cuando el cargo o
   abono aparece en el estado de una cuenta, esa es la cuenta real y el ERP **reapunta** la línea de bancos si el
   default fue otro. Así un default equivocado (p. ej. compras pagadas con la cuenta actual mientras no llega la
   tarjeta) se corrige solo al conciliar, sin intervención.
2. **Cobro por defecto = `BANCO_PRINCIPAL`.** No se crea `cobro_por_defecto()`: `BANCO_PRINCIPAL` ya es la cuenta de
   cobro y debe seguir apuntando a la cuenta actual. `Pago.cuenta_bancaria` vacío ⇒ `BANCO_PRINCIPAL` (comportamiento
   de hoy). Así Openpay (zona restringida, crea `Pago` sin la FK) queda bien sin tocarlo.
3. **Pagadora por defecto = `CuentaBancaria.pagadora(unidad)`**: la única activa de esa unidad con rol `PAGO`; si no
   hay ninguna `PAGO`, la única activa (comportamiento de hoy con una sola cuenta); si hay ambigüedad, `None`.
4. **Traspasos propios con cuenta puente**, no emparejando dos puntas (ver PR 2). Cada punta se asienta sola,
   en cualquier orden, idempotente; saldo ≠ 0 de la puente = traspaso con una punta pendiente. Mismo patrón que
   106.02 «Comisiones Openpay por comprobar».
5. **El efectivo siempre es `CAJA`**; un `Pago`/`MovimientoDeposito` EFECTIVO con cuenta bancaria es error de captura.

## 3. Hallazgos verificados (severidad descendente)

| # | Sev. | Hallazgo | Acción |
|---|---|---|---|
| H1 | **Alta** | El v1 haría que durante la transición (sin tarjeta) toda compra nueva quede con `cuenta_pago` = pagadora aunque se pague desde la actual. `compras_candidatas()` (`contabilidad/services_estados_cuenta.py:697`) solo acepta `cuenta_pago` nula o **igual** a la del estado ⇒ esos cargos nunca se emparejan, la compra queda con póliza contra la cuenta equivocada y **ambas** conciliaciones descuadran. | PR 1, paso 5: candidatas de cualquier cuenta propia con línea sin emparejar + reapunte. |
| H2 | **Alta** | v1 §7 «acepta cualquier cuenta con rol que cobra» contradice el requisito: un cliente que pague a la pagadora se seguiría rechazando. | `_cuenta_inconsistente` acepta **cualquier** cuenta activa con `cuenta_contable`. |
| H3 | **Alta** | Fondeos ya ocurren hoy: el estado de la cuenta actual (nov.) puede tener esos cargos clasificados como `RETIROS_DUENO` por una regla (`aplicar_reglas`) o sin clasificar. Además un abono de fondeo en la pagadora podría caer en la **capa 3** de `identificar_abonos` (solo monto) y proponerse como pago de un cliente. | PR 2: paso de traspasos **antes** de `identificar_abonos`, y comando de reclasificación del histórico. |
| H4 | **Alta** | v1 §5 (devolución de depósito sale de la pagadora) es imposible sin modelo: `MovimientoDeposito` (`comercial/models.py:1653`) no tiene cuenta bancaria y el signal (`contabilidad/signals.py:809`) usa `BANCO_PRINCIPAL`. La migración del v1 no lo incluía. | FK también en `MovimientoDeposito`. |
| H5 | Media | Comisiones (`COMISION_TPV` L296, `COMISION_OPENPAY` L477) van contra `BANCO_PRINCIPAL` aunque el cobro se haya registrado en otra cuenta ⇒ `comisiones_por_cobro()`/depósitos agrupados no las encontrarían (filtran por la cuenta contable del estado). | La comisión sigue la cuenta de su cobro. |
| H6 | Media | `102.02.03` puede seguir ligada (OneToOne, PROTECT) a la `CuentaBancaria` de Revolut desactivada; `0033` solo desactivó. Si tiene movimientos aplicados, reutilizarla mezcla historia. | Verificación de solo lectura antes del alta (§6). |
| H7 | Media | Conciliación de pauta (`marketing/services/conciliacion.py:60`) **ya** suma cargos de todas las cuentas (no filtra por cuenta): v1 §11 sobra. Pero con dos cuentas, si llegó el estado de una y no de la otra, `hay_movimientos` es verdadero y reporta diferencias falsas. | Exigir estado del mes de **todas** las cuentas activas. |
| H8 | Media | Cambiar la cuenta de un `Pago`/`Compra` cuya línea de bancos ya está ligada a un `MovimientoEstadoCuenta` dejaría el renglón del banco apuntando a una línea de otra cuenta. | El servicio de reapunte rechaza si la línea está emparejada (salvo el reapunte que hace la propia conciliación). |
| H9 | Baja | Correo: identifica por `numero_cuenta` exacto (`services_estados_correo.py:341`) y prueba contraseñas de las cuentas activas; la nueva necesita su `numero_cuenta` tal como lo imprime el estado y su contraseña capturada. `avisar_si_no_llego` (L559) exige `banco` con «bbva». | Solo alta correcta en admin; sin código. |
| H10 | Baja | v1 cita `contabilidad/services.py ~L435` como «ya usa `cuenta_pago`»: correcto, pero eso es `completar_poliza_compra` (BORRADOR). La póliza de compra se emite en `contabilidad/signals.py::emitir_poliza_compra` (~L925), no restringido. | Ninguna. |
| H11 | Baja | Sin otros consumidores: `BANCO_PRINCIPAL` solo vive en `signals.py` (L118-122, 296, 391, 477, 543, 809) y `services_abonos.py:197`. `aplicar_reglas`, `crear_poliza_desde_movimiento`, `_emparejar_automaticamente`, `cobertura_por_deposito`, `analizar_conciliacion`, `reportes/services/finanzas.py` y `comunicacion/services_direccion.py` ya usan la cuenta del estado o recorren todas las activas. `BANCO_SECUNDARIO` no lo lee nadie (no usarlo). La regla «Depósito de efectivo a banco» funciona para cualquier cuenta. | Ninguna. |

Respuestas a las preguntas técnicas del v1: reglas de banco → usan la cuenta contable del estado (OK). Depósitos
agrupados → por cuenta; quedan bien si cobro y comisión van a la misma cuenta (H5). Caja→depósito → sí, cualquier
cuenta. `Pago` en portal → la FK no se expone (portal no renderiza campos del modelo). Concurrencia → `select_for_update`
en el reapunte (PR 1/PR 3).

## 4. PR 1 — Modelo, defaults y conciliación que reapunta (núcleo)

Rama: la que indique el propietario (si no, `claude/cuenta-pagadora-fase1`). Migraciones: `contabilidad.0044`,
`comercial.0125` (ambas aditivas).

1. **`CuentaBancaria.rol`** (`contabilidad/models.py:162`): `CharField(max_length=6, choices=[('AMBOS','Cobros y pagos'),
   ('COBRO','Cobros'),('PAGO','Pagos')], default='AMBOS', verbose_name='Uso por defecto')` con `help_text` claro de que es
   preferencia. Classmethod `pagadora(unidad_id)`:
   - activas de la unidad con `cuenta_contable` no nula;
   - si hay exactamente una con `rol='PAGO'` → esa; si hay varias `PAGO` → `None`;
   - si no hay `PAGO` y hay exactamente una activa → esa (no regresión); si no → `None`.
   - Una con `rol='COBRO'` nunca es pagadora salvo que sea la única activa.
2. **`Pago.cuenta_bancaria`** (`comercial/models.py:1334`) y **`MovimientoDeposito.cuenta_bancaria`** (L1653):
   `ForeignKey('contabilidad.CuentaBancaria', null=True, blank=True, on_delete=PROTECT, related_name='pagos' /
   'movimientos_deposito', verbose_name='Cuenta bancaria', help_text='Vacío = Banco principal')`.
   - `clean()`: error si `metodo` es EFECTIVO (o CONDONACION en Pago) y hay cuenta; error si la cuenta no está activa o
     no tiene `cuenta_contable`.
   - `save()` solo al crear (`not self.pk`), sin cuenta, **default de egreso**: `Pago` con `tipo='REEMBOLSO'` y
     `metodo='TRANSFERENCIA'`, y `MovimientoDeposito` `tipo='DEVOLUCION'` y `metodo='TRANSFERENCIA'` →
     `CuentaBancaria.pagadora(unidad QUINTA)`. Ingresos: no se asigna nada (⇒ `BANCO_PRINCIPAL`). Reembolsos de
     tarjeta/Openpay: nada (salen de donde entró el cobro).
3. **Resolver único en `contabilidad/signals.py`**:
   ```python
   def cuenta_banco_de(obj, metodo):
       """Cuenta contable de bancos de un Pago/MovimientoDeposito: CAJA si es efectivo, la de su cuenta bancaria,
       o BANCO_PRINCIPAL (comportamiento previo a la segunda cuenta)."""
   ```
   Reemplaza los `get_cuenta('BANCO_PRINCIPAL')` de: pago de cliente (L114-122, colapsar las tres ramas), ingreso extra
   (L388-391), reembolso (L540-543), depósito en garantía (L809), comisión TPV (L296 → la del `pago`) y comisión Openpay
   (L477 → la de `transaccion.pago` si existe; si no, `BANCO_PRINCIPAL`). La función de comisión Openpay vive en
   `signals.py` (no restringido); no toques el webhook.
4. **`Compra.save()`** (`comercial/models.py:~1872`): sustituir el bloque «única cuenta activa» por
   `CuentaBancaria.pagadora(self.unidad_negocio_id)`; mismas condiciones de entrada (nueva, con unidad, sin cuenta,
   proveedor no `no_se_paga_desde_banco`). Actualizar el comentario.
5. **Conciliación que reapunta (H1)** — en `contabilidad/services_estados_cuenta.py`:
   - `compras_candidatas(..., cuenta_bancaria=X)`: aceptar también compras con `cuenta_pago` = **otra cuenta propia
     activa de la misma unidad** cuya línea de bancos (de su póliza APLICADA) **no** esté emparejada; usar
     `linea_banco_de_compra(compra, compra.cuenta_pago)` para esas.
   - En `contabilidad/services_reglas_banco.py::emparejar_compras_ya_pagadas` (L290): si la única candidata tiene
     `cuenta_pago` ≠ cuenta del estado, llamar a `reapuntar_banco_de_compra(compra, cuenta_del_estado)` y luego ligar.
   - **Nuevo** `reapuntar_banco_de_compra(compra, nueva_cuenta, *, desde_conciliacion=False)` en
     `contabilidad/services_compras.py` (mismo patrón que `reclasificar_compra`: no cancela, solo cambia la cuenta de
     la línea de bancos): `transaction.atomic()` + `select_for_update` sobre la póliza; rechaza si la línea ya está
     emparejada con un `MovimientoEstadoCuenta` (salvo `desde_conciliacion`, que solo se usa cuando aún no lo está);
     rechaza si la póliza tiene fecha anterior al arranque cerrado (`cerrar_historico_contable`) — busca cómo se
     representa ese corte antes de inventar uno; actualiza `compra.cuenta_pago` con `update_fields`. Nunca toca
     importes.
   - `aplicar_sugerencias_compras` (BORRADOR) ya asigna la cuenta del estado: sin cambio.
6. **Abonos por folio (H2)** — `contabilidad/services_abonos.py`:
   - `_cuenta_inconsistente(cuenta_bancaria)`: solo exige `cuenta_bancaria.activa` y `cuenta_contable_id`. Mensaje y
     docstring nuevos. Quita la importación de `ConfiguracionContable` si queda sin uso.
   - `_crear_pago`: `Pago(..., cuenta_bancaria=bit.cuenta_bancaria)` **antes** de `save()` (el signal asienta al crear).
     `_linea_de_banco` ya busca contra `cuenta_bancaria.cuenta_contable`: sin cambio. Idempotencia por huella intacta.
7. **Admin**:
   - `CuentaBancariaAdmin`: `rol` en lista, filtro y formulario.
   - `PagoAdmin` (`comercial/admin.py:~1127`): `cuenta_bancaria` en el fieldset «Datos», **editable solo al crear**
     (en edición, `readonly`; el cambio posterior es PR 3). Lo mismo donde se capturen movimientos de depósito.
   - Validación (system check o `clean()` de `ConfiguracionContable`): `BANCO_PRINCIPAL` debe ser la `cuenta_contable`
     de una `CuentaBancaria` activa; advertencia en el admin de Bancos si `BANCO_PRINCIPAL` apunta a una con
     `rol='PAGO'`.
8. **Tests** (nuevo `contabilidad/test_cuenta_pagadora.py`; reutiliza fábricas de `test_abonos.py`/`tests.py`):
   - Una sola cuenta: compra recibe esa cuenta; cobros, reembolso, comisiones y depósito idénticos a hoy (no regresión).
   - `COBRO`+`PAGO`: compra → pagadora; cobro → `BANCO_PRINCIPAL`; reembolso por transferencia → pagadora;
     reembolso Openpay → principal; devolución de depósito por transferencia → pagadora; pólizas APLICADAS.
   - Dos `AMBOS`: compra sin cuenta, póliza BORRADOR; `aplicar_sugerencias_compras` la completa con la cuenta del estado.
   - Override: pago recibido en la pagadora → póliza contra su cuenta contable; comisión TPV en esa misma cuenta.
   - **H1**: compra con `cuenta_pago`=pagadora, cargo en el estado de la cuenta de cobro → línea reapuntada a la de cobro
     y ligada; la póliza conserva folio e importes; ambas conciliaciones (`analizar_conciliacion`) sin diferencia.
   - Reapunte rechazado si la línea ya está emparejada.
   - Abono con folio `COT-NNN` en la pagadora → `Pago` con `cuenta_bancaria` = pagadora, ligado; reproceso del estado
     no duplica (huella).
   - EFECTIVO con cuenta → `ValidationError`.

## 5. PR 2 — Traspasos entre cuentas propias (cuenta puente)

1. **Cuenta contable puente**: migración de datos aditiva que crea (si no existe) una subcuenta de activo
   circulante «Traspasos entre cuentas propias en tránsito» (propón el código siguiendo el catálogo; p. ej. bajo 102
   o 109 — consulta al propietario en el PR) y la `ConfiguracionContable` nueva `TRASPASOS_EN_TRANSITO` (agregar a
   `OPERACION_CHOICES`, `contabilidad/models.py:~820`).
2. **Detección** `es_traspaso_propio(mov) -> CuentaBancaria | None` en `services_reglas_banco.py`: el texto normalizado
   (`normalizar_texto_banco(descripcion + referencia)`) contiene el `numero_cuenta` o la CLABE (o sus últimos 10
   dígitos) de **otra** `CuentaBancaria` activa (nunca la del propio estado). Si el formato real de BBVA no trae el
   número, el propietario podrá crear una `ReglaConciliacion` con operación `TRASPASOS_EN_TRANSITO` (fallback sin
   código); deja el campo listo en el admin de reglas.
3. **Asiento** `asentar_traspasos_propios(estado_cuenta, usuario)`: para cada movimiento sin asiento que sea traspaso,
   `crear_poliza_desde_movimiento(mov, cuenta_puente, usuario, aplicar=True, concepto='Traspaso entre cuentas propias')`
   (ya es idempotente por movimiento y usa la cuenta del estado). Cargo ⇒ DEBE puente / HABER banco; abono ⇒ al revés.
   Cero gasto, cero ingreso.
4. **Orden** en `emparejar_y_asentar` (`services_estados_cuenta.py:126`): `vincular_polizas_propias` →
   `_emparejar_automaticamente` → **`asentar_traspasos_propios`** → `identificar_abonos` → …; y en
   `services_abonos._regla_que_lo_cubre` excluir también lo que `es_traspaso_propio` detecte (así la capa 3 nunca
   propone un fondeo como pago de cliente).
5. **Histórico** — comando `reclasificar_traspasos_propios [--desde AAAA-MM-DD] [--aplicar]` (simula por defecto): para
   movimientos detectados como traspaso cuya póliza BANCO vigente usa `RETIROS_DUENO`/`APORTACIONES_DUENO`/
   `PARTIDAS_POR_IDENTIFICAR`, reapunta esa línea a la puente (sin cancelar ni cambiar importes, como
   `reclasificar_compra`). Lista lo que no pudo resolver.
6. **Indicador**: el saldo de la puente al cierre se muestra en el reporte de conciliación (o como aviso en el admin de
   Bancos): ≠ 0 = traspaso con una punta pendiente.
7. **Tests**: cargo en A y abono en B (cualquier orden y en meses distintos) ⇒ puente en 0, cero en cuentas de
   resultados; una sola punta ⇒ puente ≠ 0; reprocesar el estado no duplica; un abono de fondeo no crea bitácora de
   abono ni propuesta; reclasificación simulada no escribe y `--aplicar` sí.

## 6. PR 3 — Cambio manual de cuenta y transversales

1. **Servicio** `cambiar_cuenta_bancaria(obj, nueva, usuario)` (Pago o MovimientoDeposito; para Compra reutiliza
   `reapuntar_banco_de_compra`): atómico, `select_for_update`, reapunta la línea de bancos de su póliza (y la de su
   comisión TPV si existe), rechaza si alguna línea ya está ligada a un renglón del estado («desliga primero en el
   estado de cuenta»), deja nota en `notas` con usuario y fecha. Admin: acción «Cambiar cuenta bancaria» con
   `confirmar_accion_destructiva` (`core_erp/admin_utils.py`) y formulario intermedio con `qkt_ui.css`.
2. **Marketing (H7)**: en `conciliacion.cargos_bancarios`, `hay_movimientos` solo es verdadero si existe
   `EstadoCuentaBancario` del mes para **cada** `CuentaBancaria` activa; si falta alguno, no evalúa (mismo criterio
   «sin estado del mes no evalúa»). Test con dos cuentas y un solo estado.
3. **Tests** de la acción: cambio con póliza aplicada sin conciliar ⇒ reapunta; ya conciliada ⇒ rechaza sin escribir.

## 7. Alta operativa (la hace el propietario, después del deploy del PR 1; no por migración)

1. Verificación de solo lectura (Railway, sin escribir): ¿`102.02.03` tiene `MovimientoContable` en pólizas aplicadas?
   ¿Está ligada a una `CuentaBancaria` (Revolut) desactivada? Si tiene movimientos → crear `102.02.04` «BBVA Maestra
   PYME Pagos»; si no y está ligada → desligarla de la de Revolut, reactivarla y renombrarla.
2. Admin → Bancos: nombre, banco «BBVA», CLABE, `numero_cuenta` (10 dígitos, confirmar contra el primer estado),
   cuenta contable, unidad **QUINTA**, `saldo_inicial` 0 con fecha de apertura, rol **PAGO**, contraseña del estado.
   Cuenta actual: rol **COBRO**. `BANCO_PRINCIPAL` sigue en la actual.
3. Tras el deploy del PR 2: `reclasificar_traspasos_propios --desde <fecha del primer fondeo>` (simular, revisar, aplicar).
4. Si la cuenta nueva tuvo movimientos antes del alta, cargar su(s) estado(s) de cuenta a mano («Subir correo» o carga
   manual).

## 8. Migraciones (corren solas en el deploy al mergear)

- PR 1: `contabilidad.0044` (AddField `rol`), `comercial.0125` (AddField `cuenta_bancaria` en `Pago` y
  `MovimientoDeposito`). Aditivas, sin backfill: lo existente sigue contra `BANCO_PRINCIPAL`.
- PR 2: migración de datos aditiva (cuenta puente + `ConfiguracionContable`), `get_or_create`, sin tocar registros
  existentes. Ninguna borra ni reescribe datos.

## 9. Preguntas para el propietario (no bloquean el PR 1)

1. ¿`102.02.03` reactivada o `102.02.04` nueva? (decide el resultado de §7.1).
2. Código y nombre de la cuenta puente de traspasos (confirmar con el contador).
3. Nombres en el ERP: propuesta «BBVA Maestra PYME — Cobros» / «— Pagos».
4. Confirmar: Openpay y terminal siguen depositando en la cuenta actual (el diseño lo asume; si cambia, solo hay que
   apuntar `BANCO_PRINCIPAL` a la otra).

## 10. Riesgos

- Formato del concepto de un SPEI BBVA→BBVA desconocido: calibrar `es_traspaso_propio` con el primer estado que traiga
  un fondeo; hasta entonces cae a «sin regla» (visible) o a la regla manual del fallback, nunca a pago de cliente.
- Un reapunte por conciliación cambia la cuenta de una póliza ya aplicada en un periodo ya conciliado de la otra
  cuenta: por eso solo se permite cuando la línea **no** está emparejada en ningún estado.
- Nómina: hoy no genera pólizas (signal retirado 13/07); si se reactiva, deberá usar `pagadora()`.
