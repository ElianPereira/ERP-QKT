from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import services, services_expediente


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


@staff_member_required
@permission_required('cumplimiento.view_tramite', raise_exception=True)
def expediente_pdf(request):
    pdf, nombre = services_expediente.pdf_expediente(request)
    respuesta = HttpResponse(pdf, content_type='application/pdf')
    respuesta['Content-Disposition'] = f'inline; filename="{nombre}"'
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta


@staff_member_required
@permission_required('cumplimiento.view_vigencia', raise_exception=True)
def expediente_zip(request):
    """ZIP con los documentos oficiales vigentes (y, con `?requisitos=1`, los entregados)."""
    try:
        contenido, nombre, omitidos = services_expediente.armar_zip(
            request, incluir_requisitos=request.GET.get('requisitos') == '1')
    except services_expediente.ExpedienteDemasiadoGrande:
        messages.error(request, 'El expediente rebasa 100 MB: descarga sin los documentos de requisitos '
                                'o por trámite desde su vigencia.')
        return redirect(reverse('cumplimiento:tablero'))
    if omitidos:
        messages.warning(request, f'{len(omitidos)} archivo(s) no se pudieron leer y quedaron listados en LEEME.txt.')
    respuesta = HttpResponse(contenido, content_type='application/zip')
    respuesta['Content-Disposition'] = f'attachment; filename="{nombre}"'
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta
