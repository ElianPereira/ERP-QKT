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
from core_erp.excel import respuesta_excel

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
    puede_pagos = request.user.has_perm('comercial.view_pago')
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
        'puede_pagos': puede_pagos,
        'inicio_mes': timezone.now().date().replace(day=1),
        'puede_facturacion': puede_facturacion,
        'puede_conciliacion': request.user.has_perm('contabilidad.view_conciliacionbancaria'),
        'puede_depositos': request.user.has_perm('comercial.view_depositogarantia'),
        'puede_cortesias': request.user.has_perm('comercial.view_descuentoaplicado'),
        'puede_compras': request.user.has_perm('comercial.view_compra'),
        'puede_nomina': request.user.has_perm('nomina.view_recibonomina'),
        'puede_kooxi': request.user.has_perm('comunicacion.view_conversacionwhatsapp'),
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
    nivel = int(request.GET.get('nivel', '4'))

    unidad = None
    if unidad_id:
        unidad = get_object_or_404(UnidadNegocio, pk=unidad_id)

    datos = BalanzaComprobacionService.generar(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        unidad_negocio=unidad,
        nivel_detalle=nivel,
    )

    # Totales solo sobre las cuentas raíz: cada fila ya acumula sus subcuentas,
    # sumar padre e hija contaría el mismo importe dos veces.
    raices = [r for r in datos if r['es_raiz']]
    total_si_debe = sum((r['saldo_inicial_debe'] for r in raices), Decimal('0.00'))
    total_si_haber = sum((r['saldo_inicial_haber'] for r in raices), Decimal('0.00'))
    total_cargos = sum((r['cargos'] for r in raices), Decimal('0.00'))
    total_abonos = sum((r['abonos'] for r in raices), Decimal('0.00'))
    total_sf_debe = sum((r['saldo_final_debe'] for r in raices), Decimal('0.00'))
    total_sf_haber = sum((r['saldo_final_haber'] for r in raices), Decimal('0.00'))

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


# ==========================================
# 11. RENTABILIDAD POR EVENTO
# ==========================================

