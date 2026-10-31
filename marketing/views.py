"""Pantalla «Marketing» (Issue #468, fase 3). Solo lectura y solo Dirección (superusuario); toda la
lógica vive en `marketing/services/kpis.py`."""
from functools import wraps

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Decision
from .services import acciones, decisiones, kpis


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
        'aprobadas': [{'decision': d, 'lineas': acciones.vista_previa(d)} for d in Decision.objects.filter(
            estado=Decision.APROBADA, expira_en__gt=timezone.now(), tipo_accion__in=acciones.EJECUTABLES)],
        'modo_simulacion': acciones.modo_actual() == 'SIMULACION',
        'decisiones': Decision.objects.filter(estado__in=Decision.ABIERTAS).select_related('hallazgo'),
        'decisiones_recientes': Decision.objects.exclude(estado__in=Decision.ABIERTAS)[:5],
    })


@solo_direccion
@require_POST
def decidir(request, pk, accion):
    """Aprobar, rechazar o posponer una decisión. Aprobar solo deja constancia; ejecutar es otro paso."""
    hecho, mensaje = decisiones.resolver(pk, accion, request.user)
    (messages.success if hecho else messages.warning)(request, mensaje)
    return redirect(f"{reverse('marketing:tablero')}#decisiones")


@solo_direccion
@require_POST
def ejecutar(request, pk):
    """Ejecuta (o simula, con el interruptor apagado) una decisión ya aprobada. Solo superusuario, POST + CSRF."""
    hecho, mensaje = acciones.ejecutar(pk, request.user)
    (messages.success if hecho else messages.warning)(request, mensaje)
    return redirect(f"{reverse('marketing:tablero')}#decisiones")
