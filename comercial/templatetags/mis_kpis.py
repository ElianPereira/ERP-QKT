from django import template
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal
from comercial.models import Cotizacion, Gasto # <--- Importamos Gasto

register = template.Library()

@register.simple_tag
def calcular_metricas():
    # 1. Definir fechas (Mes actual)
    hoy = timezone.now()
    
    # 2. CALCULAR VENTAS (Ingresos Confirmados)
    ventas_mes = Cotizacion.objects.filter(
        fecha_evento__month=hoy.month,
        fecha_evento__year=hoy.year,
        estado='CONFIRMADA'
    )
    total_vendido = ventas_mes.aggregate(Sum('precio_final'))['precio_final__sum'] or Decimal('0.00')
    cantidad_eventos = ventas_mes.count()
    
    # 3. CALCULAR GASTOS (Egresos Reales)
    gastos_mes = Gasto.objects.filter(
        fecha_gasto__month=hoy.month,
        fecha_gasto__year=hoy.year
    )
    total_gastado = gastos_mes.aggregate(Sum('monto'))['monto__sum'] or Decimal('0.00')
    
    # 4. CALCULAR UTILIDAD REAL
    utilidad_real = total_vendido - total_gastado

    return {
        'total_vendido': total_vendido,
        'cantidad_eventos': cantidad_eventos,
        'total_gastado': total_gastado, # <--- Dato nuevo
        'utilidad_real': utilidad_real  # <--- Dato nuevo
    }