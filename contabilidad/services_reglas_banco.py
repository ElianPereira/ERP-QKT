"""
Asientos generados desde el estado de cuenta (Issue #329)
==========================================================
`_emparejar_automaticamente()` solo encuentra asientos que YA existen. Buena
parte de la actividad bancaria no tiene documento en el ERP que genere su
póliza: traspasos del dueño, comisiones de BBVA, gastos con tarjeta sin CFDI.
Este módulo cierra ese hueco con `ReglaConciliacion`:

1. Siempre corre DESPUÉS del emparejamiento contra lo existente (Pago, Compra,
   pólizas manuales), así un movimiento que ya tiene asiento nunca recibe otro.
2. Al primer movimiento sin asiento que calce con una regla activa le crea una
   póliza origen BANCO ligada al propio MovimientoEstadoCuenta (idempotente),
   y si la regla se aplica sola, lo empareja con la línea de bancos.
3. Clasificar a mano con «recordar» crea una regla APRENDIDA con una clave
   estable (RFC impreso → cuenta del tercero → comercio de la tarjeta), así el
   mismo movimiento del mes siguiente ya no necesita a nadie.
"""
import logging
import re
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Sum

from .models import (
    ConfiguracionContable,
    MovimientoContable,
    MovimientoEstadoCuenta,
    Poliza,
    ReglaConciliacion,
    UnidadNegocio,
    normalizar_texto_banco,
)
from .services_compras import reclasificar_compra

logger = logging.getLogger(__name__)

PRIORIDAD_APRENDIDA = 50

RFC_RE = re.compile(r'RFC: ?[A-Z&]{3,4} ?\d{6}[A-Z0-9]{3}')
CUENTA_BNET_RE = re.compile(r'BNET (\d{10})\b')
CLABE_RE = re.compile(r'\b00(\d{18})\b')
MARCA_TARJETA = '******'


def usuario_sistema():
    from .signals import get_usuario_sistema
    return get_usuario_sistema()


def _poliza_del_movimiento(movimiento):
    """Póliza BANCO vigente (no cancelada) ya creada para este movimiento."""
    return Poliza.objects.filter(
        content_type=ContentType.objects.get_for_model(MovimientoEstadoCuenta),
        object_id=movimiento.pk,
        origen='BANCO',
    ).exclude(estado='CANCELADA').first()


def _vincular(movimiento, poliza, cuenta_banco):
    return _vincular_linea(movimiento, poliza.movimientos.filter(cuenta=cuenta_banco).first())


def _vincular_linea(movimiento, linea):
    if not linea:
        return False
    movimiento.movimiento_contable = linea
    movimiento.match_automatico = True
    movimiento.confirmado = False
    movimiento.save(update_fields=['movimiento_contable', 'match_automatico', 'confirmado'])
    return True


