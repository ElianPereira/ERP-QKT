from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML
from django.contrib import messages               # <--- NUEVO
from django.core.mail import EmailMessage         # <--- NUEVO
from django.conf import settings                  # <--- NUEVO
from .models import Cotizacion

# Tu vista de PDF normal (déjala como está)
def generar_pdf_cotizacion(request, cotizacion_id):
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    context = {'cotizacion': cotizacion}
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    response = HttpResponse(content_type='application/pdf')
    filename = f"Recibo_{cotizacion.id}_{cotizacion.cliente.nombre}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    HTML(string=html_string, base_url=request.build_absolute_uri()).write_pdf(response)
    return response

# --- NUEVA FUNCIÓN PARA ENVIAR CORREO ---
def enviar_cotizacion_email(request, cotizacion_id):
    # 1. Buscar datos
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    cliente = cotizacion.cliente
    
    if not cliente.email:
        messages.error(request, f"El cliente {cliente.nombre} no tiene email registrado.")
        # Regresa a la página anterior
        return redirect(request.META.get('HTTP_REFERER', '/admin/'))

    # 2. Generar el PDF en memoria (sin guardarlo en disco)
    context = {'cotizacion': cotizacion}
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    pdf_file = html.write_pdf() # Esto guarda el PDF en bytes en esta variable

    # 3. Armar el Correo
    asunto = f"Recibo de pago - Evento {cotizacion.fecha_evento}"
    mensaje = f"""
    Hola {cliente.nombre},
    
    Adjunto encontrarás el recibo de tu evento programado para el {cotizacion.fecha_evento}.
    
    Saludos,
    El equipo de Eventos.
    """

    email = EmailMessage(
        asunto,
        mensaje,
        settings.DEFAULT_FROM_EMAIL,
        [cliente.email],
    )

    # 4. Adjuntar PDF y Enviar
    filename = f"Recibo_{cotizacion.id}.pdf"
    email.attach(filename, pdf_file, 'application/pdf')
    
    email.send() # Imprime en terminal por ahora

    messages.success(request, f"✅ Correo enviado a {cliente.email}")
    return redirect(request.META.get('HTTP_REFERER', '/admin/'))