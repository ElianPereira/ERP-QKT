"""
Hoja del evento: todo lo que el equipo necesita para operar un servicio en una
sola página (qué, cuándo, dónde, quién y cómo). Se descarga desde la
cotización y desde el calendario.

Solo lee lo que ya existe en el ERP: la cotización y sus conceptos, los
espacios y el personal asignados, las tareas de operación con su checklist, el
encargado y el equipo de apoyo vigentes ese día, el contrato y el depósito en
garantía. No calcula nada nuevo ni muestra costos ni márgenes.
"""
from decimal import Decimal

from core_erp import documentos
from core_erp.horarios import formato_hora_ampm


def _horario(cotizacion) -> str:
    if cotizacion.tipo_servicio == 'HOSPEDAJE' and cotizacion.fecha_salida:
        return (f'Entrada {cotizacion.fecha_evento:%d/%m/%Y} · Salida {cotizacion.fecha_salida:%d/%m/%Y} '
                f'({cotizacion.noches} noche{"s" if cotizacion.noches != 1 else ""})')
    if cotizacion.hora_inicio and cotizacion.hora_fin:
        return f'{formato_hora_ampm(cotizacion.hora_inicio)} a {formato_hora_ampm(cotizacion.hora_fin)}'
    return 'Horario por confirmar'


def _a_cargo(fecha) -> dict:
    """Encargado (o su suplente) y equipo de apoyo vigentes el día del servicio."""
    from comunicacion.models import Responsable
    from comunicacion.services_responsables import TEMAS_APOYO, equipo_apoyo, responsable
    from comunicacion.services_visitas import telefono_legible

    encargado = responsable('ENCARGADO', fecha)
    apoyo = []
    for integrante in equipo_apoyo(fecha):
        temas = [str(Responsable._meta.get_field(c).verbose_name)
                 for c in TEMAS_APOYO.values() if getattr(integrante, c)]
        apoyo.append({'nombre': integrante.nombre, 'telefono': telefono_legible(integrante.telefono_wa),
                      'temas': ', '.join(temas)})
    return {
        'encargado': ({'nombre': encargado.nombre, 'telefono': telefono_legible(encargado.telefono_wa)}
                      if encargado else None),
        'apoyo': apoyo,
    }


def _tareas(cotizacion) -> list:
    tareas = (cotizacion.tareas_operativas.select_related('plantilla', 'responsable')
              .prefetch_related('plantilla__items').order_by('fecha', 'hora_limite'))
    return [{
        'nombre': t.plantilla.nombre,
        'fecha': t.fecha,
        'entrada': formato_hora_ampm(t.hora_entrada),
        'limite': formato_hora_ampm(t.hora_limite),
        'responsable': t.responsable.nombre if t.responsable_id else 'Sin asignar',
        'completada': t.completada_en,
        'pasos': [i.texto for i in t.plantilla.items.all()],
    } for t in tareas]


def _deposito(cotizacion):
    deposito = getattr(cotizacion, 'deposito_garantia', None)
    if deposito is None:
        return None
    return {'monto': deposito.monto, 'recibido': deposito.recibido, 'en_custodia': deposito.en_custodia,
            'estado': deposito.estado}


def _contrato(cotizacion):
    contrato = cotizacion.contratos.order_by('-generado_en').first()
    if contrato is None:
        return None
    firma = getattr(contrato, 'firma', None)
    return {'numero': contrato.numero, 'firmado': bool(firma and firma.firmado)}


def contexto_hoja_evento(cotizacion) -> dict:
    from comunicacion.services_visitas import telefono_legible

    cliente = cotizacion.cliente
    saldo = cotizacion.saldo_pendiente()
    return {
        'titulo': 'Hoja del evento',
        'cotizacion': cotizacion,
        'folio': f'COT-{cotizacion.id:03d}',
        'horario': _horario(cotizacion),
        'domicilio': documentos.DOMICILIO,
        'cliente_telefono': telefono_legible(cliente.telefono) if cliente.telefono else '',
        'conceptos': cotizacion.items.all(),
        'espacios': cotizacion.espacios_asignados.select_related('espacio').order_by('fecha', 'hora_inicio'),
        'personal': cotizacion.personal_asignado.select_related('empleado').order_by('fecha', 'hora_inicio'),
        'a_cargo': _a_cargo(cotizacion.fecha_evento),
        'tareas': _tareas(cotizacion),
        'total': cotizacion.precio_final,
        'pagado': cotizacion.total_pagado(),
        'saldo': saldo if saldo > 0 else Decimal('0.00'),
        'deposito': _deposito(cotizacion),
        'contrato': _contrato(cotizacion),
    }
