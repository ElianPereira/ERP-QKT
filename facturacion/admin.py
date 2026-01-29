import os
from decimal import Decimal
from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse, path
from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from weasyprint import HTML

from .models import SolicitudFactura

@admin.register(SolicitudFactura)
class SolicitudFacturaAdmin(admin.ModelAdmin):
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:object_id>/generar-pdf/', 
                self.admin_site.admin_view(self.generar_pdf_individual), 
                name='facturacion_solicitudfactura_generar_pdf'
            ),
        ]
        return custom_urls + urls

    def generar_pdf_individual(self, request, object_id):
        solicitud = get_object_or_404(SolicitudFactura, pk=object_id)
        
        try:
            ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
            if os.name == 'nt':
                logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}"
            else:
                logo_url = f"file://{ruta_logo}"

            # --- MATEMÁTICA ---
            total = Decimal(solicitud.monto) if solicitud.monto else Decimal('0.00')
            subtotal = total
            iva = Decimal('0.00')
            ret_isr = Decimal('0.00')
            
            if solicitud.cliente.es_cliente_fiscal:
                factor_divisor = Decimal('1.16')
                if solicitud.cliente.tipo_persona == 'MORAL':
                    factor_divisor = Decimal('1.1475') 
                    subtotal = total / factor_divisor
                    iva = subtotal * Decimal('0.16')
                    ret_isr = subtotal * Decimal('0.0125')
                else:
                    subtotal = total / factor_divisor
                    iva = subtotal * Decimal('0.16')
            
            context = {
                'solicitud': solicitud,
                'cliente': solicitud.cliente,
                'folio': f"SOL-{int(solicitud.id):03d}",
                'logo_url': logo_url,
                'calc_subtotal': subtotal,
                'calc_iva': iva,
                'calc_ret_isr': ret_isr,
                'calc_total': total
            }

            html_string = render_to_string('facturacion/solicitud_pdf.html', context)
            pdf_file = HTML(string=html_string).write_pdf()

            filename = f"Solicitud_{solicitud.cliente.rfc or 'XAXX010101000'}_SOL-{solicitud.id}.pdf"
            
            # Guardar overwrite
            solicitud.archivo_pdf.save(filename, ContentFile(pdf_file), save=True)
            
            self.message_user(request, f"✅ PDF actualizado para SOL-{object_id}", level=messages.SUCCESS)
            
        except Exception as e:
            self.message_user(request, f"Error generando PDF: {e}", level=messages.ERROR)

        return redirect('admin:facturacion_solicitudfactura_changelist')

    def add_view(self, request, form_url='', extra_context=None):
        return redirect('/admin/facturacion/nueva/')
    
    # --- COLUMNAS LISTA ---
    def folio_solicitud(self, obj):
        return f"SOL-{int(obj.id):03d}"
    folio_solicitud.short_description = "Folio"
    folio_solicitud.admin_order_field = 'id'

    def link_cotizacion(self, obj):
        if obj.cotizacion:
            url = reverse('admin:comercial_cotizacion_change', args=[obj.cotizacion.id])
            texto_boton = f"COT-{int(obj.cotizacion.id):03d}"
            return format_html('<a href="{}" style="color: #2c3e50; font-weight: bold;"><i class="fas fa-file-contract"></i> {}</a>', url, texto_boton)
        return format_html('<span style="color: #999;">{}</span>', "Directa")
    link_cotizacion.short_description = "Origen"

    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html('<a href="{}" target="_blank" class="button" style="background-color:#17a2b8; color:white; padding:4px 10px; border-radius:4px; text-decoration:none; font-weight:bold;"><i class="fas fa-file-pdf"></i> Ver PDF</a>', obj.archivo_pdf.url)
        else:
            url_generar = reverse('admin:facturacion_solicitudfactura_generar_pdf', args=[obj.id])
            return format_html('<a href="{}" class="button" style="background-color:#ffc107; color:#333; padding:4px 10px; border-radius:4px; text-decoration:none; font-weight:bold;"><i class="fas fa-cog"></i> Generar</a>', url_generar)
    ver_pdf.short_description = "Documento"
    ver_pdf.allow_tags = True

    list_display = ('folio_solicitud', 'cliente', 'link_cotizacion', 'monto', 'fecha_solicitud', 'ver_pdf')
    list_display_links = ('folio_solicitud',)
    list_filter = ('fecha_solicitud', 'metodo_pago')
    search_fields = ('cliente__nombre', 'cliente__razon_social')
    autocomplete_fields = ['cotizacion', 'cliente']