def crear_poliza_desde_movimiento(movimiento, cuenta_contrapartida, usuario, *, aplicar=True, concepto=''):
    """
    Póliza de un movimiento del banco contra `cuenta_contrapartida`:

        cargo (sale dinero):  DEBE contrapartida / HABER bancos
        abono (entra dinero): DEBE bancos / HABER contrapartida

    Idempotente por movimiento: si ya existe su póliza BANCO vigente, la
    devuelve sin crear otra. Si `aplicar`, la aplica y empareja el movimiento
    con su línea de bancos; si no, queda en BORRADOR y el movimiento sigue sin
    asiento hasta que alguien la aplique (lo recoge `vincular_polizas_propias`).
    """
    cuenta_bancaria = movimiento.estado_cuenta.cuenta_bancaria
    cuenta_banco = cuenta_bancaria.cuenta_contable
    if not cuenta_banco:
        raise ValueError(f"La cuenta bancaria «{cuenta_bancaria}» no tiene cuenta contable de bancos.")
    if cuenta_contrapartida == cuenta_banco:
        raise ValueError("La contrapartida no puede ser la misma cuenta de bancos.")

    existente = _poliza_del_movimiento(movimiento)
    if existente:
        if existente.estado == 'APLICADA' and not movimiento.movimiento_contable_id:
            _vincular(movimiento, existente, cuenta_banco)
        return existente

    es_cargo = movimiento.cargo > 0
    importe = movimiento.cargo if es_cargo else movimiento.abono
    if importe <= 0:
        raise ValueError("El movimiento no tiene importe.")

    unidad = cuenta_bancaria.unidad_negocio or UnidadNegocio.objects.filter(clave='QUINTA').first()
    if not unidad:
        raise ValueError("No hay unidad de negocio para la póliza.")

    tipo = 'E' if es_cargo else 'I'
    concepto_banco = (movimiento.descripcion or '').strip()
    with transaction.atomic():
        poliza = Poliza.objects.create(
            tipo=tipo,
            folio=Poliza.siguiente_folio(tipo, movimiento.fecha),
            fecha=movimiento.fecha,
            concepto=(f"{concepto}: {concepto_banco}" if concepto else concepto_banco)[:500] or "Movimiento bancario",
            unidad_negocio=unidad,
            estado='BORRADOR',
            origen='BANCO',
            content_type=ContentType.objects.get_for_model(MovimientoEstadoCuenta),
            object_id=movimiento.pk,
            created_by=usuario,
        )
        cero = Decimal('0.00')
        referencia = (movimiento.referencia or '')[:100]
        linea_contrapartida = dict(poliza=poliza, cuenta=cuenta_contrapartida,
                                   concepto=concepto_banco[:300], referencia=referencia)
        linea_banco = dict(poliza=poliza, cuenta=cuenta_banco,
                           concepto=concepto_banco[:300], referencia=referencia)
        if es_cargo:
            MovimientoContable.objects.create(debe=importe, haber=cero, **linea_contrapartida)
            MovimientoContable.objects.create(debe=cero, haber=importe, **linea_banco)
        else:
            MovimientoContable.objects.create(debe=importe, haber=cero, **linea_banco)
            MovimientoContable.objects.create(debe=cero, haber=importe, **linea_contrapartida)

        if aplicar:
            poliza.aplicar(usuario)
            _vincular(movimiento, poliza, cuenta_banco)
    return poliza


def _patrones_de_cuenta(cuenta):
    """Números con los que BBVA puede nombrar una cuenta propia en un concepto (mínimo 8 dígitos)."""
    clabe = (cuenta.clabe or '').strip()
    # CLABE = 3 banco + 3 plaza + 11 cuenta (con un cero inicial) + 1 verificador: la cuenta de 10 dígitos es [7:17].
    cuenta_en_clabe = clabe[7:17] if len(clabe) == 18 else ''
    candidatos = {(cuenta.numero_cuenta or '').strip(), clabe, cuenta_en_clabe}
    return [c for c in candidatos if c.isdigit() and len(c) >= 8]


def es_traspaso_propio(movimiento):
    """
    La otra cuenta propia (CuentaBancaria activa, con cuenta contable, distinta de la del estado) que
    el concepto del banco nombra por su número de cuenta o CLABE, o None. El número debe aparecer como
    cadena de dígitos completa (no dentro de una referencia más larga). Si el formato real de BBVA no
    trae el número, el fallback es una ReglaConciliacion con operación TRASPASOS_EN_TRANSITO.
    """
    import re

    from .models import CuentaBancaria

    texto = normalizar_texto_banco(f"{movimiento.descripcion or ''} {movimiento.referencia or ''}")
    if not texto:
        return None
    propia = movimiento.estado_cuenta.cuenta_bancaria_id
    otras = CuentaBancaria.objects.filter(activa=True, cuenta_contable__isnull=False).exclude(pk=propia)
    for cuenta in otras:
        for numero in _patrones_de_cuenta(cuenta):
            if re.search(rf'(?<!\d){re.escape(numero)}(?!\d)', texto):
                return cuenta
    return None


