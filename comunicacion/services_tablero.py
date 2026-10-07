"""
Tablero del agente de WhatsApp (Issue #366, fase B).

Mide, para un periodo: cuánta gente escribió, cuántas respuestas dio la IA y
cuánto costaron, cuántas veces se pasó a una persona y por qué, y si las
conversaciones terminaron en cotización y en pago. Solo lectura.
"""
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from .models import ComunicacionCliente, ConversacionWhatsApp, MensajeWhatsApp, PaseAHumano

# Precio de lista de Anthropic en USD por millón de tokens: entrada, salida,
# lectura de caché y escritura de caché (5 min). Un modelo que no esté aquí
# muestra tokens sin costo en vez de un costo inventado.
PRECIOS_USD_MTOK = {
    'claude-opus-5-5': (Decimal('4'), Decimal('20'), Decimal('0.20'), Decimal('5')),
    'claude-sonnet-5-5': (Decimal('2'), Decimal('10'), Decimal('0.20'), Decimal('2.50')),
}
PERIODOS = (7, 30, 90)
ESTADOS_PAGADOS = ('CONFIRMADA', 'EJECUTADA', 'CERRADA')


def _costo_usd(fila) -> Decimal | None:
    precios = PRECIOS_USD_MTOK.get(fila['modelo'])
    if precios is None:
        return None
    tokens = (fila['entrada'], fila['salida'], fila['cache_lectura'], fila['cache_escritura'])
    total = sum((Decimal(t or 0) * p for t, p in zip(tokens, precios)), Decimal('0'))
    return (total / Decimal('1000000')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _consumo(respuestas) -> dict:
    filas = (respuestas.exclude(modelo='').values('modelo').annotate(
        respuestas=Count('id'), entrada=Sum('tokens_entrada'), salida=Sum('tokens_salida'),
        cache_lectura=Sum('tokens_cache_lectura'), cache_escritura=Sum('tokens_cache_escritura'),
    ).order_by('modelo'))
    por_modelo, costo, sin_precio = [], Decimal('0.00'), False
    for fila in filas:
        fila['costo_usd'] = _costo_usd(fila)
        if fila['costo_usd'] is None:
            sin_precio = True
        else:
            costo += fila['costo_usd']
        por_modelo.append(fila)
    medidas = sum(f['respuestas'] for f in por_modelo)
    return {
        'por_modelo': por_modelo,
        'costo_usd': costo,
        'costo_completo': not sin_precio,
        'costo_por_respuesta_usd': (
            (costo / medidas).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
            if medidas and not sin_precio else None),
    }


def _conversion(conversaciones) -> dict:
    """Conversaciones nuevas del periodo que acabaron en cotización y en pago.

    Liga por los últimos 10 dígitos del teléfono (mismo criterio que
    `herramientas_agente.cotizaciones_del_telefono`) y solo cuenta
    cotizaciones creadas después del primer mensaje: la que ya existía no la
    trajo el agente.
    """
    from comercial.models import Cotizacion

    con_cotizacion = con_pago = 0
    for conv in conversaciones:
        cotizaciones = Cotizacion.objects.filter(
            cliente__telefono__endswith=conv.telefono[-10:], created_at__gte=conv.created_at)
        if not cotizaciones.exists():
            continue
        con_cotizacion += 1
        if cotizaciones.filter(estado__in=ESTADOS_PAGADOS).exists() or cotizaciones.filter(
                pagos__isnull=False).exists():
            con_pago += 1
    total = len(conversaciones)
    return {
        'nuevas': total,
        'con_cotizacion': con_cotizacion,
        'con_pago': con_pago,
        'tasa_cotizacion': round(con_cotizacion * 100 / total) if total else 0,
        'tasa_pago': round(con_pago * 100 / total) if total else 0,
    }


def metricas_agente(dias: int = 30) -> dict:
    if dias not in PERIODOS:
        dias = 30
    hoy = timezone.localdate()
    desde_fecha = hoy - timedelta(days=dias - 1)
    desde = timezone.make_aware(datetime.combine(desde_fecha, time.min))

    mensajes = MensajeWhatsApp.objects.filter(created_at__gte=desde)
    respuestas_ia = mensajes.filter(direccion='AGENTE', automatico=False).exclude(modelo='')
    pases = PaseAHumano.objects.filter(created_at__gte=desde)
    nuevas = list(ConversacionWhatsApp.objects.filter(created_at__gte=desde))
    activas = mensajes.filter(direccion='ENTRADA').values('conversacion').distinct().count()

    return {
        'dias': dias,
        'periodos': PERIODOS,
        'desde': desde_fecha,
        'conversaciones_activas': activas,
        'mensajes_clientes': mensajes.filter(direccion='ENTRADA').count(),
        'respuestas_ia': respuestas_ia.count(),
        'respuestas_equipo': mensajes.filter(direccion='HUMANO').count(),
        'pases_a_humano': pases.count(),
        'conversaciones_a_humano': pases.values('conversacion').distinct().count(),
        'motivos': list(pases.values('motivo').annotate(veces=Count('id')).order_by('-veces', 'motivo')[:10]),
        'ultimos_pases': list(pases.select_related('conversacion')[:15]),
        'consumo': _consumo(respuestas_ia),
        'conversion': _conversion(nuevas),
        'seguimientos': ComunicacionCliente.objects.filter(
            tipo='SEGUIMIENTO', estado__in=('ENVIADO', 'ENTREGADO', 'ABIERTO'), fecha_envio__gte=desde).count(),
        'autorizaciones': ConversacionWhatsApp.objects.filter(consentimiento_en__gte=desde).count(),
    }
