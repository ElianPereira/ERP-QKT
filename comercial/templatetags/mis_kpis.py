from django import template
from django.db.models import Sum
from django.utils import timezone
from decimal import Decimal # <--- Agregamos esto
from comercial.models import Cotizacion

register = template.Library()

@register.simple_tag
def calcular_metricas():
    # 1. Definir fechas (Mes actual)
    hoy = timezone.now()
    
    # 2. Filtrar ventas CONFIRMADAS de este mes y año
    ventas_mes = Cotizacion.objects.filter(
        fecha_evento__month=hoy.month,
        fecha_evento__year=hoy.year,
        estado='CONFIRMADA'
    )
    
    # 3. Calcular Totales
    total_vendido = ventas_mes.aggregate(Sum('precio_final'))['precio_final__sum'] or Decimal('0.00')
    cantidad_eventos = ventas_mes.count()
    
    # 4. Calcular Ganancia Estimada (Corregido para evitar el TypeError)
    # Convertimos el 1.3 a Decimal para que sea compatible
    divisor_margen = Decimal('1.3')
    ganancia_estimada = total_vendido - (total_vendido / divisor_margen) if total_vendido > 0 else Decimal('0.00')

    return {
        'total_vendido': total_vendido,
        'cantidad_eventos': cantidad_eventos,
        'ganancia_estimada': ganancia_estimada
    }