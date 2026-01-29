import os
from decimal import Decimal
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from weasyprint import HTML

from .models import SolicitudFactura
from comercial.models import Cliente, Cotizacion

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
            
            # 1. Actualizamos datos fiscales del cliente
            cliente.rfc = request.POST.get('rfc').upper().strip()
            cliente.razon_social = request.POST.get('razon_social')
            cliente.codigo_postal_fiscal = request.POST.get('cp')
            cliente.regimen_fiscal = request.POST.get('regimen_fiscal')
            cliente.uso_cfdi = request.POST.get('uso_cfdi')
            cliente.es_cliente_fiscal = True 
            cliente.save()
            
            # 2. Creamos la solicitud en BD
            solicitud = SolicitudFactura.objects.create(
                cliente=cliente,
                cotizacion=cotizacion_obj, 
                monto=request.POST.get('monto'),
                concepto=request.POST.get('concepto'),
                forma_pago=request.POST.get('forma_pago'),
                metodo_pago=request.POST.get('metodo_pago')
            )
            
            # --- PREPARACIÓN DEL PDF ---
            ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
            if os.name == 'nt':
                logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}"
            else:
                logo_url = f"file://{ruta_logo}"

            # --- CÁLCULO INVERSO DE IMPUESTOS (AQUÍ ESTABA FALTANDO) ---
            total = Decimal(solicitud.monto)
            subtotal = total
            iva = Decimal('0.00')
            ret_isr = Decimal('0.00')
            
            if cliente.es_cliente_fiscal:
                factor_divisor = Decimal('1.16')
                if cliente.tipo_persona == 'MORAL':
                    # Si es Moral: Total = Subtotal * (1 + 0.16 - 0.0125) = 1.1475
                    factor_divisor = Decimal('1.1475') 
                    subtotal = total / factor_divisor
                    iva = subtotal * Decimal('0.16')
                    ret_isr = subtotal * Decimal('0.0125')
                else:
                    # Si es Física: Total = Subtotal * 1.16
                    subtotal = total / factor_divisor
                    iva = subtotal * Decimal('0.16')

            context = {
                'solicitud': solicitud,
                'cliente': cliente,
                'folio': f"SOL-{int(solicitud.id):03d}",
                'logo_url': logo_url,
                # Variables matemáticas para el template
                'calc_subtotal': subtotal,
                'calc_iva': iva,
                'calc_ret_isr': ret_isr,
                'calc_total': total
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