"""
Vistas de Indicadores (Issue #402). Solo Dirección (superusuario); toda la lógica
vive en `indicadores/services/`.
"""
from datetime import date
from functools import wraps

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core_erp.documentos import nombre_archivo, respuesta_pdf
from core_erp.excel import respuesta_excel

from .exportacion import hojas
from .forms import PlanForm
from .models import ParametrosNegocio
from .services.base import ETIQUETAS, TIPOS, q2
from .services.plan_anual import PlanAnualService
from .services.tablero import TableroService, overrides_desde

SUFIJO = {'EVENTO': 'evento', 'PASADIA': 'pasadia', 'HOSPEDAJE': 'hospedaje'}   # campos de ParametrosNegocio


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


def _anio(request, por_defecto: int) -> int:
    try:
        anio = int(request.GET.get('anio', ''))
    except ValueError:
        return por_defecto
    return anio if 2000 <= anio <= 2100 else por_defecto


def _guardar_numeros(form: PlanForm, p: ParametrosNegocio, usuario, hoy: date) -> ParametrosNegocio:
    """Un `ParametrosNegocio` nuevo y activo con estos números; el vigente se desactiva (no se borra nada)."""
    omitir = {'id', 'created_by', 'updated_by', 'created_at', 'updated_at'}
    nuevo = ParametrosNegocio(**{c.name: getattr(p, c.name) for c in p._meta.concrete_fields if c.name not in omitir})
    nuevo.vigente_desde, nuevo.activo = hoy, True
    servicios = form.servicios(p)
    for tipo in TIPOS:
        s = servicios.get(tipo)
        setattr(nuevo, f'precio_venta_{SUFIJO[tipo]}', s['precio'] if s else None)
        setattr(nuevo, f'costo_directo_{SUFIJO[tipo]}', s['costo'] if s else None)
    nuevo.costo_fijo_mensual_override = q2(form.cleaned_data['cf_anual'] / 12)
    nuevo.created_by = nuevo.updated_by = usuario
    nuevo.full_clean()
    nuevo.save()
    return nuevo


@solo_direccion
def plan(request):
    """
    «Plan óptimo»: qué vender en el año para dejar más utilidad. Abre ya lleno; «Calcular» recalcula con lo
    que haya en el formulario sin guardar nada, y «Guardar estos números» crea un Parámetro nuevo y activo.
    """
    from django.utils import timezone

    hoy = timezone.localdate()
    p = ParametrosNegocio.vigente()
    iniciales = PlanAnualService.valores_iniciales(p, hoy)
    inicial = PlanForm.iniciales(_anio(request, hoy.year + 1), iniciales)

    # Al abrir se usan los números iniciales como si ya se hubiera pulsado «Calcular».
    form = PlanForm(request.POST if request.method == 'POST' else inicial)

    resultado, filas = None, []
    if form.is_valid():
        servicios = form.servicios(p)
        if request.method == 'POST' and request.POST.get('accion') == 'guardar':
            try:
                _guardar_numeros(form, p, request.user, hoy)
            except ValidationError as error:
                for mensaje in error.messages:
                    messages.error(request, mensaje)
            else:
                messages.success(request, 'Números guardados: son los Parámetros vigentes.')
                return redirect(f'{reverse("indicadores:plan")}?anio={form.cleaned_data["anio"]}')
        resultado = PlanAnualService.generar(
            form.cleaned_data['anio'], p, servicios, form.cleaned_data['cf_anual'], hoy,
            con_umbrales=True, iniciales=iniciales)
        filas = [{'tipo': t, 'nombre': ETIQUETAS[t], 'servicio': resultado['servicios'].get(t)} for t in TIPOS]
    return render(request, 'indicadores/plan.html', {
        'title': 'Plan óptimo', 'form': form, 'plan': resultado, 'filas': filas,
        'campos': [{'tipo': t, 'precio': form[f'precio_{t}'], 'costo': form[f'costo_{t}']} for t in TIPOS],
        'avisos': iniciales['avisos'],
    })
