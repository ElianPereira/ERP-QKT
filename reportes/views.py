"""
Vistas del Módulo de Reportes
==============================
Selector centralizado + generación de cada reporte en HTML y PDF.

ERP Quinta Ko'ox Tanil
"""
from datetime import date
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from core_erp.documentos import nombre_archivo, respuesta_pdf

from .models import ReporteGenerado

# ==========================================
# UTILIDADES
# ==========================================

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
    """PDF con el sistema de documentos QKT (core_erp/documentos.py)."""
    return respuesta_pdf(template, context, filename, request=request)


# ==========================================
# SELECTOR PRINCIPAL
# ==========================================

@staff_member_required
def selector_reportes(request):
    """Página principal del centro de reportes."""
    from contabilidad.models import CuentaContable, UnidadNegocio

    puede_contabilidad = request.user.has_perm('contabilidad.view_movimientocontable')
    puede_comercial = request.user.has_perm('comercial.view_cotizacion')
    puede_facturacion = request.user.has_perm('facturacion.view_solicitudfactura')

    context = {
        'title': 'Centro de Reportes',
        'unidades_negocio': UnidadNegocio.objects.filter(activa=True),
        'cuentas_padre': CuentaContable.objects.filter(
            activa=True, permite_movimientos=False, nivel__lte=2
        ).order_by('codigo_sat'),
        'cuentas_movimiento': CuentaContable.objects.filter(
            activa=True, permite_movimientos=True
        ).order_by('codigo_sat'),
        'hoy': timezone.now().date(),
        'inicio_anio': date(timezone.now().year, 1, 1),
        'puede_contabilidad': puede_contabilidad,
        'puede_comercial': puede_comercial,
        'puede_facturacion': puede_facturacion,
    }
    return render(request, 'reportes/selector.html', context)


# ==========================================
# 1. BALANZA DE COMPROBACIÓN
# ==========================================

@staff_member_required
@permission_required('contabilidad.view_movimientocontable', raise_exception=True)
def reporte_balanza(request):
    """Genera Balanza de Comprobación en PDF."""
    from contabilidad.models import UnidadNegocio
    from contabilidad.services import BalanzaComprobacionService

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
        'titulo': 'Balanza de comprobación',
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

    filename = nombre_archivo('Balanza', fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_balanza.html', context, filename)


# ==========================================
# 2. ESTADO DE RESULTADOS
# ==========================================

@staff_member_required
@permission_required('contabilidad.view_movimientocontable', raise_exception=True)
def reporte_estado_resultados(request):
    """Genera Estado de Resultados en PDF."""
    from contabilidad.models import UnidadNegocio

    from .services.contabilidad import EstadoResultadosService

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
    datos['titulo'] = 'Estado de resultados'

    _registrar_reporte(request, 'EDO_RESULTADOS', fecha_inicio, fecha_fin, parametros={
        'unidad': str(unidad) if unidad else None,
    })

    filename = nombre_archivo('EstadoResultados', fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_estado_resultados.html', datos, filename)


# ==========================================
# 3. BALANCE GENERAL
# ==========================================

@staff_member_required
@permission_required('contabilidad.view_movimientocontable', raise_exception=True)
def reporte_balance_general(request):
    """Genera Balance General en PDF."""
    from contabilidad.models import UnidadNegocio

    from .services.contabilidad import BalanceGeneralService

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
    datos['titulo'] = 'Balance general'

    _registrar_reporte(request, 'BALANCE_GRAL', fecha_fin, fecha_fin, parametros={
        'unidad': str(unidad) if unidad else None,
    })

    filename = nombre_archivo('BalanceGeneral', fecha_fin)
    return _render_pdf(request, 'reportes/pdf_balance_general.html', datos, filename)


# ==========================================
# 4. LIBRO MAYOR
# ==========================================

@staff_member_required
@permission_required('contabilidad.view_movimientocontable', raise_exception=True)
def reporte_libro_mayor(request):
    """Genera Libro Mayor de una cuenta en PDF."""
    from contabilidad.models import UnidadNegocio

    from .services.contabilidad import LibroMayorService

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
    datos['titulo'] = f"Libro mayor {datos['cuenta'].codigo_sat} {datos['cuenta'].nombre}"

    _registrar_reporte(request, 'LIBRO_MAYOR', fecha_inicio, fecha_fin, parametros={
        'cuenta': str(datos['cuenta']),
        'unidad': str(unidad) if unidad else None,
    })

    filename = nombre_archivo('LibroMayor', datos['cuenta'].codigo_sat, fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_libro_mayor.html', datos, filename)


# ==========================================
# 5. AUXILIAR DE CUENTAS
# ==========================================

@staff_member_required
@permission_required('contabilidad.view_movimientocontable', raise_exception=True)
def reporte_auxiliar(request):
    """Genera Auxiliar de Cuentas (subcuentas de un padre) en PDF."""
    from contabilidad.models import UnidadNegocio

    from .services.contabilidad import AuxiliarCuentasService

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
    datos['titulo'] = 'Auxiliar de cuentas'

    _registrar_reporte(request, 'AUXILIAR', fecha_inicio, fecha_fin, parametros={
        'cuenta_padre': str(datos['cuenta_padre']),
        'unidad': str(unidad) if unidad else None,
    })

    filename = nombre_archivo('Auxiliar', datos['cuenta_padre'].codigo_sat, fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_auxiliar.html', datos, filename)


# ==========================================
# 6. CxC (CARTERA) - ANTIGÜEDAD DE SALDOS
# ==========================================

@staff_member_required
@permission_required('comercial.view_pago', raise_exception=True)
def reporte_cxc(request):
    """Genera reporte de CxC / Antigüedad de Saldos en PDF."""
    from .services.comercial import CxCCarteraService

    fecha_corte = _parse_fecha(request, 'fecha_corte', timezone.now().date())
    datos = CxCCarteraService.generar(fecha_corte=fecha_corte)
    datos['titulo'] = 'Cartera de clientes'

    _registrar_reporte(request, 'CXC_CARTERA', fecha_corte, fecha_corte)

    filename = nombre_archivo('Cartera', fecha_corte)
    return _render_pdf(request, 'reportes/pdf_cxc.html', datos, filename)


# ==========================================
# 7. COTIZACIONES POR PERÍODO
# ==========================================

@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
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
    datos['titulo'] = 'Cotizaciones por periodo'
    # Mismos tonos que la lista de cotizaciones del admin.
    from comercial.admin import CotizacionAdmin
    for fila in datos['cotizaciones']:
        fila['estado_tono'] = CotizacionAdmin.TONOS_ESTADO.get(fila['estado_clave'], 'neutro')

    _registrar_reporte(request, 'COT_PERIODO', fecha_inicio, fecha_fin, parametros={
        'estado': estado,
    })

    filename = nombre_archivo('Cotizaciones', fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_cotizaciones.html', datos, filename)


# ==========================================
# 10. FACTURAS EMITIDAS
# ==========================================

@staff_member_required
@permission_required('facturacion.view_solicitudfactura', raise_exception=True)
def reporte_facturas(request):
    """Genera reporte de facturas emitidas por período en PDF."""
    from .services.facturacion import FacturasEmitidasService

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())

    datos = FacturasEmitidasService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
    )
    datos['titulo'] = 'Facturas emitidas'

    _registrar_reporte(request, 'FACTURAS', fecha_inicio, fecha_fin)

    filename = nombre_archivo('Facturas', fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_facturas.html', datos, filename)
