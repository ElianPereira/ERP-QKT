"""Pantalla «Marketing» (Issue #468, fase 3). Solo lectura y solo Dirección (superusuario); toda la
lógica vive en `marketing/services/kpis.py`."""
from functools import wraps

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import Decision
from .services import decisiones, kpis


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
        'decisiones': Decision.objects.filter(estado__in=Decision.ABIERTAS).select_related('hallazgo'),
        'decisiones_recientes': Decision.objects.exclude(estado__in=Decision.ABIERTAS)[:5],
    })


@solo_direccion
@require_POST
def decidir(request, pk, accion):
    """Aprobar, rechazar o posponer una decisión. Aprobar solo deja constancia; la ejecución es de la fase 5."""
    hecho, mensaje = decisiones.resolver(pk, accion, request.user)
    (messages.success if hecho else messages.warning)(request, mensaje)
    return redirect(f"{reverse('marketing:tablero')}#decisiones")