def asentar_traspasos_propios(estado_cuenta, usuario=None):
    """
    Cada punta de un traspaso entre cuentas propias se asienta sola contra la cuenta puente
    (TRASPASOS_EN_TRANSITO): cargo = DEBE puente / HABER banco; abono = DEBE banco / HABER puente. Sin gasto
    ni ingreso, sin esperar a la otra punta, en cualquier orden. Idempotente (una póliza por movimiento).
    Saldo ≠ 0 de la puente = traspaso con una punta pendiente. Devuelve cuántos asentó.
    """
    from .services_abonos import ids_movimientos_en_bitacora

    puente = ConfiguracionContable.obtener_cuenta('TRASPASOS_EN_TRANSITO')
    if puente is None or not estado_cuenta.cuenta_bancaria.cuenta_contable_id:
        return 0
    usuario = usuario or usuario_sistema()
    en_bitacora = ids_movimientos_en_bitacora()
    asentados = 0
    pendientes = (estado_cuenta.movimientos.filter(movimiento_contable__isnull=True)
                  .exclude(pk__in=en_bitacora).order_by('fecha', 'id'))
    for mov in pendientes:
        if _poliza_del_movimiento(mov) or es_traspaso_propio(mov) is None:
            continue
        try:
            crear_poliza_desde_movimiento(mov, puente, usuario, aplicar=True,
                                          concepto='Traspaso entre cuentas propias')
        except Exception as e:  # un movimiento roto no debe frenar a los demás
            logger.warning("No se pudo asentar el traspaso propio %s: %s", mov.pk, e)
            continue
        asentados += 1
    return asentados


TEXTO_FONDEO = 'TRASPASO CUENTAS PROPIAS'
ESTADOS_QUE_CUENTAN = ('PROCESADO', 'CONCILIADO')


def _es_cargo_de_fondeo(mov):
    """Cargo cuyo concepto es «TRASPASO CUENTAS PROPIAS» y no nombra la cuenta destino (BBVA solo imprime el
    origen en la punta que recibe)."""
    if not mov.cargo > 0:
        return False
    texto = normalizar_texto_banco(f"{mov.descripcion or ''} {mov.referencia or ''}")
    return TEXTO_FONDEO in texto and es_traspaso_propio(mov) is None


def _cuentas_propias_otras(cuenta_bancaria, fecha=None):
    from .models import CuentaBancaria
    otras = CuentaBancaria.objects.filter(activa=True, cuenta_contable__isnull=False).exclude(pk=cuenta_bancaria.pk)
    return [c for c in otras if fecha is None or c.operaba_en(fecha.year, fecha.month)]


def _estado_cubre(cuenta, dia):
    """¿Hay un estado procesado de la cuenta que incluya `dia`? Del mes del día o del siguiente (un corte antes
    de fin de mes deja los últimos días en el estado siguiente) y con corte igual o posterior."""
    from datetime import date

    from .models import EstadoCuentaBancario
    siguiente = (dia.year + (dia.month == 12), dia.month % 12 + 1)
    for estado in EstadoCuentaBancario.objects.filter(cuenta_bancaria=cuenta, estado__in=ESTADOS_QUE_CUENTAN):
        if (estado.periodo_anio, estado.periodo_mes) not in ((dia.year, dia.month), siguiente):
            continue
        corte = estado.fecha_corte_real or date(estado.periodo_anio + (estado.periodo_mes == 12),
                                                 estado.periodo_mes % 12 + 1, 1)
        if corte >= dia:
            return True
    return False


