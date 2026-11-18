"""Pantallas «Ventas» (Dirección) y «Mis comisiones» (la vendedora). Solo lectura (Issue #505, PR 5).

El candado vive aquí, en el servidor: «Mis comisiones» resuelve al Empleado SIEMPRE desde `request.user`
y no lee ningún parámetro de la petición; el tablero y todo lo demás es solo del superusuario.
"""
from datetime import date

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils import timezone

from . import services_tablero
from .models import CorteComision
from .services_atribucion import vendedor_de_usuario
from .services_cortes import nombre_pdf


def _fecha(request, campo, defecto):
    try:
        return date.fromisoformat(request.GET.get(campo, ''))
    except ValueError:
        return defecto


@staff_member_required
def tablero(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    hoy = timezone.localdate()
    desde = _fecha(request, 'desde', hoy.replace(day=1))
    hasta = _fecha(request, 'hasta', hoy)
    if desde > hasta:
        desde, hasta = hasta, desde
    datos = services_tablero.tablero(desde, hasta, hoy)
    return render(request, 'ventas/tablero.html', {'title': 'Ventas', 'd': datos, 'totales': datos['totales']})


def _vendedora_o_403(request):
    vendedor = vendedor_de_usuario(request.user)
    if vendedor is None:
        raise PermissionDenied
    return vendedor


@staff_member_required
def mis_comisiones(request):
    vendedor = _vendedora_o_403(request)
    datos = services_tablero.mis_comisiones(vendedor)
    return render(request, 'ventas/mis_comisiones.html', {'title': 'Mis comisiones', 'd': datos})


@staff_member_required
def mis_comisiones_estado(request, pk):
    """Estado de comisiones de un corte propio. Un corte ajeno responde 404, igual que uno inexistente."""
    vendedor = _vendedora_o_403(request)
    corte = CorteComision.objects.filter(pk=pk, vendedor=vendedor).exclude(estado='CANCELADO').first()
    if corte is None or not corte.archivo_pdf:
        raise Http404
    try:
        archivo = corte.archivo_pdf.open('rb')
    except OSError:
        raise Http404 from None
    respuesta = FileResponse(archivo, as_attachment=False, content_type='application/pdf',
                             filename=nombre_pdf(corte))
    respuesta['X-Content-Type-Options'] = 'nosniff'
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta

