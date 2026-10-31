"""Pantalla «Marketing» (Issue #468, fase 3). Solo lectura y solo Dirección (superusuario); toda la
lógica vive en `marketing/services/kpis.py`."""
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .services import kpis


def solo_direccion(vista):
    @wraps(vista)
    @staff_member_required
    def envoltura(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return envoltura


@solo_direccion
def tablero(request):
    datos = kpis.resumen()
    ventana = datos['ventana']
    return render(request, 'marketing/tablero.html', {
        'title': 'Marketing', 'd': datos, 'total': ventana['total'],
        'servicios': list(ventana['por_servicio'].values()), 'campanas': ventana['por_campana'],
        'cohortes': datos['cohortes'], 'pronostico': datos['pronostico'],
    })