def parejas_de_fondeos(cuenta_bancaria):
    """
    {id del cargo: id del abono} de los fondeos «TRASPASO CUENTAS PROPIAS» de una cuenta. La pareja es un abono del
    mismo importe, ±1 día, en el estado de otra cuenta propia, que `es_traspaso_propio` atribuye a esta cuenta.
    Uno a uno, por orden de fecha e id. Se recalcula siempre de los datos (sin estado guardado), así que es estable
    al reprocesar un PDF.
    """
    from datetime import timedelta
    cargos = [m for m in MovimientoEstadoCuenta.objects.filter(
        estado_cuenta__cuenta_bancaria=cuenta_bancaria, cargo__gt=0).select_related('estado_cuenta__cuenta_bancaria')
        .order_by('fecha', 'id') if _es_cargo_de_fondeo(m)]
    if not cargos:
        return {}
    importes = {c.cargo for c in cargos}
    desde, hasta = min(c.fecha for c in cargos) - timedelta(days=1), max(c.fecha for c in cargos) + timedelta(days=1)
    abonos = [a for a in MovimientoEstadoCuenta.objects.filter(
        abono__in=importes, fecha__range=(desde, hasta), estado_cuenta__cuenta_bancaria__activa=True,
    ).exclude(estado_cuenta__cuenta_bancaria=cuenta_bancaria).select_related('estado_cuenta__cuenta_bancaria')
        .order_by('fecha', 'id') if (propia := es_traspaso_propio(a)) is not None and propia.pk == cuenta_bancaria.pk]
    usados, parejas = set(), {}
    for cargo in cargos:
        for abono in abonos:
            if abono.pk not in usados and abono.abono == cargo.cargo and abs((abono.fecha - cargo.fecha).days) <= 1:
                usados.add(abono.pk)
                parejas[cargo.pk] = abono.pk
                break
    return parejas


def _clasificar_fondeos(estado_cuenta):
    """Cargos de fondeo sin asiento de un estado: (con_pareja, en_espera, libres). Los libres (sin pareja y con las
    otras cuentas ya procesadas, o sin otra cuenta propia) siguen a `aplicar_reglas` como siempre."""
    from datetime import timedelta

    from .services_abonos import ids_movimientos_en_bitacora
    cuenta = estado_cuenta.cuenta_bancaria
    sin_asiento = (estado_cuenta.movimientos.filter(movimiento_contable__isnull=True, cargo__gt=0)
                   .exclude(pk__in=ids_movimientos_en_bitacora()).order_by('fecha', 'id'))
    candidatos = [m for m in sin_asiento if _es_cargo_de_fondeo(m) and not _poliza_del_movimiento(m)]
    if not candidatos:
        return [], [], []
    parejas = parejas_de_fondeos(cuenta)
    con_pareja, en_espera, libres = [], [], []
    for mov in candidatos:
        if mov.pk in parejas:
            con_pareja.append(mov)
        elif any(not (_estado_cubre(o, mov.fecha - timedelta(days=1)) and _estado_cubre(o, mov.fecha + timedelta(days=1)))
                 for o in _cuentas_propias_otras(cuenta, mov.fecha)):
            en_espera.append(mov)
        else:
            libres.append(mov)
    return con_pareja, en_espera, libres


def asentar_fondeos(estado_cuenta, usuario=None):
    """
    Cargos «TRASPASO CUENTAS PROPIAS» (el abono de la otra cuenta es el que nombra el origen): con pareja van a la
    puente. Sin pareja y con la otra cuenta aún sin estado procesado esperan sin asiento y sin pendiente (si el
    estado no llega, ya avisa `avisar_si_no_llego`); sin pareja y con ambos estados procesados fue a una cuenta
    personal y sigue a `aplicar_reglas`. Devuelve {'asentados': n, 'en_espera': n}.
    """
    puente = ConfiguracionContable.obtener_cuenta('TRASPASOS_EN_TRANSITO')
    if puente is None or not estado_cuenta.cuenta_bancaria.cuenta_contable_id:
        return {'asentados': 0, 'en_espera': 0}
    usuario = usuario or usuario_sistema()
    con_pareja, en_espera, _ = _clasificar_fondeos(estado_cuenta)
    asentados = 0
    for mov in con_pareja:
        try:
            crear_poliza_desde_movimiento(mov, puente, usuario, aplicar=True, concepto='Traspaso entre cuentas propias')
        except Exception as e:  # un movimiento roto no debe frenar a los demás
            logger.warning("No se pudo asentar el fondeo %s: %s", mov.pk, e)
            continue
        asentados += 1
    return {'asentados': asentados, 'en_espera': len(en_espera)}


