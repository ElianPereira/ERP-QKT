"""
Vistas de Indicadores (Issue #402). Solo Dirección (superusuario); toda la lógica
vive en `indicadores/services/`.
"""
from datetime import date
from functools import wraps

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone

from core_erp.documentos import nombre_archivo, respuesta_pdf
from core_erp.excel import respuesta_excel

from .exportacion import hojas
from .services.tablero import TableroService, overrides_desde


def solo_direccion(vista):
    @wraps(vista)
    @staff_member_required
    def envoltura(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return vista(request, *args, **kwargs)
    return envoltura


def _mes(request):
    """(año, mes) del `?mes=AAAA-MM`; sin él o inválido, el mes en curso."""
    hoy = timezone.localdate()
    try:
        anio, mes = (int(x) for x in request.GET.get('mes', '').split('-'))
        date(anio, mes, 1)
        return anio, mes
    except (ValueError, TypeError):
        return hoy.year, hoy.month


@solo_direccion
def tablero(request):
    anio, mes = _mes(request)
    overrides = overrides_desde(request.GET)
    datos = TableroService.generar(anio, mes, overrides)
    return render(request, 'indicadores/tablero.html', {
        'title': 'Indicadores', 'd': datos, 'mes_valor': f'{anio}-{mes:02d}',
        'simulando': bool(overrides),
        'tendencia': [{'mes': f['etiqueta'], 'venta': str(f['venta_neta']), 'ocupacion': str(f['ocupacion'])}
                      for f in datos['tendencia']],
        'linea_equilibrio': {
            'pesos': str(datos['equilibrio_pesos']) if datos['equilibrio_pesos'] is not None else None,
            'ocupacion': (str(datos['equilibrio_ocupacion'])
                          if datos['equilibrio_ocupacion'] is not None else None)},
    })


@solo_direccion
def exportar(request):
    """PDF «Indicadores del mes» o, con `?formato=excel`, el mismo contenido en Excel."""
    anio, mes = _mes(request)
    datos = TableroService.generar(anio, mes, overrides_desde(request.GET))
    nombre_base = f'{anio}-{mes:02d}'
    if request.GET.get('formato') == 'excel':
        return respuesta_excel('Indicadores del mes', list(hojas(datos)),
                               nombre_archivo('Indicadores', nombre_base, extension='xlsx'))
    return respuesta_pdf('indicadores/pdf_indicadores.html', {'titulo': 'Indicadores del mes', 'd': datos},
                         nombre_archivo('Indicadores', nombre_base), request=request)
