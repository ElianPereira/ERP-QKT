"""
Vistas del Módulo de Reportes
==============================
Selector centralizado + generación de cada reporte en HTML y PDF.

ERP Quinta Ko'ox Tanil
"""
import os
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from django.utils import timezone
from weasyprint import HTML

from .models import ReporteGenerado


# ==========================================
# UTILIDADES
# ==========================================

def _logo_url():
    """Genera la URL del logo para WeasyPrint."""
    ruta = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    return f"file://{ruta}"


def _parse_fecha(request, campo, default=None):
    """Parsea fecha de GET params con fallback."""
    valor = request.GET.get(campo, '')
    if valor:
        try:
            return date.fromisoformat(valor)
        except (ValueError, TypeError):
            pass
    return default


def _registrar_reporte(request, tipo, fecha_inicio, fecha_fin, formato='PDF', parametros=None):
    """Registra el reporte en el historial de auditoría."""
    ReporteGenerado.objects.create(
        tipo=tipo,
        formato=formato,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        parametros=parametros or {},
        created_by=request.user,
    )


def _render_pdf(request, template, context, filename):
    """Renderiza un template a PDF con WeasyPrint."""
    context['logo_url'] = _logo_url()
    context['fecha_impresion'] = timezone.now()
    html_string = render_to_string(template, context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html_string).write_pdf(response)
    return response


# ==========================================
# SELECTOR PRINCIPAL
# ==========================================

@staff_member_required
def selector_reportes(request):
    """Página principal del centro de reportes."""
    from contabilidad.models import CuentaContable, UnidadNegocio
    from airbnb.models import AnuncioAirbnb

    context = {
        'title': 'Centro de Reportes',
        'unidades_negocio': UnidadNegocio.objects.filter(activa=True),
        'cuentas_padre': CuentaContable.objects.filter(
            activa=True, permite_movimientos=False, nivel__lte=2
        ).order_by('codigo_sat'),
        'cuentas_movimiento': CuentaContable.objects.filter(
            activa=True, permite_movimientos=True
        ).order_by('codigo_sat'),
        'anuncios_airbnb': AnuncioAirbnb.objects.filter(activo=True),
        'hoy': timezone.now().date(),
        'inicio_anio': date(timezone.now().year, 1, 1),
    }
    return render(request, 'reportes/selector.html', context)


# ==========================================
# 1. BALANZA DE COMPROBACIÓN
# ==========================================

@staff_member_required
def reporte_balanza(request):
    """Genera Balanza de Comprobación en PDF."""
    from contabilidad.services import BalanzaComprobacionService
    from contabilidad.models import UnidadNegocio

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    unidad_id = request.GET.get('unidad_negocio')
    nivel = int(request.GET.get('nivel', '3'))

    unidad = None
    if unidad_id:
        unidad = get_object_or_404(UnidadNegocio, pk=unidad_id)

    datos = BalanzaComprobacionService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        unidad_negocio=unidad,
        nivel_detalle=nivel,
    )

    # Totales
    total_si_debe = sum(Decimal(str(r['saldo_inicial_debe'])) for r in datos)
    total_si_haber = sum(Decimal(str(r['saldo_inicial_haber'])) for r in datos)
    total_cargos = sum(Decimal(str(r['cargos'])) for r in datos)
    total_abonos = sum(Decimal(str(r['abonos'])) for r in datos)
    total_sf_debe = sum(Decimal(str(r['saldo_final_debe'])) for r in datos)
    total_sf_haber = sum(Decimal(str(r['saldo_final_haber'])) for r in datos)

    context = {
        'titulo': 'Balanza de Comprobación',
        'fecha_inicio': fecha_inicio,
        'fecha_fin': fecha_fin,
        'unidad': unidad,
        'nivel': nivel,
        'datos': datos,
        'total_si_debe': total_si_debe,
        'total_si_haber': total_si_haber,
        'total_cargos': total_cargos,
        'total_abonos': total_abonos,
        'total_sf_debe': total_sf_debe,
        'total_sf_haber': total_sf_haber,
    }

    _registrar_reporte(request, 'BALANZA', fecha_inicio, fecha_fin, parametros={
        'unidad': str(unidad) if unidad else None, 'nivel': nivel,
    })

    filename = f"Balanza_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_balanza.html', context, filename)