def autocorregir_fondeos(estado_cuenta):
    """
    Un cargo de fondeo ya APLICADO como retiro del dueño cuya pareja apareció después (el abono cayó en el estado
    del mes siguiente) se reapunta a la puente, con los candados de `reclasificar_traspasos`. Idempotente.
    Devuelve cuántos corrigió.
    """
    from .services_traspasos import reapuntar_a_puente
    puente = ConfiguracionContable.obtener_cuenta('TRASPASOS_EN_TRANSITO')
    retiros = ConfiguracionContable.obtener_cuenta('RETIROS_DUENO')
    if puente is None or retiros is None:
        return 0
    cuenta = estado_cuenta.cuenta_bancaria
    aplicados = [m for m in estado_cuenta.movimientos.filter(movimiento_contable__isnull=False, cargo__gt=0)
                 .select_related('estado_cuenta__cuenta_bancaria') if _es_cargo_de_fondeo(m)]
    if not aplicados:
        return 0
    parejas = parejas_de_fondeos(cuenta)
    corregidos = 0
    for mov in aplicados:
        if mov.pk in parejas and reapuntar_a_puente(mov, puente, {retiros.pk}, True):
            corregidos += 1
    return corregidos


def reintentar_fondeos_cruzados(estado_cuenta, usuario=None):
    """
    Al terminar un estado, los fondeos de las otras cuentas propias (estados del mismo mes y del anterior) pueden
    haber encontrado su pareja o dejado de esperar: se asientan, se autocorrigen y lo liberado pasa a las reglas.
    """
    from .models import EstadoCuentaBancario
    usuario = usuario or usuario_sistema()
    mes, anio = estado_cuenta.periodo_mes, estado_cuenta.periodo_anio
    anterior = (anio - (mes == 1), 12 if mes == 1 else mes - 1)
    estados = [e for e in EstadoCuentaBancario.objects.filter(
        estado__in=ESTADOS_QUE_CUENTAN, cuenta_bancaria__activa=True).exclude(cuenta_bancaria=estado_cuenta.cuenta_bancaria)
        if (e.periodo_anio, e.periodo_mes) in ((anio, mes), anterior)]
    for otro in estados:
        asentar_fondeos(otro, usuario)
        autocorregir_fondeos(otro)
        libres = {m.pk for m in _clasificar_fondeos(otro)[2]}
        if libres:  # ya no esperan y no eran fondeos: siguen la regla de siempre (retiro del dueño)
            aplicar_reglas(otro, usuario=usuario, solo_ids=libres)


def vincular_polizas_propias(estado_cuenta):
    """Empareja los movimientos cuya póliza BANCO quedó en borrador y alguien
    aplicó después. Devuelve cuántos vinculó."""
    cuenta_banco = estado_cuenta.cuenta_bancaria.cuenta_contable
    if not cuenta_banco:
        return 0
    vinculados = 0
    for mov in estado_cuenta.movimientos.filter(movimiento_contable__isnull=True):
        poliza = _poliza_del_movimiento(mov)
        if poliza and poliza.estado == 'APLICADA' and _vincular(mov, poliza, cuenta_banco):
            vinculados += 1
    return vinculados


def _caja_alcanza(cuenta_caja, movimiento):
    """¿El saldo de Caja a la fecha del abono cubre el monto? Solo pólizas aplicadas."""
    t = cuenta_caja.movimientos.filter(
        poliza__estado='APLICADA', poliza__fecha__lte=movimiento.fecha,
    ).aggregate(d=Sum('debe'), h=Sum('haber'))
    debe, haber = t['d'] or Decimal('0.00'), t['h'] or Decimal('0.00')
    saldo = debe - haber if cuenta_caja.naturaleza == 'D' else haber - debe
    return saldo >= movimiento.abono


