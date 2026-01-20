import os
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from .models import SolicitudFactura
from comercial.models import Cliente, Cotizacion
from weasyprint import HTML

@staff_member_required
def crear_solicitud(request):
    clientes = Cliente.objects.all().order_by('nombre')
    cotizaciones = Cotizacion.objects.filter(estado__in=['ACEPTADA', 'CONFIRMADA']).order_by('-id')
    
    if request.method == 'POST':
        try:
            cliente_id = request.POST.get('cliente_id')
            cliente = get_object_or_404(Cliente, id=cliente_id)
            
            cotizacion_id = request.POST.get('cotizacion_id')
            cotizacion_obj = None
            if cotizacion_id:
                cotizacion_obj = get_object_or_404(Cotizacion, id=cotizacion_id)
            
            cliente.rfc = request.POST.get('rfc').upper().strip()
            cliente.razon_social = request.POST.get('razon_social')
            cliente.codigo_postal_fiscal = request.POST.get('cp')
            cliente.regimen_fiscal = request.POST.get('regimen_fiscal')
            cliente.uso_cfdi = request.POST.get('uso_cfdi')
            cliente.es_cliente_fiscal = True 
            cliente.save()
            
            solicitud = SolicitudFactura.objects.create(
                cliente=cliente,
                cotizacion=cotizacion_obj, 
                monto=request.POST.get('monto'),
                concepto=request.POST.get('concepto'),
                forma_pago=request.POST.get('forma_pago'),
                metodo_pago=request.POST.get('metodo_pago')
            )
            
            # --- FIX IMAGEN (Ruta Local) ---
            ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
            logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
            # -------------------------------
            
            context = {
                'solicitud': solicitud,
                'cliente': cliente,
                'folio': f"SOL-{int(solicitud.id):03d}",
                'logo_url': logo_url  # <--- Pasamos URL al template
            }
            
            html_string = render_to_string('facturacion/solicitud_pdf.html', context)
            
            pdf_file = HTML(string=html_string).write_pdf()

            filename = f"Solicitud_{cliente.rfc}_SOL-{solicitud.id}.pdf"
            solicitud.archivo_pdf.save(filename, ContentFile(pdf_file))
            
            messages.success(request, f"✅ Solicitud SOL-{int(solicitud.id):03d} creada correctamente.")
            return redirect('/admin/facturacion/solicitudfactura/')
            
        except Exception as e:
            messages.error(request, f"Error generando solicitud: {e}")
        
    context = {
        'clientes': clientes,
        'cotizaciones': cotizaciones
    }
    return render(request, 'facturacion/formulario_solicitud.html', context)