# ==========================================
# 2. ESTADO DE RESULTADOS
# ==========================================

@staff_member_required
def reporte_estado_resultados(request):
    """Genera Estado de Resultados en PDF."""
    from .services.contabilidad import EstadoResultadosService
    from contabilidad.models import UnidadNegocio

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    unidad_id = request.GET.get('unidad_negocio')

    unidad = None
    if unidad_id:
        unidad = get_object_or_404(UnidadNegocio, pk=unidad_id)

    datos = EstadoResultadosService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        unidad_negocio=unidad,
    )
    datos['unidad'] = unidad
    datos['titulo'] = 'Estado de Resultados'

    _registrar_reporte(request, 'EDO_RESULTADOS', fecha_inicio, fecha_fin, parametros={
        'unidad': str(unidad) if unidad else None,
    })

    filename = f"EdoResultados_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_estado_resultados.html', datos, filename)


# ==========================================
# 3. BALANCE GENERAL
# ==========================================

@staff_member_required
def reporte_balance_general(request):
    """Genera Balance General en PDF."""
    from .services.contabilidad import BalanceGeneralService
    from contabilidad.models import UnidadNegocio

    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    unidad_id = request.GET.get('unidad_negocio')

    unidad = None
    if unidad_id:
        unidad = get_object_or_404(UnidadNegocio, pk=unidad_id)

    datos = BalanceGeneralService.generar(
        fecha_corte=fecha_fin,
        unidad_negocio=unidad,
    )
    datos['unidad'] = unidad
    datos['titulo'] = 'Balance General'

    _registrar_reporte(request, 'BALANCE_GRAL', fecha_fin, fecha_fin, parametros={
        'unidad': str(unidad) if unidad else None,
    })

    filename = f"BalanceGeneral_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_balance_general.html', datos, filename)


# ==========================================
# 4. LIBRO MAYOR
# ==========================================

@staff_member_required
def reporte_libro_mayor(request):
    """Genera Libro Mayor de una cuenta en PDF."""
    from .services.contabilidad import LibroMayorService
    from contabilidad.models import UnidadNegocio

    cuenta_id = request.GET.get('cuenta_id')
    if not cuenta_id:
        return HttpResponse("Parámetro 'cuenta_id' requerido.", status=400)

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    unidad_id = request.GET.get('unidad_negocio')

    unidad = None
    if unidad_id:
        unidad = get_object_or_404(UnidadNegocio, pk=unidad_id)

    datos = LibroMayorService.generar(
        cuenta_id=int(cuenta_id),
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        unidad_negocio=unidad,
    )
    datos['unidad'] = unidad
    datos['titulo'] = f"Libro Mayor — {datos['cuenta'].codigo_sat} {datos['cuenta'].nombre}"

    _registrar_reporte(request, 'LIBRO_MAYOR', fecha_inicio, fecha_fin, parametros={
        'cuenta': str(datos['cuenta']),
        'unidad': str(unidad) if unidad else None,
    })

    filename = f"LibroMayor_{datos['cuenta'].codigo_sat}_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_libro_mayor.html', datos, filename)


# ==========================================
# 5. AUXILIAR DE CUENTAS
# ==========================================

@staff_member_required
def reporte_auxiliar(request):
    """Genera Auxiliar de Cuentas (subcuentas de un padre) en PDF."""
    from .services.contabilidad import AuxiliarCuentasService
    from contabilidad.models import UnidadNegocio

    cuenta_padre_id = request.GET.get('cuenta_padre_id')
    if not cuenta_padre_id:
        return HttpResponse("Parámetro 'cuenta_padre_id' requerido.", status=400)

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    unidad_id = request.GET.get('unidad_negocio')

    unidad = None
    if unidad_id:
        unidad = get_object_or_404(UnidadNegocio, pk=unidad_id)

    datos = AuxiliarCuentasService.generar(
        cuenta_padre_id=int(cuenta_padre_id),
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        unidad_negocio=unidad,
    )
    datos['unidad'] = unidad
    datos['titulo'] = f"Auxiliar de Cuentas — {datos['cuenta_padre'].codigo_sat} {datos['cuenta_padre'].nombre}"

    _registrar_reporte(request, 'AUXILIAR', fecha_inicio, fecha_fin, parametros={
        'cuenta_padre': str(datos['cuenta_padre']),
        'unidad': str(unidad) if unidad else None,
    })

    filename = f"Auxiliar_{datos['cuenta_padre'].codigo_sat}_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_auxiliar.html', datos, filename)