def _pendiente_sin_saldo_caja(movimiento):
    from comunicacion.services_pendientes import registrar_pendiente
    registrar_pendiente(
        titulo=f"Depósito de efectivo sin saldo en Caja: ${movimiento.abono}",
        rol='DIRECCION', tipo='ALERTA',
        detalle=(f"El abono del {movimiento.fecha} ({movimiento.descripcion[:120]}) parece un depósito de efectivo, "
                 "pero Caja no tiene saldo suficiente para el traspaso. Revisa si es un cobro de cliente sin Pago registrado."),
        clave_idempotencia=f"deposito_efectivo_sin_caja:{movimiento.pk}",
    )


def aplicar_reglas(estado_cuenta, usuario=None, solo_ids=None):
    """
    Crea la póliza de cada movimiento sin asiento que calce con una regla.
    Devuelve un resumen con listas de MovimientoEstadoCuenta: 'aplicadas',
    'borrador' (póliza creada, falta aplicarla), 'sin_cuenta' (la regla calza
    pero su operación no tiene cuenta configurada) y 'sin_regla'.
    """
    from .services_abonos import ids_movimientos_en_bitacora

    resumen = {'aplicadas': [], 'borrador': [], 'sin_cuenta': [], 'sin_regla': [], 'sin_saldo_caja': []}
    if not estado_cuenta.cuenta_bancaria.cuenta_contable:
        return resumen

    usuario = usuario or usuario_sistema()
    reglas = list(ReglaConciliacion.objects.filter(activa=True).select_related('cuenta'))
    cuenta_caja = ConfiguracionContable.obtener_cuenta('CAJA')

    # Un abono propuesto a Dirección o ya registrado como pago de un cliente no es de una regla.
    en_bitacora = ids_movimientos_en_bitacora() | {m.pk for m in _clasificar_fondeos(estado_cuenta)[1]}
    pendientes = (estado_cuenta.movimientos.filter(movimiento_contable__isnull=True)
                  .exclude(pk__in=en_bitacora).order_by('fecha', 'id'))
    if solo_ids is not None:
        pendientes = pendientes.filter(pk__in=solo_ids)
    for mov in pendientes:
        existente = _poliza_del_movimiento(mov)
        if existente and existente.estado == 'BORRADOR':
            resumen['borrador'].append(mov)
            continue

        regla = next((r for r in reglas if r.coincide(mov)), None)
        if not regla:
            resumen['sin_regla'].append(mov)
            continue
        cuenta = regla.cuenta_contrapartida()
        if not cuenta:
            logger.warning("Regla «%s» sin cuenta configurada (operación %s)", regla, regla.operacion)
            resumen['sin_cuenta'].append(mov)
            continue

        if mov.abono > 0 and cuenta == cuenta_caja and not _caja_alcanza(cuenta, mov):
            # Abono que dice «depósito de efectivo» pero Caja no tiene con qué:
            # no es un traspaso, quizá un cliente sin Pago registrado. Se revisa a mano.
            resumen['sin_saldo_caja'].append(mov)
            _pendiente_sin_saldo_caja(mov)
            continue

        try:
            poliza = crear_poliza_desde_movimiento(
                mov, cuenta, usuario, aplicar=regla.aplicar_automaticamente, concepto=regla.nombre,
            )
        except Exception as e:  # una regla rota no debe frenar las demás
            logger.warning("No se pudo asentar el movimiento %s con la regla «%s»: %s", mov.pk, regla, e)
            resumen['sin_cuenta'].append(mov)
            continue
        resumen['aplicadas' if poliza.estado == 'APLICADA' else 'borrador'].append(mov)
    return resumen


TIPOS_PROVISIONALES = ('GASTO', 'COSTO')


