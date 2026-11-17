"""
Vistas del Manual (Issue #507). Cualquier usuario del equipo (staff) entra; lo que
ve lo decide `services.roles_de(request.user)` y nada de lo que venga en la URL.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import render
from django.utils import timezone

from core_erp.documentos import nombre_archivo, respuesta_pdf

from . import services as sv
from .models import DescargaManual


@staff_member_required
def indice(request):
    roles = sv.roles_de(request.user)
    visibles = sv.secciones_visibles(request.user)
    consulta = (request.GET.get('q') or '').strip()[:80]
    resultados = sv.buscar(consulta, visibles, roles) if consulta else None
    return render(request, 'manual/indice.html', {
        'title': 'Manual',
        'consulta': consulta,
        'resultados': resultados,
        'capitulos': sv.capitulos(visibles),
        'total': len(visibles),
        'es_superusuario': request.user.is_superuser,
        'selector_pdf': sv.SELECTOR_PDF,
        'etiquetas_rol': sv.ETIQUETAS_ROL,
    })


@staff_member_required
def seccion(request, slug):
    visibles = sv.secciones_visibles(request.user)
    elegida = next((s for s in visibles if s.slug == slug), None)
    if elegida is None:   # no existe o no es para este usuario: misma respuesta
        raise Http404
    anterior, siguiente = sv.anterior_y_siguiente(elegida, visibles)
    return render(request, 'manual/seccion.html', {
        'title': elegida.titulo,
        'seccion': elegida,
        'cuerpo': sv.render_cuerpo(elegida, sv.roles_de(request.user)),
        'anterior': anterior,
        'siguiente': siguiente,
        'roles_etiquetas': [sv.ETIQUETAS_ROL[r] for r in elegida.roles],
    })


@staff_member_required
def pdf(request):
    """PDF del manual del propio usuario; solo el superusuario puede elegir otro."""
    try:
        roles, borradores = sv.roles_para_pdf(request.user, request.GET.get('rol', ''))
    except ValueError:
        return HttpResponseBadRequest('Manual no válido.')
    secciones = sv.secciones_para(roles, borradores=borradores)
    etiqueta = sv.etiqueta_de_roles(roles)
    version = sv.version_manual()
    ahora = timezone.localtime()
    quien = request.user.get_full_name() or request.user.get_username()
    cuerpos = {s.slug: sv.render_cuerpo(s, roles, para_pdf=True, bajar_titulos=2) for s in secciones}
    DescargaManual.objects.create(usuario=request.user, rol_exportado=etiqueta, version=version,
                                  secciones=len(secciones))
    grupos = [(cap, [(s, cuerpos[s.slug]) for s in lista]) for cap, lista in sv.capitulos(secciones)]
    contexto = {
        'titulo': 'Manual de procedimientos',
        'manual_etiqueta': 'Completo' if etiqueta == 'COMPLETO' else ' + '.join(
            sv.ETIQUETAS_ROL[r] for r in etiqueta.split('+')),
        'version': version,
        'fecha': ahora,
        'descargado_por': quien,
        'pie_css': sv.css_cadena(f'Manual {etiqueta.title()} · versión {version} · {ahora:%d/%m/%Y} · '
                                 f'Descargado por {quien}'),
        'capitulos': grupos,
        'incluye_borradores': borradores and any(s.borrador for s in secciones),
    }
    nombre = nombre_archivo('Manual', etiqueta, ahora.date())
    return respuesta_pdf('manual/pdf_manual.html', contexto, nombre, request=request, descargar=True)
