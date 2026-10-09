"""
Recibos de nómina (Issue #373, fase 3)
======================================
Fuente única para calcular y emitir el recibo semanal: la usan la carga de
Excel, la sincronización con Jibble desde el admin, su webhook y el comando
`sync_jibble`, que antes repetía el mismo cálculo con `float`.

Cada registro de asistencia trae `fecha` (AAAA-MM-DD), `entrada`, `salida`,
`horas_fmt`, `horas_raw` (horas reales) y `horas_a_pagar` (ya con la regla
del 90 %).
"""
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core_erp.documentos import nombre_archivo, render_pdf

from .models import Empleado, ReciboNomina

DIAS = ('Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom')
CENTAVO = Decimal('0.01')


def _decimal(valor) -> Decimal:
    return Decimal(str(valor))


def _fecha(valor) -> date:
    return valor if isinstance(valor, date) else datetime.strptime(str(valor)[:10], '%Y-%m-%d').date()


def calcular_recibo(empleado, registros) -> dict:
    """Totales del recibo en `Decimal`, sin guardar nada."""
    tarifa = empleado.tarifa_base
    horas_reales = sum((_decimal(r['horas_raw']) for r in registros), Decimal('0')).quantize(
        CENTAVO, rounding=ROUND_HALF_UP)
    horas_a_pagar = sum((_decimal(r['horas_a_pagar']) for r in registros), Decimal('0'))
    total = (horas_a_pagar * tarifa).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    sin_redondeo = (horas_reales * tarifa).quantize(CENTAVO, rounding=ROUND_HALF_UP)
    filas = [
        {**r, 'fecha': _fecha(r['fecha']), 'dia': DIAS[_fecha(r['fecha']).weekday()]}
        for r in registros
    ]
    return {
        'filas': filas,
        'tarifa': tarifa,
        'horas_reales': horas_reales,
        'horas_a_pagar': horas_a_pagar,
        'ajuste_horas': horas_reales - horas_a_pagar,
        'total': total,
        'sin_redondeo': sin_redondeo,
        'ajuste_dinero': sin_redondeo - total,
    }


def _fecha_emision(valor):
    """'AAAA-MM-DD HH:MM' (última salida de la semana) o ahora."""
    if valor:
        try:
            return timezone.make_aware(datetime.strptime(valor, '%Y-%m-%d %H:%M'))
        except ValueError:
            pass
    return timezone.localtime()


def generar_recibo(empleado, registros, *, inicio=None, fin=None, fecha_emision='') -> ReciboNomina:
    """Guarda el recibo y su PDF. El periodo, por defecto, va de la primera a la última fecha.

    Todo o nada: si el PDF no se puede generar, no queda un recibo sin archivo.
    """
    calculo = calcular_recibo(empleado, registros)
    fechas = [f['fecha'] for f in calculo['filas']]
    inicio = _fecha(inicio) if inicio else min(fechas)
    fin = _fecha(fin) if fin else max(fechas)
    with transaction.atomic():
        return _guardar(empleado, calculo, inicio, fin, fecha_emision)


def _guardar(empleado, calculo, inicio, fin, fecha_emision) -> ReciboNomina:
    recibo = ReciboNomina.objects.create(
        empleado=empleado,
        periodo=f'{inicio:%Y-%m-%d} al {fin:%Y-%m-%d}',
        horas_trabajadas=calculo['horas_a_pagar'],
        tarifa_aplicada=calculo['tarifa'],
        total_pagado=calculo['total'],
    )
    folio = f'NOM-{recibo.pk:03d}'
    pdf = render_pdf('nomina/recibo_nomina.html', {
        'titulo': 'Recibo de nómina',
        'empleado': empleado,
        'folio': folio,
        'inicio': inicio,
        'fin': fin,
        'fecha_emision': _fecha_emision(fecha_emision),
        **calculo,
    })
    recibo.archivo_pdf.save(nombre_archivo('Nomina', empleado.nombre, inicio), ContentFile(pdf))
    return recibo


def generar_recibos(datos_empleados, fecha_emision_por_empleado=None) -> int:
    """Un recibo por empleado con asistencia; devuelve cuántos se generaron."""
    fecha_emision_por_empleado = fecha_emision_por_empleado or {}
    count = 0
    for nombre, registros in datos_empleados.items():
        if not registros:
            continue
        empleado, _ = Empleado.objects.get_or_create(nombre=nombre)
        generar_recibo(empleado, registros, fecha_emision=fecha_emision_por_empleado.get(nombre, ''))
        count += 1
    return count