# ==========================================
# 6. CxC (CARTERA) - ANTIGÜEDAD DE SALDOS
# ==========================================

@staff_member_required
def reporte_cxc(request):
    """Genera reporte de CxC / Antigüedad de Saldos en PDF."""
    from .services.comercial import CxCCarteraService

    fecha_corte = _parse_fecha(request, 'fecha_corte', timezone.now().date())
    datos = CxCCarteraService.generar(fecha_corte=fecha_corte)
    datos['titulo'] = 'Cartera de Clientes — Antigüedad de Saldos'

    _registrar_reporte(request, 'CXC_CARTERA', fecha_corte, fecha_corte)

    filename = f"CxC_Cartera_{fecha_corte.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_cxc.html', datos, filename)


# ==========================================
# 7. COTIZACIONES POR PERÍODO
# ==========================================

@staff_member_required
def reporte_cotizaciones(request):
    """Genera reporte de cotizaciones por período/estado en PDF."""
    from .services.comercial import CotizacionesPeriodoService

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    estado = request.GET.get('estado_cotizacion') or None

    datos = CotizacionesPeriodoService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        estado=estado,
    )
    datos['titulo'] = 'Cotizaciones por Período'

    _registrar_reporte(request, 'COT_PERIODO', fecha_inicio, fecha_fin, parametros={
        'estado': estado,
    })

    filename = f"Cotizaciones_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_cotizaciones.html', datos, filename)


# ==========================================
# 8. OCUPACIÓN AIRBNB
# ==========================================

@staff_member_required
def reporte_ocupacion(request):
    """Genera reporte de ocupación Airbnb por listing/mes en PDF."""
    from .services.airbnb import OcupacionService

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    anuncio_id = request.GET.get('anuncio_id') or None

    datos = OcupacionService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        anuncio_id=int(anuncio_id) if anuncio_id else None,
    )
    datos['titulo'] = 'Ocupación por Listing'

    _registrar_reporte(request, 'OCUPACION', fecha_inicio, fecha_fin, parametros={
        'anuncio_id': anuncio_id,
    })

    filename = f"Ocupacion_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_ocupacion.html', datos, filename)


# ==========================================
# 9. COMPARATIVO MENSUAL AIRBNB
# ==========================================

@staff_member_required
def reporte_comparativo_airbnb(request):
    """Genera comparativo mensual de ingresos Airbnb en PDF."""
    from .services.airbnb import ComparativoMensualService

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    anuncio_id = request.GET.get('anuncio_id') or None

    datos = ComparativoMensualService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        anuncio_id=int(anuncio_id) if anuncio_id else None,
    )
    datos['titulo'] = 'Comparativo Mensual Airbnb'

    _registrar_reporte(request, 'COMPARATIVO', fecha_inicio, fecha_fin, parametros={
        'anuncio_id': anuncio_id,
    })

    filename = f"ComparativoAirbnb_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_comparativo_airbnb.html', datos, filename)


# ==========================================
# 10. FACTURAS EMITIDAS
# ==========================================

@staff_member_required
def reporte_facturas(request):
    """Genera reporte de facturas emitidas por período en PDF."""
    from .services.facturacion import FacturasEmitidasService

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())

    datos = FacturasEmitidasService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )
    datos['titulo'] = 'Facturas Emitidas'

    _registrar_reporte(request, 'FACTURAS', fecha_inicio, fecha_fin)

    filename = f"Facturas_{fecha_inicio.strftime('%Y%m%d')}_{fecha_fin.strftime('%Y%m%d')}.pdf"
    return _render_pdf(request, 'reportes/pdf_facturas.html', datos, filename)
