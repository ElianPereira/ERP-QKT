"""Expediente de empleados y versiones de condiciones (Issue #505, PR 1A).

Ningún mensaje ni log de este módulo incluye CURP, RFC, NSS, CLABE ni el
número de identificación: solo nombres de campos.
"""
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import CondicionesPago, Empleado

logger = logging.getLogger(__name__)

# Lo que exige el expediente de Ventas (etiqueta que ve quien lo captura).
TIPOS_DOCUMENTO_VENTAS = (
    ('INE_FRENTE', 'INE (frente)'),
    ('INE_REVERSO', 'INE (reverso)'),
    ('ACUERDO_FIRMADO', 'Acuerdo de confidencialidad firmado'),
)


def expediente_faltantes(empleado) -> list:
    """Etiquetas de lo que falta en el expediente. Solo el puesto de Ventas lo exige."""
    if empleado.puesto != 'VENTAS':
        return []
    faltan = []
    if not (empleado.nombre_legal or '').strip():
        faltan.append('Nombre legal')
    if not empleado.domicilio_completo:
        faltan.append('Domicilio completo')
    if not empleado.identificacion_texto:
        faltan.append('Identificación (tipo y número)')
    if empleado.pk:
        presentes = set(empleado.documentos.filter(activo=True).values_list('tipo', flat=True))
    else:
        presentes = set()
    faltan.extend(etiqueta for tipo, etiqueta in TIPOS_DOCUMENTO_VENTAS if tipo not in presentes)
    return faltan


def condiciones_vigentes(empleado, fecha):
    """Condiciones aceptadas que regían en `fecha` (la actual o una sustituida con rango)."""
    candidatas = CondicionesPago.objects.filter(
        empleado=empleado, estado__in=('ACEPTADA', 'SUSTITUIDA'), vigente_desde__lte=fecha)
    for c in candidatas.order_by('-vigente_desde', '-id'):
        if c.vigente_hasta is None or c.vigente_hasta >= fecha:
            return c
    return None


@transaction.atomic
def aceptar_condiciones(condiciones, usuario=None):
    """Pasa las condiciones a ACEPTADA; la versión aceptada anterior queda SUSTITUIDA
    y vigente hasta el día previo al inicio de la nueva."""
    condiciones = CondicionesPago.objects.select_for_update().get(pk=condiciones.pk)
    if condiciones.estado not in ('BORRADOR', 'PROPUESTA'):
        raise ValidationError('Solo se aceptan condiciones en borrador o propuesta.')
    if not condiciones.escalones.exists() and not (
            condiciones.pago_visita or condiciones.pago_fijo or condiciones.tarifa_hora):
        raise ValidationError('Captura al menos un componente de pago antes de aceptarlas.')
    anterior = CondicionesPago.objects.filter(empleado_id=condiciones.empleado_id, estado='ACEPTADA').first()
    if anterior is not None:
        if condiciones.vigente_desde <= anterior.vigente_desde:
            raise ValidationError('La nueva versión debe iniciar después de la vigente.')
        fin = condiciones.vigente_desde - timedelta(days=1)
        CondicionesPago.objects.filter(pk=anterior.pk).update(estado='SUSTITUIDA', vigente_hasta=fin)
    CondicionesPago.objects.filter(pk=condiciones.pk).update(
        estado='ACEPTADA', aceptada_en=timezone.now(),
        updated_by=usuario if getattr(usuario, 'pk', None) else None)
    condiciones.refresh_from_db()
    return condiciones


def avisar_baja(empleado: Empleado):
    """Pendiente a Dirección para desactivar usuario y Responsable de quien causa baja."""
    from comunicacion.services_pendientes import registrar_pendiente

    registrar_pendiente(
        titulo=f'Baja de {empleado.nombre}: desactivar usuario y responsable',
        rol='DIRECCION', tipo='OTRO', severidad='NORMAL',
        detalle=f'Fecha de baja: {empleado.fecha_baja:%d/%m/%Y}\n'
                'Desactiva su usuario del ERP y su renglón en Responsables.',
        objeto=empleado, clave_idempotencia=f'baja_empleado:{empleado.pk}:{empleado.fecha_baja:%Y%m%d}')
