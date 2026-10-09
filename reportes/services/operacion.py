"""
Reportes operativos (Issue #373, fase 5)
=========================================
Nómina por periodo, ocupación, depósitos en garantía, cortesías y descuentos,
y uso del agente Kooxi. Solo lectura; todo monto en `Decimal`.
"""
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict

CERO = Decimal('0.00')


def _porcentaje(parte, total) -> Decimal:
    if not total:
        return Decimal('0.0')
    return (Decimal(parte) * 100 / Decimal(total)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


# ==========================================
# NÓMINA POR PERIODO
# ==========================================

_INICIO_PERIODO = re.compile(r'^(\d{4}-\d{2}-\d{2})')


def inicio_del_recibo(recibo) -> date:
    """Primer día que cubre el recibo. `periodo` es texto ('AAAA-MM-DD al AAAA-MM-DD');
    si un recibo viejo no lo trae así, cuenta la fecha en que se generó."""
    encontrado = _INICIO_PERIODO.match(recibo.periodo or '')
    if encontrado:
        try:
            return datetime.strptime(encontrado.group(1), '%Y-%m-%d').date()
        except ValueError:
            pass
    from django.utils import timezone
    return timezone.localtime(recibo.fecha_generacion).date()


class NominaPeriodoService:
    """Recibos cuyo periodo empieza dentro del rango; los cancelados no cuentan."""

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from nomina.models import ReciboNomina

        recibos = [
            r for r in ReciboNomina.objects.exclude(estado='CANCELADO')
            .select_related('empleado').order_by('empleado__nombre', 'fecha_generacion')
            if fecha_inicio <= inicio_del_recibo(r) <= fecha_fin
        ]
        empleados = {}
        horas = total = pagado = CERO
        for r in recibos:
            fila = empleados.setdefault(r.empleado_id, {
                'nombre': r.empleado.nombre, 'puesto': r.empleado.get_puesto_display(),
                'recibos': 0, 'horas': CERO, 'total': CERO, 'pagado': CERO,
            })
            fila['recibos'] += 1
            fila['horas'] += r.horas_trabajadas
            fila['total'] += r.total_pagado
            horas += r.horas_trabajadas
            total += r.total_pagado
            if r.estado == 'PAGADO':
                fila['pagado'] += r.total_pagado
                pagado += r.total_pagado
        for fila in empleados.values():
            fila['pendiente'] = fila['total'] - fila['pagado']
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'recibos': recibos,
            'empleados': sorted(empleados.values(), key=lambda f: f['nombre']),
            'horas': horas, 'total': total, 'pagado': pagado, 'pendiente': total - pagado,
        }


# ==========================================
# OCUPACIÓN
# ==========================================

class OcupacionService:
    """
    Días del periodo ocupados por una reservación con venta real
    (CONFIRMADA, EJECUTADA, CERRADA) o cerrados con un bloqueo de fecha. La
    ocupación se mide sobre los días que sí se podían vender (sin los bloqueados).
    """

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from comercial.disponibilidad import bloqueos_activos, rango_traslapa
        from comercial.models import Cotizacion
        from comercial.views import ESTADOS_VENTA_REAL

        fin_exclusivo = fecha_fin + timedelta(days=1)
        dias_periodo = (fin_exclusivo - fecha_inicio).days
        reservaciones = list(rango_traslapa(
            Cotizacion.objects.filter(estado__in=ESTADOS_VENTA_REAL), fecha_inicio, fin_exclusivo,
        ).select_related('cliente').order_by('fecha_evento'))

        ocupados = {}  # día → tipo de servicio
        etiquetas = dict(Cotizacion.TIPO_SERVICIO_CHOICES)
        por_tipo = {}
        filas = []
        for cot in reservaciones:
            inicio, fin = cot.rango_ocupado()
            dias = [inicio + timedelta(days=i) for i in range((fin - inicio).days)]
            en_periodo = [d for d in dias if fecha_inicio <= d < fin_exclusivo]
            for d in en_periodo:
                ocupados.setdefault(d, cot.tipo_servicio)
            tipo = por_tipo.setdefault(cot.tipo_servicio, {
                'nombre': etiquetas.get(cot.tipo_servicio, cot.tipo_servicio),
                'reservaciones': 0, 'dias': 0, 'personas': 0, 'venta': CERO,
            })
            tipo['reservaciones'] += 1
            tipo['dias'] += len(en_periodo)
            tipo['personas'] += cot.num_personas or 0
            tipo['venta'] += cot.precio_final
            filas.append({
                'folio': f"COT-{cot.id:03d}", 'inicio': inicio, 'fin': fin - timedelta(days=1),
                'tipo': tipo['nombre'], 'cliente': cot.cliente.nombre, 'evento': cot.nombre_evento,
                'personas': cot.num_personas or 0, 'dias': len(en_periodo),
                'estado': cot.get_estado_display(), 'venta': cot.precio_final,
            })

        bloqueados = set()
        for bloqueo in bloqueos_activos(fecha_inicio, fin_exclusivo):
            inicio, fin = bloqueo.rango_ocupado()
            for i in range((fin - inicio).days):
                d = inicio + timedelta(days=i)
                if fecha_inicio <= d < fin_exclusivo and d not in ocupados:
                    bloqueados.add(d)

        disponibles = dias_periodo - len(bloqueados)
        fines = [fecha_inicio + timedelta(days=i) for i in range(dias_periodo)
                 if (fecha_inicio + timedelta(days=i)).weekday() >= 5]
        fines_ocupados = sum(1 for d in fines if d in ocupados)
        tipos = sorted(por_tipo.values(), key=lambda t: -t['dias'])
        for t in tipos:
            t['porcentaje'] = _porcentaje(t['dias'], disponibles)
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'dias_periodo': dias_periodo, 'dias_ocupados': len(ocupados),
            'dias_bloqueados': len(bloqueados),
            'dias_libres': dias_periodo - len(ocupados) - len(bloqueados),
            'ocupacion': _porcentaje(len(ocupados), disponibles),
            'fines_semana': len(fines), 'fines_ocupados': fines_ocupados,
            'ocupacion_fines': _porcentaje(fines_ocupados, len(fines)),
            'tipos': tipos, 'reservaciones': filas,
            'venta': sum((f['venta'] for f in filas), CERO),
        }


