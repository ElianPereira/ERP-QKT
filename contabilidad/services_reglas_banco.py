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

from .models import (
    MovimientoContable,
    MovimientoEstadoCuenta,
    Poliza,
    ReglaConciliacion,
    UnidadNegocio,
    normalizar_texto_banco,
)

logger = logging.getLogger(__name__)

PRIORIDAD_APRENDIDA = 50

RFC_RE = re.compile(r'RFC: ?[A-Z&]{3,4} ?\d{6}[A-Z0-9]{3}')
CUENTA_BNET_RE = re.compile(r'BNET (\d{10})\b')
CLABE_RE = re.compile(r'\b00(\d{18})\b')
MARCA_TARJETA = '******'


def _usuario_sistema():
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
    linea = poliza.movimientos.filter(cuenta=cuenta_banco).first()
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


def aplicar_reglas(estado_cuenta, usuario=None):
    """
    Crea la póliza de cada movimiento sin asiento que calce con una regla.
    Devuelve un resumen con listas de MovimientoEstadoCuenta: 'aplicadas',
    'borrador' (póliza creada, falta aplicarla), 'sin_cuenta' (la regla calza
    pero su operación no tiene cuenta configurada) y 'sin_regla'.
    """
    resumen = {'aplicadas': [], 'borrador': [], 'sin_cuenta': [], 'sin_regla': []}
    if not estado_cuenta.cuenta_bancaria.cuenta_contable:
        return resumen

    usuario = usuario or _usuario_sistema()
    reglas = list(ReglaConciliacion.objects.filter(activa=True).select_related('cuenta'))

    pendientes = estado_cuenta.movimientos.filter(movimiento_contable__isnull=True).order_by('fecha', 'id')
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
