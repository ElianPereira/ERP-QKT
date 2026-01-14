# views.py
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML, CSS
from .models import Cotizacion

def generar_pdf_cotizacion(request, cotizacion_id):
    # 1. Obtener la cotización
    cotizacion = get_object_or_404(Cotizacion, id=cotizacion_id)
    
    # 2. Contexto
    # Tu HTML usa 'cotizacion' para acceder a todo (cliente, producto, pagos, etc.)
    context = {
        'cotizacion': cotizacion,
    }
    
    # 3. Renderizar tu nuevo template de Recibo
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    
    # 4. Configurar respuesta PDF
    response = HttpResponse(content_type='application/pdf')
    
    # Nombre del archivo: Ej. Recibo_15_JuanPerez.pdf
    filename = f"Recibo_{cotizacion.id}_{cotizacion.cliente.nombre.replace(' ', '_')}.pdf"
    response['Content-Disposition'] = f'inline; filename="{filename}"'
    
    # 5. Generar PDF con WeasyPrint
    # Nota: A veces WeasyPrint necesita 'presentational_hints=True' para respetar ciertos estilos HTML5
    html = HTML(string=html_string, base_url=request.build_absolute_uri())
    html.write_pdf(response, presentational_hints=True)
    
    return response  