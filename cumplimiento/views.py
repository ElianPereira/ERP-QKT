from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import services


@staff_member_required
@permission_required('cumplimiento.view_tramite', raise_exception=True)
def tablero(request):
    contexto = services.resumen_tablero()
    contexto['puede_cargar_catalogo'] = request.user.is_superuser
    contexto['title'] = 'Tablero de cumplimiento'
    return render(request, 'cumplimiento/tablero.html', contexto)


@staff_member_required
@require_POST
def cargar_catalogo(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    creados = services.cargar_catalogo_sugerido(request.user)
    if creados:
        messages.success(request, f'Se cargaron {creados} trámite(s) del catálogo sugerido.')
    else:
        messages.info(request, 'El catálogo sugerido ya estaba cargado.')
    return redirect(reverse('cumplimiento:tablero'))
