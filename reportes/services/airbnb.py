"""
Servicios de Reportes Airbnb
==============================
Ocupación por listing/mes y comparativo mensual.

ERP Quinta Ko'ox Tanil
"""
from decimal import Decimal
from datetime import date, timedelta
from calendar import monthrange
from typing import Dict, List
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth


class OcupacionService:
    """
    Calcula tasa de ocupación por listing y mes.
    Ocupación = noches reservadas / noches disponibles del mes.
    """

    @classmethod
    def generar(
        cls,
        fecha_inicio: date,
        fecha_fin: date,
        anuncio_id: int = None,
    ) -> Dict:
        from airbnb.models import AnuncioAirbnb, ReservaAirbnb

        anuncios = AnuncioAirbnb.objects.filter(activo=True)
        if anuncio_id:
            anuncios = anuncios.filter(pk=anuncio_id)

        # Generar lista de meses en el rango
        meses = cls._generar_meses(fecha_inicio, fecha_fin)

        data_por_listing = []
        totales_por_mes = {m['key']: {'noches': 0, 'disponibles': 0} for m in meses}

        for anuncio in anuncios:
            fila = {
                'anuncio': anuncio.nombre,
                'tipo': anuncio.get_tipo_display(),
                'meses': [],
                'total_noches': 0,
                'total_disponibles': 0,
            }

            for mes in meses:
                inicio_mes = mes['inicio']
                fin_mes = mes['fin']
                dias_mes = (fin_mes - inicio_mes).days + 1

                # Reservas que se solapan con el mes (excluir bloqueadas por host y canceladas)
                reservas = ReservaAirbnb.objects.filter(
                    anuncio=anuncio,
                    fecha_inicio__lte=fin_mes,
                    fecha_fin__gte=inicio_mes,
                ).exclude(estado__in=['CANCELADA', 'BLOQUEADA'])

                noches = 0
                for r in reservas:
                    check_in = max(r.fecha_inicio, inicio_mes)
                    check_out = min(r.fecha_fin, fin_mes + timedelta(days=1))
                    noches += max(0, (check_out - check_in).days)

                tasa = (Decimal(noches) / Decimal(dias_mes) * 100).quantize(Decimal('0.1')) if dias_mes > 0 else Decimal('0.0')

                fila['meses'].append({
                    'noches': noches,
                    'disponibles': dias_mes,
                    'tasa': tasa,
                })
                fila['total_noches'] += noches
                fila['total_disponibles'] += dias_mes

                totales_por_mes[mes['key']]['noches'] += noches
                totales_por_mes[mes['key']]['disponibles'] += dias_mes

            fila['tasa_global'] = (
                (Decimal(fila['total_noches']) / Decimal(fila['total_disponibles']) * 100).quantize(Decimal('0.1'))
                if fila['total_disponibles'] > 0 else Decimal('0.0')
            )
            data_por_listing.append(fila)

        # Totales generales por mes + acumulado global
        totales_meses = []
        total_noches_global = 0
        total_disponibles_global = 0
        for mes in meses:
            t = totales_por_mes[mes['key']]
            tasa = (Decimal(t['noches']) / Decimal(t['disponibles']) * 100).quantize(Decimal('0.1')) if t['disponibles'] > 0 else Decimal('0.0')
            totales_meses.append({
                'noches': t['noches'],
                'disponibles': t['disponibles'],
                'tasa': tasa,
            })
            total_noches_global += t['noches']
            total_disponibles_global += t['disponibles']

        tasa_global = (
            (Decimal(total_noches_global) / Decimal(total_disponibles_global) * 100).quantize(Decimal('0.1'))
            if total_disponibles_global > 0 else Decimal('0.0')
        )

        return {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'meses': meses,
            'data': data_por_listing,
            'totales_meses': totales_meses,
            'tasa_global_total': tasa_global,
        }

    @classmethod
    def _generar_meses(cls, inicio: date, fin: date) -> List[Dict]:
        meses = []
        current = date(inicio.year, inicio.month, 1)
        while current <= fin:
            ultimo_dia = monthrange(current.year, current.month)[1]
            fin_mes = date(current.year, current.month, ultimo_dia)
            meses.append({
                'key': current.strftime('%Y-%m'),
                'label': current.strftime('%b %Y'),
                'inicio': max(current, inicio),
                'fin': min(fin_mes, fin),
            })
            # Avanzar al siguiente mes
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)
        return meses


class ComparativoMensualService:
    """
    Genera comparativo mensual de ingresos Airbnb: bruto, comisiones, 
    retenciones, neto. Agrupado por mes y listing.
    """

    @classmethod
    def generar(
        cls,
        fecha_inicio: date,
        fecha_fin: date,
        anuncio_id: int = None,
    ) -> Dict:
        from airbnb.models import PagoAirbnb, AnuncioAirbnb

        qs = PagoAirbnb.objects.filter(
            fecha_checkin__gte=fecha_inicio,
            fecha_checkin__lte=fecha_fin,
            estado='PAGADO',
        ).select_related('anuncio')

        if anuncio_id:
            qs = qs.filter(anuncio_id=anuncio_id)

        # Agrupar por mes
        datos_mes = qs.annotate(
            mes=TruncMonth('fecha_checkin')
        ).values('mes').annotate(
            total_bruto=Sum('monto_bruto'),
            total_comision=Sum('comision_airbnb'),
            total_isr=Sum('retencion_isr'),
            total_iva=Sum('retencion_iva'),
            total_neto=Sum('monto_neto'),
            num_reservas=Count('id'),
        ).order_by('mes')

        meses = []
        gran_total = {
            'bruto': Decimal('0.00'),
            'comision': Decimal('0.00'),
            'isr': Decimal('0.00'),
            'iva': Decimal('0.00'),
            'neto': Decimal('0.00'),
            'reservas': 0,
        }

        for d in datos_mes:
            meses.append({
                'mes': d['mes'],
                'label': d['mes'].strftime('%b %Y'),
                'bruto': d['total_bruto'] or Decimal('0.00'),
                'comision': d['total_comision'] or Decimal('0.00'),
                'isr': d['total_isr'] or Decimal('0.00'),
                'iva': d['total_iva'] or Decimal('0.00'),
                'neto': d['total_neto'] or Decimal('0.00'),
                'reservas': d['num_reservas'],
            })
            gran_total['bruto'] += d['total_bruto'] or Decimal('0.00')
            gran_total['comision'] += d['total_comision'] or Decimal('0.00')
            gran_total['isr'] += d['total_isr'] or Decimal('0.00')
            gran_total['iva'] += d['total_iva'] or Decimal('0.00')
            gran_total['neto'] += d['total_neto'] or Decimal('0.00')
            gran_total['reservas'] += d['num_reservas']

        # Detalle por listing
        por_listing = qs.values(
            'anuncio__nombre'
        ).annotate(
            total_bruto=Sum('monto_bruto'),
            total_neto=Sum('monto_neto'),
            num_reservas=Count('id'),
        ).order_by('-total_bruto')

        return {
            'fecha_inicio': fecha_inicio,
            'fecha_fin': fecha_fin,
            'meses': meses,
            'gran_total': gran_total,
            'por_listing': list(por_listing),
        }
