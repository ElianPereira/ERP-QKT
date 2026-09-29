"""
Borrado de pólizas por Dirección.

Excepción explícita a la soft-deactivation (decisión del propietario,
2026-09-29): Dirección (`is_superuser`) puede borrar pólizas con sus
movimientos, siempre con motivo y dejando una foto completa en
`PolizaEliminada`. Para todos los demás, lo que existe es cancelar.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .models import PolizaEliminada


def _foto(poliza):
    return {
        'id': poliza.pk,
        'tipo': poliza.tipo,
        'folio': poliza.folio,
        'fecha': poliza.fecha.isoformat(),
        'concepto': poliza.concepto,
        'unidad_negocio': poliza.unidad_negocio.clave,
        'estado': poliza.estado,
        'origen': poliza.origen,
        'documento_origen': (
            f"{poliza.content_type.app_label}.{poliza.content_type.model}:{poliza.object_id}"
            if poliza.content_type_id else None
        ),
        'creada_por': poliza.created_by.get_username(),
        'creada_el': poliza.created_at.isoformat(),
        'aplicada_por': poliza.aplicada_por.get_username() if poliza.aplicada_por_id else None,
        'cancelada_por': poliza.cancelada_por.get_username() if poliza.cancelada_por_id else None,
        'motivo_cancelacion': poliza.motivo_cancelacion,
        'movimientos': [
            {
                'cuenta': m.cuenta.codigo_sat,
                'nombre_cuenta': m.cuenta.nombre,
                'concepto': m.concepto,
                'debe': str(m.debe),
                'haber': str(m.haber),
                'referencia': m.referencia,
            }
            for m in poliza.movimientos.select_related('cuenta')
        ],
    }


def eliminar_polizas(polizas, usuario, motivo):
    """Borra las pólizas y sus movimientos (CASCADE) y deja una fila de
    `PolizaEliminada` por cada una. Todo o nada. Los renglones del estado de
    cuenta ligados a esos movimientos quedan sin asignar (SET_NULL)."""
    if not usuario.is_superuser:
        raise PermissionDenied("Solo Dirección puede borrar pólizas.")
    motivo = (motivo or '').strip()
    if not motivo:
        raise ValidationError("Indica el motivo del borrado.")

    eliminadas = 0
    with transaction.atomic():
        for poliza in polizas.select_related(
            'unidad_negocio', 'content_type', 'created_by', 'aplicada_por', 'cancelada_por',
        ).select_for_update(of=('self',)):
            PolizaEliminada.objects.create(
                tipo=poliza.tipo, folio=poliza.folio, fecha=poliza.fecha,
                concepto=poliza.concepto, origen=poliza.origen, estado=poliza.estado,
                total=poliza.total_debe, detalle=_foto(poliza), motivo=motivo,
                eliminada_por=usuario,
            )
            poliza.delete()
            eliminadas += 1
    return eliminadas