@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def reporte_rentabilidad(request):
    """Rentabilidad por evento + gastos operativos, en PDF o Excel."""
    from .services.comercial import RentabilidadEventosService

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', date(timezone.now().year, 1, 1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    estado = request.GET.get('estado_cotizacion') or None
    excel = request.GET.get('formato') == 'excel'

    datos = RentabilidadEventosService.generar(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin, estado=estado)
    datos['titulo'] = 'Rentabilidad por evento'
    _registrar_reporte(request, 'RENTABILIDAD', fecha_inicio, fecha_fin,
                       formato='EXCEL' if excel else 'PDF', parametros={'estado': estado})

    if excel:
        return respuesta_excel(datos['titulo'], [
            ('Eventos', ['Folio', 'Fecha', 'Cliente', 'Evento', 'Venta total', 'IVA trasladado',
                         'Ingreso sin IVA', 'Gasto facturado', 'IVA acreditable', 'Gasto sin factura',
                         'Utilidad bruta'],
             [(e['folio'], e['fecha'], e['cliente'], e['evento'], e['venta'], e['iva'], e['base'],
               e['gasto_fiscal'], e['iva_acreditable'], e['gasto_nofiscal'], e['utilidad'])
              for e in datos['eventos']]),
            ('Gastos operativos', ['Categoría', 'Con factura', 'Base', 'IVA', 'Total'],
             [(g['nombre'], 'Sí', g['base'], g['iva'], g['base'] + g['iva']) for g in datos['operativos_fiscales']]
             + [(g['nombre'], 'No', None, None, g['total']) for g in datos['operativos_nofiscales']]),
        ], nombre_archivo('Rentabilidad', fecha_inicio, fecha_fin, extension='xlsx'))

    filename = nombre_archivo('Rentabilidad', fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_rentabilidad.html', datos, filename)


# ==========================================
# 12. PAGOS RECIBIDOS
# ==========================================

@staff_member_required
@permission_required('comercial.view_pago', raise_exception=True)
def reporte_pagos(request):
    """Pagos del periodo (cobros, reembolsos y cortesías), en PDF o Excel."""
    from .services.comercial import PagosRecibidosService

    fecha_inicio = _parse_fecha(request, 'fecha_inicio', timezone.now().date().replace(day=1))
    fecha_fin = _parse_fecha(request, 'fecha_fin', timezone.now().date())
    excel = request.GET.get('formato') == 'excel'

    datos = PagosRecibidosService.generar(fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
    datos['titulo'] = 'Pagos recibidos'
    _registrar_reporte(request, 'PAGOS', fecha_inicio, fecha_fin, formato='EXCEL' if excel else 'PDF')

    if excel:
        return respuesta_excel(datos['titulo'], [
            ('Pagos', ['Fecha', 'Folio', 'Cliente', 'Evento', 'Tipo', 'Método', 'Referencia',
                       'Registró', 'Importe'],
             [(p.fecha_pago, f"COT-{p.cotizacion_id:03d}", p.cotizacion.cliente.nombre,
               p.cotizacion.nombre_evento, p.get_tipo_display(), p.get_metodo_display(),
               p.referencia, p.usuario.get_username() if p.usuario else 'Sistema',
               -p.monto if p.tipo == 'REEMBOLSO' else p.monto)
              for p in datos['pagos']]),
        ], nombre_archivo('Pagos', fecha_inicio, fecha_fin, extension='xlsx'))

    filename = nombre_archivo('Pagos', fecha_inicio, fecha_fin)
    return _render_pdf(request, 'reportes/pdf_pagos.html', datos, filename)


# ==========================================
# FASE 5: REPORTES NUEVOS (Issue #373)
# ==========================================

def _periodo(request, default_inicio=None):
    hoy = timezone.now().date()
    return (_parse_fecha(request, 'fecha_inicio', default_inicio or hoy.replace(day=1)),
            _parse_fecha(request, 'fecha_fin', hoy))


def _entregar(request, tipo, nombre, plantilla, datos, hojas):
    """Registra el reporte y responde en PDF o, con `?formato=excel`, en Excel."""
    excel = request.GET.get('formato') == 'excel'
    fi, ff = datos['fecha_inicio'], datos['fecha_fin']
    _registrar_reporte(request, tipo, fi, ff, formato='EXCEL' if excel else 'PDF')
    if excel:
        return respuesta_excel(datos['titulo'], hojas(), nombre_archivo(nombre, fi, ff, extension='xlsx'))
    return _render_pdf(request, plantilla, datos, nombre_archivo(nombre, fi, ff))


@staff_member_required
@permission_required('nomina.view_recibonomina', raise_exception=True)
def reporte_nomina(request):
    """Nómina por periodo: recibos por empleado, pagado y pendiente."""
    from .services.operacion import NominaPeriodoService, inicio_del_recibo

    datos = NominaPeriodoService.generar(*_periodo(request))
    datos['titulo'] = 'Nómina por periodo'
    return _entregar(request, 'NOMINA', 'Nomina', 'reportes/pdf_nomina.html', datos, lambda: [
        ('Por empleado', ['Empleado', 'Puesto', 'Recibos', 'Horas', 'Total', 'Pagado', 'Pendiente'],
         [(e['nombre'], e['puesto'], e['recibos'], e['horas'], e['total'], e['pagado'], e['pendiente'])
          for e in datos['empleados']]),
        ('Recibos', ['Folio', 'Empleado', 'Inicio', 'Periodo', 'Horas', 'Tarifa', 'Total', 'Estado',
                     'Fecha de pago'],
         [(f"NOM-{r.pk:03d}", r.empleado.nombre, inicio_del_recibo(r), r.periodo, r.horas_trabajadas,
           r.tarifa_aplicada, r.total_pagado, r.get_estado_display(), r.fecha_pago)
          for r in datos['recibos']]),
    ])


@staff_member_required
@permission_required('comercial.view_compra', raise_exception=True)
def reporte_gastos(request):
    """Gastos (compras) por categoría, con y sin factura."""
    from .services.finanzas import GastosCategoriaService

    datos = GastosCategoriaService.generar(*_periodo(request))
    datos['titulo'] = 'Gastos por categoría'
    return _entregar(request, 'GASTOS', 'Gastos', 'reportes/pdf_gastos.html', datos, lambda: [
        ('Por categoría', ['Categoría', 'Compras', 'Base', 'IVA', 'Retenciones', 'Total',
                           'Con factura', 'Sin factura'],
         [(c['nombre'], c['compras'], c['base'], c['iva'], c['retenciones'], c['total'],
           c['con_factura'], c['sin_factura']) for c in datos['categorias']]),
        ('Compras', ['Fecha', 'Proveedor', 'RFC', 'Categoría', 'UUID', 'Base', 'IVA', 'Retenciones',
                     'Total'],
         [(c.fecha_emision, c.proveedor_nombre, c.rfc_emisor, c.get_categoria_display(), c.uuid or '',
           c.base, c.iva, c.retenciones, c.total) for c in datos['compras']]),
    ])


@staff_member_required
@permission_required('contabilidad.view_movimientocontable', raise_exception=True)
def reporte_flujo(request):
    """Flujo de efectivo de las cuentas bancarias según pólizas aplicadas."""
    from .services.finanzas import FlujoEfectivoService

    datos = FlujoEfectivoService.generar(*_periodo(request))
    datos['titulo'] = 'Flujo de efectivo'
    return _entregar(request, 'FLUJO', 'FlujoEfectivo', 'reportes/pdf_flujo.html', datos, lambda: [
        ('Por origen', ['Origen', 'Entradas', 'Salidas', 'Neto'],
         [(o['nombre'], o['entradas'], o['salidas'], o['neto']) for o in datos['origenes']]),
        ('Por mes', ['Mes', 'Entradas', 'Salidas', 'Neto', 'Saldo al cierre'],
         [(m['mes'], m['entradas'], m['salidas'], m['neto'], m['saldo']) for m in datos['meses']]),
        ('Por cuenta', ['Cuenta', 'Saldo inicial', 'Entradas', 'Salidas', 'Saldo final'],
         [(c['nombre'], c['saldo_inicial'], c['entradas'], c['salidas'], c['saldo_final'])
          for c in datos['cuentas']]),
    ])


@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def reporte_ocupacion(request):
    """Ocupación de la Quinta: días vendidos, bloqueados y libres."""
    from .services.operacion import OcupacionService

    datos = OcupacionService.generar(*_periodo(request))
    datos['titulo'] = 'Ocupación'
    return _entregar(request, 'OCUPACION', 'Ocupacion', 'reportes/pdf_ocupacion.html', datos, lambda: [
        ('Por servicio', ['Servicio', 'Reservaciones', 'Días', 'Ocupación %', 'Personas', 'Venta'],
         [(t['nombre'], t['reservaciones'], t['dias'], float(t['porcentaje']), t['personas'], t['venta'])
          for t in datos['tipos']]),
        ('Reservaciones', ['Folio', 'Desde', 'Hasta', 'Servicio', 'Cliente', 'Evento', 'Personas',
                           'Días en el periodo', 'Estado', 'Venta'],
         [(r['folio'], r['inicio'], r['fin'], r['tipo'], r['cliente'], r['evento'], r['personas'],
           r['dias'], r['estado'], r['venta']) for r in datos['reservaciones']]),
    ])


@staff_member_required
@permission_required('comercial.view_depositogarantia', raise_exception=True)
def reporte_depositos(request):
    """Depósitos en garantía: movimientos del periodo y depósitos abiertos."""
    from .services.operacion import DepositosGarantiaService

    datos = DepositosGarantiaService.generar(*_periodo(request))
    datos['titulo'] = 'Depósitos en garantía'
    return _entregar(request, 'DEPOSITOS', 'Depositos', 'reportes/pdf_depositos.html', datos, lambda: [
        ('Movimientos', ['Fecha', 'Folio', 'Cliente', 'Tipo', 'Método', 'Referencia', 'Monto'],
         [(m.fecha, f"COT-{m.deposito.cotizacion_id:03d}", m.deposito.cotizacion.cliente.nombre,
           m.get_tipo_display(), m.get_metodo_display(), m.referencia, m.monto)
          for m in datos['movimientos']]),
        ('Abiertos', ['Folio', 'Cliente', 'Fin del servicio', 'Monto', 'Recibido', 'En custodia',
                      'Por recibir', 'Devolver a más tardar', 'Vencido', 'Estado'],
         [(a['folio'], a['cliente'], a['fecha_servicio'], a['monto'], a['recibido'], a['en_custodia'],
           a['por_recibir'], a['limite'], 'Sí' if a['vencido'] else 'No', a['estado'])
          for a in datos['abiertos']]),
    ])


@staff_member_required
@permission_required('comercial.view_descuentoaplicado', raise_exception=True)
def reporte_cortesias(request):
    """Cortesías, promociones y condonaciones del periodo."""
    from .services.operacion import CortesiasDescuentosService

    datos = CortesiasDescuentosService.generar(*_periodo(request))
    datos['titulo'] = 'Cortesías y descuentos'
    return _entregar(request, 'CORTESIAS', 'Cortesias', 'reportes/pdf_cortesias.html', datos, lambda: [
        ('Descuentos', ['Fecha', 'Folio', 'Cliente', 'Regla', 'Tipo', 'Estado de la cotización',
                        'Cuenta', 'Monto sin IVA'],
         [(timezone.localtime(a.fecha_aplicacion).date(), f"COT-{a.cotizacion_id:03d}",
           a.cotizacion.cliente.nombre, a.descuento.nombre,
           'Cortesía' if a.descuento.es_cortesia else 'Promoción', a.cotizacion.get_estado_display(),
           'Sí' if a.venta_real else 'No', a.monto_aplicado) for a in datos['aplicados']]),
        ('Condonaciones', ['Fecha', 'Folio', 'Cliente', 'Referencia', 'Monto condonado', 'Sin IVA'],
         [(p.fecha_pago, f"COT-{p.cotizacion_id:03d}", p.cotizacion.cliente.nombre, p.referencia, p.monto, p.base)
          for p in datos['condonaciones']]),
    ])


@staff_member_required
@permission_required('contabilidad.view_conciliacionbancaria', raise_exception=True)
def reporte_conciliacion(request):
    """Conciliaciones bancarias del periodo y movimientos del banco sin asiento."""
    from .services.finanzas import ConciliacionBancariaService

    datos = ConciliacionBancariaService.generar(*_periodo(request))
    datos['titulo'] = 'Conciliación bancaria'
    return _entregar(request, 'CONCILIACION', 'Conciliacion', 'reportes/pdf_conciliacion.html', datos, lambda: [
        ('Conciliaciones', ['Cuenta', 'Periodo', 'Saldo banco', 'Saldo libros', 'Cargos banco sin póliza',
                            'Abonos banco sin póliza', 'Salidas no cobradas', 'Depósitos en tránsito',
                            'Diferencia arrastrada', 'Diferencia', 'Estado'],
         [(str(c.cuenta_bancaria), f"{c.mes:02d}/{c.anio}", c.saldo_segun_banco, c.saldo_segun_libros,
           c.cargos_banco_no_registrados, c.abonos_banco_no_registrados, c.cargos_empresa_no_cobrados,
           c.abonos_empresa_no_abonados, c.diferencia_arrastrada, c.diferencia, c.get_estado_display())
          for c in datos['conciliaciones']]),
        ('Sin conciliar', ['Fecha', 'Cuenta', 'Descripción', 'Referencia', 'Cargo', 'Abono'],
         [(m.fecha, str(m.estado_cuenta.cuenta_bancaria), m.descripcion, m.referencia, m.cargo, m.abono)
          for m in datos['pendientes']]),
    ])


@staff_member_required
@permission_required('comunicacion.view_conversacionwhatsapp', raise_exception=True)
def reporte_kooxi(request):
    """Uso, costo y resultados del agente de WhatsApp en el periodo."""
    from .services.operacion import KooxiService

    datos = KooxiService.generar(*_periodo(request))
    datos['titulo'] = 'Agente Kooxi'
    return _entregar(request, 'KOOXI', 'Kooxi', 'reportes/pdf_kooxi.html', datos, lambda: [
        ('Resumen', ['Indicador', 'Valor'], [
            ('Conversaciones activas', datos['conversaciones_activas']),
            ('Mensajes de clientes', datos['mensajes_clientes']),
            ('Respuestas de la IA', datos['respuestas_ia']),
            ('Respuestas del equipo', datos['respuestas_equipo']),
            ('Pases a persona', datos['pases_a_humano']),
            ('Respuestas detenidas por el filtro', datos['bloqueadas']),
            ('Conversaciones nuevas', datos['conversion']['nuevas']),
            ('… con cotización', datos['conversion']['con_cotizacion']),
            ('… con pago', datos['conversion']['con_pago']),
            ('Costo (USD)', float(datos['consumo']['costo_usd'])),
        ]),
        ('Consumo por modelo', ['Modelo', 'Respuestas', 'Tokens de entrada', 'Tokens de salida',
                                'Lectura de caché', 'Escritura de caché', 'Costo (USD)'],
         [(f['modelo'], f['respuestas'], f['entrada'], f['salida'], f['cache_lectura'],
           f['cache_escritura'], float(f['costo_usd']) if f['costo_usd'] is not None else None)
          for f in datos['consumo']['por_modelo']]),
        ('Pases a persona', ['Motivo', 'Veces'], [(m['motivo'], m['veces']) for m in datos['motivos']]),
        ('Sin respuesta', ['Pregunta', 'Veces'], [(p['pregunta'], p['veces']) for p in datos['sin_respuesta']]),
    ])