def sustituir_asientos_provisionales_por_cfdi(estado_cuenta, usuario):
    """
    Un cargo asentado por palabra clave contra una cuenta de gasto es
    provisional: no lleva IVA acreditable porque aún no hay factura. Cuando
    su CFDI se carga después (carga masiva de XML), la Compra es el asiento
    bueno. Se cancela la póliza provisional —se conserva, no se borra— y se
    libera el movimiento para que el paso siguiente lo empareje con la Compra.
    Sin esto, una Compra que se marca pagada sola (unidad con una única cuenta
    bancaria) dejaría el mismo dinero asentado dos veces en bancos.
    Solo con exactamente una Compra candidata: con varias no se adivina.
    Traspasos, inversiones o nómina nunca se tocan. Devuelve los liberados.
    """
    from .services_compras import ReapunteRechazado, reapuntar_banco_de_compra
    from .services_estados_cuenta import compras_candidatas

    cuenta_bancaria = estado_cuenta.cuenta_bancaria
    unidad = cuenta_bancaria.unidad_negocio
    cuenta_banco = cuenta_bancaria.cuenta_contable
    if not unidad or not cuenta_banco:
        return []

    liberados = []
    enlazados = estado_cuenta.movimientos.filter(
        cargo__gt=0, movimiento_contable__poliza__origen='BANCO',
        movimiento_contable__poliza__estado='APLICADA',
    ).select_related('movimiento_contable__poliza')
    for mov in enlazados:
        poliza = mov.movimiento_contable.poliza
        es_provisional = poliza.movimientos.exclude(cuenta=cuenta_banco).filter(
            cuenta__tipo__in=TIPOS_PROVISIONALES,
        ).exists()
        if not es_provisional:
            continue
        candidatas = compras_candidatas(mov, unidad, cuenta_bancaria=cuenta_bancaria)
        if len(candidatas) != 1:
            continue
        compra = candidatas[0]
        with transaction.atomic():
            if compra.cuenta_pago_id and compra.cuenta_pago_id != cuenta_bancaria.pk:
                # Pagada desde la otra cuenta propia: el estado manda, pero si el reapunte se rechaza
                # (línea ligada, periodo cerrado) la provisional se queda como está.
                try:
                    reapuntar_banco_de_compra(compra, cuenta_bancaria, desde_conciliacion=True)
                except ReapunteRechazado:
                    continue
            poliza.cancelar(usuario, f"Sustituida por el CFDI de la Compra #{compra.pk} ({compra.uuid or 'sin UUID'})")
            mov.movimiento_contable = None
            mov.match_automatico = False
            mov.confirmado = False
            mov.save(update_fields=['movimiento_contable', 'match_automatico', 'confirmado'])
            # Sin clasificación propia, la Compra hereda la cuenta de la palabra
            # clave (INS, MANT…) en vez de caer en Gastos generales.
            if compra.categoria == 'SIN_CLASIFICAR' and not (compra.proveedor_id and compra.proveedor.cuenta_gasto_id):
                linea_gasto = poliza.movimientos.exclude(cuenta=cuenta_banco).filter(
                    cuenta__tipo__in=TIPOS_PROVISIONALES,
                ).first()
                reclasificar_compra(compra, cuenta=linea_gasto.cuenta)
        liberados.append(mov)
    return liberados


def emparejar_compras_ya_pagadas(estado_cuenta):
    """
    Cargos sin asiento contra Compras que ya se marcaron pagadas desde esta
    cuenta. `_emparejar_automaticamente` ya cubre importe + fecha ±5 días de
    la póliza (fechada con la emisión del CFDI); esto agrega la factura pagada
    semanas después cuando BBVA imprime el RFC del comercio. Devuelve cuántos.
    """
    from .services_compras import ReapunteRechazado, reapuntar_banco_de_compra
    from .services_estados_cuenta import compras_candidatas, linea_banco_de_compra

    cuenta_bancaria = estado_cuenta.cuenta_bancaria
    unidad = cuenta_bancaria.unidad_negocio
    if not unidad or not cuenta_bancaria.cuenta_contable_id:
        return 0
    emparejados = 0
    for mov in estado_cuenta.movimientos.filter(movimiento_contable__isnull=True, cargo__gt=0):
        pagadas = [c for c in compras_candidatas(mov, unidad, cuenta_bancaria=cuenta_bancaria) if c.cuenta_pago_id]
        if len(pagadas) != 1:
            continue
        compra = pagadas[0]
        if compra.cuenta_pago_id != cuenta_bancaria.pk:
            # El cargo apareció en el estado de esta cuenta: ahí salió el dinero (el estado manda).
            try:
                reapuntar_banco_de_compra(compra, cuenta_bancaria, desde_conciliacion=True)
            except ReapunteRechazado:
                continue
        linea = linea_banco_de_compra(compra, cuenta_bancaria)
        if linea and _vincular_linea(mov, linea):
            emparejados += 1
    return emparejados