# ==========================================
# DEPÓSITOS EN GARANTÍA
# ==========================================

class DepositosGarantiaService:
    """Movimientos del periodo y los depósitos que siguen abiertos a la fecha de corte."""

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from comercial.models import DepositoGarantia, MovimientoDeposito

        movimientos = list(MovimientoDeposito.objects.filter(
            fecha__gte=fecha_inicio, fecha__lte=fecha_fin,
        ).select_related('deposito__cotizacion__cliente').order_by('fecha', 'id'))
        sumas = dict.fromkeys(('RECEPCION', 'DEVOLUCION', 'RETENCION_DANOS', 'RETENCION_SERVICIO'), CERO)
        for m in movimientos:
            sumas[m.tipo] += m.monto

        hoy = fecha_fin
        abiertos = []
        custodia = por_recibir = CERO
        for dep in DepositoGarantia.objects.select_related('cotizacion__cliente').order_by('cotizacion__fecha_evento'):
            if dep.liquidado:
                continue
            limite = dep.fecha_limite_devolucion
            vencido = bool(limite and limite < hoy and dep.en_custodia > 0)
            abiertos.append({
                'folio': f"COT-{dep.cotizacion_id:03d}", 'cliente': dep.cotizacion.cliente.nombre,
                'fecha_servicio': dep.fin_servicio, 'monto': dep.monto, 'recibido': dep.recibido,
                'en_custodia': dep.en_custodia, 'por_recibir': dep.por_recibir,
                'limite': limite, 'vencido': vencido, 'estado': dep.get_estado_display(),
            })
            custodia += dep.en_custodia
            por_recibir += dep.por_recibir
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'movimientos': movimientos,
            'recibido': sumas['RECEPCION'], 'devuelto': sumas['DEVOLUCION'],
            'retenido_danos': sumas['RETENCION_DANOS'], 'retenido_servicio': sumas['RETENCION_SERVICIO'],
            'retenido': sumas['RETENCION_DANOS'] + sumas['RETENCION_SERVICIO'],
            'abiertos': abiertos, 'en_custodia': custodia, 'por_recibir': por_recibir,
            'vencidos': sum(1 for a in abiertos if a['vencido']),
        }


# ==========================================
# CORTESÍAS Y DESCUENTOS
# ==========================================

class CortesiasDescuentosService:
    """
    Descuentos aplicados en el periodo (por fecha de aplicación), separados en
    cortesías (`Descuento.es_cortesia`) y promociones, y las condonaciones de
    saldo. Solo pesan en el total los de ventas reales: un descuento en una
    cotización que no se concretó no le costó nada a la Quinta.
    """

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from comercial.models import DescuentoAplicado, Pago
        from comercial.views import ESTADOS_VENTA_REAL

        aplicados = list(DescuentoAplicado.objects.filter(
            activo=True, fecha_aplicacion__date__gte=fecha_inicio, fecha_aplicacion__date__lte=fecha_fin,
        ).select_related('descuento', 'cotizacion__cliente', 'aplicado_por').order_by('fecha_aplicacion'))

        reglas = {}
        cortesias = promociones = no_concretado = CERO
        for a in aplicados:
            a.venta_real = a.cotizacion.estado in ESTADOS_VENTA_REAL
            fila = reglas.setdefault(a.descuento_id, {
                'nombre': a.descuento.nombre, 'cortesia': a.descuento.es_cortesia,
                'veces': 0, 'monto': CERO,
            })
            if not a.venta_real:
                no_concretado += a.monto_aplicado
                continue
            fila['veces'] += 1
            fila['monto'] += a.monto_aplicado
            if a.descuento.es_cortesia:
                cortesias += a.monto_aplicado
            else:
                promociones += a.monto_aplicado

        condonaciones = list(Pago.objects.filter(
            metodo='CONDONACION', fecha_pago__gte=fecha_inicio, fecha_pago__lte=fecha_fin,
        ).exclude(tipo='REEMBOLSO').select_related('cotizacion__cliente').order_by('fecha_pago'))
        condonado = sum((p.monto for p in condonaciones), CERO)
        return {
            'fecha_inicio': fecha_inicio, 'fecha_fin': fecha_fin,
            'aplicados': aplicados,
            'reglas': sorted((r for r in reglas.values() if r['veces']), key=lambda r: -r['monto']),
            'cortesias': cortesias, 'promociones': promociones, 'no_concretado': no_concretado,
            'condonaciones': condonaciones, 'condonado': condonado,
            'total': cortesias + promociones + condonado,
        }


# ==========================================
# KOOXI (AGENTE DE WHATSAPP)
# ==========================================

class KooxiService:
    """Mismas métricas que el tablero de Kooxi, para un periodo con fechas."""

    @classmethod
    def generar(cls, fecha_inicio: date, fecha_fin: date) -> Dict:
        from comunicacion.services_tablero import metricas_periodo

        m = metricas_periodo(fecha_inicio, fecha_fin)
        m['fecha_inicio'], m['fecha_fin'] = fecha_inicio, fecha_fin
        m['tasa_humano'] = _porcentaje(m['conversaciones_a_humano'], m['conversaciones_activas'])
        return m
