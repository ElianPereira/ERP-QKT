"""
Calendario unificado de la Quinta
=================================
Cotizaciones (eventos, pasadías, hospedaje), asignaciones de espacio y de
personal en un solo FullCalendar.
"""
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import permission_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_date

from .models import AsignacionEspacio, AsignacionPersonal, Cotizacion


def _construir_eventos_calendario(fecha_inicio, fecha_fin):
    """Arma la lista de eventos del calendario, acotada al rango
    [fecha_inicio, fecha_fin) — nunca el histórico completo (SEC-DOS-001)."""
    eventos_lista = []

    # Traslape de rango, no solo las que empiezan dentro de la ventana: un
    # Hospedaje de varias noches puede haber comenzado antes de fecha_inicio.
    cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA').filter(
        fecha_evento__lt=fecha_fin,
    ).filter(
        Q(fecha_salida__gt=fecha_inicio)
        | Q(fecha_salida__isnull=True, fecha_evento__gte=fecha_inicio)
    ).select_related('cliente')

    for c in cotizaciones:
        evento = {
            'title': f"{c.cliente.nombre} - {c.nombre_evento}",
            'start': c.fecha_evento.strftime("%Y-%m-%d"),
            'color': '#27ae60' if c.estado == 'CONFIRMADA' else '#95a5a6',
            'url': f'/admin/comercial/cotizacion/{c.id}/change/',
            'extendedProps': {'tipo': 'evento'}
        }
        if c.tipo_servicio == 'HOSPEDAJE' and c.fecha_salida:
            # Rango real (FullCalendar: 'end' es exclusivo).
            evento['end'] = c.fecha_salida.strftime("%Y-%m-%d")
        eventos_lista.append(evento)

    asignaciones_esp = AsignacionEspacio.objects.filter(
        fecha__gte=fecha_inicio, fecha__lt=fecha_fin,
    ).select_related('espacio', 'cotizacion__cliente')
    for a in asignaciones_esp:
        eventos_lista.append({
            'title': f"📍 {a.espacio.nombre}: COT-{a.cotizacion_id:03d}",
            'start': f"{a.fecha.strftime('%Y-%m-%d')}T{a.hora_inicio.strftime('%H:%M:%S')}",
            'end': f"{a.fecha.strftime('%Y-%m-%d')}T{a.hora_fin.strftime('%H:%M:%S')}" if a.hora_fin > a.hora_inicio else None,
            'color': '#9b59b6',
            'url': f'/admin/comercial/asignacionespacio/{a.id}/change/',
            'extendedProps': {'tipo': 'espacio'}
        })

    asignaciones_per = AsignacionPersonal.objects.filter(
        fecha__gte=fecha_inicio, fecha__lt=fecha_fin,
    ).select_related('empleado')
    for a in asignaciones_per:
        eventos_lista.append({
            'title': f"👤 {a.empleado.nombre} ({a.get_rol_display()})",
            'start': f"{a.fecha.strftime('%Y-%m-%d')}T{a.hora_inicio.strftime('%H:%M:%S')}",
            'color': '#16a085',
            'url': f'/admin/comercial/asignacionpersonal/{a.id}/change/',
            'extendedProps': {'tipo': 'personal'}
        })

    return eventos_lista


@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def calendario_unificado(request):
    """Página del calendario. No trae eventos: FullCalendar los pide por AJAX
    a `calendario_unificado_eventos` para el rango visible en cada momento."""
    return render(request, 'admin/calendario.html', {
        'eventos_url': reverse('calendario_unificado_eventos'),
        'title': 'Calendario Unificado',
    })


@staff_member_required
@permission_required('comercial.view_cotizacion', raise_exception=True)
def calendario_unificado_eventos(request):
    """JSON de eventos acotado a `start`/`end` (YYYY-MM-DD, fin exclusivo).
    Devuelve un array plano: es lo que FullCalendar espera de
    `successCallback(eventos)`."""
    fecha_inicio = parse_date(request.GET.get('start', ''))
    fecha_fin = parse_date(request.GET.get('end', ''))
    if not fecha_inicio or not fecha_fin:
        return JsonResponse({'error': "Parámetros 'start' y 'end' requeridos (YYYY-MM-DD)."}, status=400)

    return JsonResponse(_construir_eventos_calendario(fecha_inicio, fecha_fin), safe=False)
