"""Avisos de ventas por WhatsApp (Issue #505, PR 5): resumen semanal a la vendedora y línea del lunes a Dirección.

Solo lectura sobre `services_tablero`. La vendedora recibe únicamente lo suyo; nada de costos ni de otras personas.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from comunicacion.formato_wa import Aviso
from comunicacion.services_direccion import dinero
from comunicacion.services_pendientes import _enviar_aviso
from comunicacion.services_responsables import responsable, telefono_de

from . import services_tablero
from .services_atribucion import usuario_de_telefono, vendedor_de_usuario
from .services_comisiones import lunes_de

logger = logging.getLogger(__name__)


def enviar_resumen_semanal_ventas(hoy=None) -> int:
    """Resumen de la semana a quien tiene el rol VENTAS: visitas, contrataciones, escalón y por cobrar.
    Idempotente por semana y teléfono. Sin responsable de VENTAS o sin Empleado ligado, no manda nada."""
    hoy = hoy or timezone.localdate()
    if responsable('VENTAS', hoy) is None:
        return 0
    telefono = telefono_de('VENTAS', hoy)
    vendedor = vendedor_de_usuario(usuario_de_telefono(telefono))
    if not telefono or vendedor is None:
        return 0
    r = services_tablero.resumen_semanal(vendedor, hoy)
    esc = r['escalon']
    lineas = [f'Visitas atendidas: {r["visitas_atendidas"]}', f'Contrataciones: {r["contrataciones"]}',
              f'Llevas {esc["contrataciones_anio"]} contratación(es) en {esc["anio"]}']
    if esc['monto_siguiente'] is not None:
        lineas.append(f'La siguiente te paga {dinero(esc["monto_siguiente"])}')
    lineas.append(f'Por cobrar: {dinero(r["por_pagar"])}')
    aviso = Aviso(titulo=f'Tu semana del {r["desde"]:%d/%m} al {r["hasta"]:%d/%m}', categoria='Ventas', lineas=lineas)
    comm = _enviar_aviso(telefono, aviso, clave=f'ventas:resumen:{r["desde"]}:{telefono}')
    return int(comm is not None and comm.estado != 'FALLIDO')


def linea_resumen_direccion(hoy=None) -> str:
    """Una línea para el resumen del lunes: visitas, contrataciones y costo de ventas de la semana que cerró.
    Vacía si no hay actividad ni costo. Un fallo no tumba el resumen."""
    try:
        hoy = hoy or timezone.localdate()
        lunes = lunes_de(hoy - timedelta(days=1))
        t = services_tablero.tablero(lunes, lunes + timedelta(days=6), hoy)['totales']
        if not (t['visitas_atendidas'] or t['contrataciones'] or t['costo_de_ventas']):
            return ''
        return (f'Ventas de la semana: {t["visitas_atendidas"]} visita(s) atendida(s), {t["contrataciones"]} '
                f'contratación(es), costo de ventas {dinero(t["costo_de_ventas"])}')
    except Exception:
        logger.exception('Resumen semanal: falló la línea de ventas')
        return ''
