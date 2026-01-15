import json
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.contrib import messages
from django.core.mail import EmailMessage
from django.conf import settings
from django.db.models import Sum
from django.contrib import admin
# --- IMPORTANTE: ESTA ES LA LLAVE DEL CANDADO ---
from django.contrib.admin.views.decorators import staff_member_required
from django.core.serializers.json import DjangoJSONEncoder
from weasyprint import HTML
from .models import Cotizacion

# --- 1. VISTA PARA VER/IMPRIMIR PDF ---
def generar_pdf_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    
    total_pagado = cotizacion.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    saldo_pendiente = cotizacion.precio_final - total_pagado

    context = {
        'cotizacion': cotizacion,
        'total_pagado': total_pagado,
        'saldo_pendiente': saldo_pendiente
    }
    
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Recibo_{cotizacion.id}_{cotizacion.cliente.nombre}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response

# --- 2. VISTA PARA ENVIAR CORREO ---
def enviar_cotizacion_email(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    cliente = cotizacion.cliente
    
    if not cliente.email:
        messages.error(request, f"El cliente {cliente.nombre} no tiene email registrado.")
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    total_pagado = cotizacion.pagos.aggregate(Sum('monto'))['monto__sum'] or 0
    saldo_pendiente = cotizacion.precio_final - total_pagado

    context = {
        'cotizacion': cotizacion,
        'total_pagado': total_pagado,
        'saldo_pendiente': saldo_pendiente
    }
    
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    pdf_file = html.write_pdf()

    asunto = f"Recibo de pago - Evento {cotizacion.fecha_evento}"
    mensaje = f"""
    Hola {cliente.nombre},
    
    Adjunto encontrarás el recibo actualizado de tu evento.
    
    Total del evento: ${cotizacion.precio_final}
    Abonado hasta hoy: ${total_pagado}
    Saldo Pendiente: ${saldo_pendiente}
    
    Saludos,
    Quinta Kooxtanil
    """

    email = EmailMessage(
        asunto,
        mensaje,
        settings.DEFAULT_FROM_EMAIL,
        [cliente.email],
    )
    email.attach(f"Recibo_{cotizacion.id}.pdf", pdf_file, 'application/pdf')
    email.send()

    messages.success(request, f"✅ Correo enviado a {cliente.email}")
    return redirect(request.META.get('HTTP_REFERER', '/admin/'))

# --- 3. VISTA DEL CALENDARIO ---
def ver_calendario(request):
    cotizaciones = Cotizacion.objects.exclude(estado='CANCELADA')
    
    eventos_lista = []
    for c in cotizaciones:
        color = '#28a745' if c.estado == 'CONFIRMADA' else '#6c757d'
        eventos_lista.append({
            'title': f"{c.cliente.nombre} - {c.producto.nombre}",
            'start': c.fecha_evento.strftime("%Y-%m-%d"),
            'color': color,
            'url': f'/admin/comercial/cotizacion/{c.id}/change/'
        })

    eventos_json = json.dumps(eventos_lista, cls=DjangoJSONEncoder)
    return render(request, 'admin/calendario.html', {'eventos_json': eventos_json})

# --- 4. VISTA DEL DASHBOARD (CON CANDADO 🔒) ---
@staff_member_required # <--- ESTO OBLIGA A INICIAR SESIÓN
def ver_dashboard_kpis(request):
    # 1. Obtenemos la información base del admin (títulos, usuario, etc.)
    context = admin.site.each_context(request)
    
    # 2. Le agregamos la lista de Apps (para que sigan saliendo los iconos de abajo)
    context['app_list'] = admin.site.get_app_list(request)
    
    # 3. Renderizamos NUESTRA plantilla directamente
    return render(request, 'admin/dashboard_kpi.html', context)