def clave_aprendizaje(movimiento):
    """
    Clave estable para reconocer el mismo tipo de movimiento el mes siguiente,
    de más a menos confiable: RFC impreso por el banco, cuenta del tercero
    (BNET o CLABE), comercio de la tarjeta. Devuelve
    {'patrones': str, 'cuenta_tercero': str, 'etiqueta': str} o None.
    """
    texto = normalizar_texto_banco(movimiento.descripcion)
    rfc = RFC_RE.search(texto)
    if rfc:
        return {'patrones': rfc.group(0), 'cuenta_tercero': '', 'etiqueta': rfc.group(0)}
    cuenta = CUENTA_BNET_RE.search(texto) or CLABE_RE.search(texto)
    if cuenta:
        return {'patrones': '', 'cuenta_tercero': cuenta.group(1), 'etiqueta': f"cuenta {cuenta.group(1)}"}
    if MARCA_TARJETA in texto:
        palabras = texto.split(MARCA_TARJETA)[0].split()
        if palabras:
            primera = palabras[0]
            comercio = primera if '*' in primera.rstrip('*') else primera.rstrip('*')
            if len(comercio) < 4 and len(palabras) > 1:
                comercio = f"{comercio} {palabras[1].rstrip('*')}"
            if len(comercio) >= 4:
                return {'patrones': comercio, 'cuenta_tercero': '', 'etiqueta': comercio}
    return None


def clasificar_movimiento(movimiento, cuenta_contrapartida, usuario, *, recordar=True, nombre_regla=''):
    """
    Clasificación manual: crea y aplica la póliza del movimiento y, si
    `recordar`, una regla APRENDIDA para los siguientes. Devuelve (poliza, regla|None).
    """
    regla = None
    with transaction.atomic():
        poliza = crear_poliza_desde_movimiento(
            movimiento, cuenta_contrapartida, usuario, aplicar=True, concepto=nombre_regla,
        )
        if poliza.estado == 'BORRADOR':
            poliza.aplicar(usuario)
            vincular_polizas_propias(movimiento.estado_cuenta)
        clave = clave_aprendizaje(movimiento) if recordar else None
        if clave:
            tipo = 'CARGO' if movimiento.cargo > 0 else 'ABONO'
            regla = ReglaConciliacion.objects.filter(
                patrones=clave['patrones'], cuenta_tercero=clave['cuenta_tercero'],
                tipo_movimiento=tipo, activa=True,
            ).first()
            if regla:
                regla.cuenta = cuenta_contrapartida
                regla.operacion = ''
                regla.updated_by = usuario
                regla.save()
            else:
                regla = ReglaConciliacion.objects.create(
                    nombre=(nombre_regla or f"{clave['etiqueta']} → {cuenta_contrapartida.nombre}")[:120],
                    tipo_movimiento=tipo,
                    patrones=clave['patrones'],
                    cuenta_tercero=clave['cuenta_tercero'],
                    cuenta=cuenta_contrapartida,
                    prioridad=PRIORIDAD_APRENDIDA,
                    origen='APRENDIDA',
                    created_by=usuario,
                    updated_by=usuario,
                )
    return poliza, regla
