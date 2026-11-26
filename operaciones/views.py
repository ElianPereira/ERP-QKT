"""Hoja de operación del evento (PDF para imprimir). Ver `services_hoja`."""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404

from core_erp.documentos import nombre_archivo, respuesta_pdf
from ventas.permisos import es_vendedor

from .services_hoja import MAX_HOJAS, PLANTILLA, contexto_documento_hoja, cotizaciones_operables


def respuesta_hoja(cotizaciones, request, *, descargar=False):
    """PDF de una o varias hojas en un solo render; el nombre sale de lo que contiene."""
    cotizaciones = list(cotizaciones)
    if len(cotizaciones) == 1:
        nombre = nombre_archivo('HojaOperacion', f"COT-{cotizaciones[0].pk:03d}")
    else:
        fechas = [c.fecha_evento for c in cotizaciones]
        nombre = nombre_archivo('AgendaOperacion', min(fechas), max(fechas))
    return respuesta_pdf(PLANTILLA, contexto_documento_hoja(cotizaciones), nombre,
                         request=request, descargar=descargar)


@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def hoja_operacion(request, cotizacion_id):
    """PDF en línea (`?descargar=1` lo baja). Un vendedor nunca la genera; una venta no operable da 404."""
    if es_vendedor(request.user):
        raise PermissionDenied
    cotizacion = get_object_or_404(cotizaciones_operables(), pk=cotizacion_id)
    return respuesta_hoja([cotizacion], request, descargar=request.GET.get('descargar') == '1')


@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def hojas_operacion(request):
    """Varias hojas en un PDF por GET (`?ids=1,2`): así el navegador del celular lo abre y lo guarda,
    cosa que no hace con el PDF devuelto por el POST de una acción del admin."""
    if es_vendedor(request.user):
        raise PermissionDenied
    ids = [int(x) for x in request.GET.get('ids', '').split(',') if x.strip().isdigit()][:MAX_HOJAS]
    cotizaciones = list(cotizaciones_operables().filter(pk__in=ids))
    if not cotizaciones:
        raise Http404
    return respuesta_hoja(cotizaciones, request, descargar=request.GET.get('descargar') == '1')
