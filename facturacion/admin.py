import os
from django.conf import settings
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.shortcuts import redirect
from django.contrib import messages
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from weasyprint import HTML

from .models import SolicitudFactura

@admin.register(SolicitudFactura)
class SolicitudFacturaAdmin(admin.ModelAdmin):
    # ==========================================================
    # 1. REDIRECCIÓN DEL BOTÓN "AGREGAR"
    # ==========================================================
    def add_view(self, request, form_url='', extra_context=None):
        """
        Sobrescribimos la vista de 'Agregar' nativa del Admin.
        En lugar de mostrar el formulario aburrido de Django, 
        redirigimos al usuario a la vista personalizada que genera el PDF.
        """
        return redirect('/admin/facturacion/nueva/')

    # ==========================================================
    # 2. ACCIÓN PARA REGENERAR PDFS (Para arreglar los vacíos)
    # ==========================================================
    actions = ['generar_pdf_manualmente']

    @admin.action(description="📄 Generar/Regenerar PDF de Solicitud")
    def generar_pdf_manualmente(self, request, queryset):
        """
        Permite seleccionar solicitudes desde la lista y crearles su PDF
        usando los datos que ya tienen guardados en BD.
        """
        exitosos = 0
        errores = 0

        for solicitud in queryset:
            try:
                # 1. Preparar Contexto (Igual que en tu views.py)
                ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
                # Ajuste de ruta para Windows/Linux
                if os.name == 'nt':
                    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}"
                else:
                    logo_url = f"file://{ruta_logo}"

                context = {
                    'solicitud': solicitud,
                    'cliente': solicitud.cliente,
                    'folio': f"SOL-{int(solicitud.id):03d}",
                    'logo_url': logo_url
                }

                # 2. Renderizar HTML
                html_string = render_to_string('facturacion/solicitud_pdf.html', context)

                # 3. Generar PDF
                pdf_file = HTML(string=html_string).write_pdf()

                # 4. Guardar archivo en el modelo
                filename = f"Solicitud_{solicitud.cliente.rfc or 'XAXX010101000'}_SOL-{solicitud.id}.pdf"
                
                # Guardamos sin disparar señales recursivas si las hubiera
                solicitud.archivo_pdf.save(filename, ContentFile(pdf_file), save=True)
                exitosos += 1

            except Exception as e:
                errores += 1
                self.message_user(request, f"Error en SOL-{solicitud.id}: {str(e)}", level=messages.ERROR)

        if exitosos > 0:
            self.message_user(request, f"✅ Se generaron {exitosos} PDFs correctamente.", level=messages.SUCCESS)

    # ==========================================================
    # 3. CONFIGURACIÓN DEL LISTADO (Tu código original mejorado)
    # ==========================================================
    
    # 3.1 Folio Solicitud
    def folio_solicitud(self, obj):
        return f"SOL-{int(obj.id):03d}"
    folio_solicitud.short_description = "Folio"
    folio_solicitud.admin_order_field = 'id'

    # 3.2 Link a la Cotización
    def link_cotizacion(self, obj):
        if obj.cotizacion:
            url = reverse('admin:comercial_cotizacion_change', args=[obj.cotizacion.id])
            texto_boton = f"COT-{int(obj.cotizacion.id):03d}"
            
            return format_html(
                '<a href="{}" style="color: #2c3e50; font-weight: bold;">'
                '<i class="fas fa-file-contract"></i> {}</a>',
                url, texto_boton
            )
        
        # Si no tiene cotización
        return format_html('<span style="color: #999;">{}</span>', "Directa")
        
    link_cotizacion.short_description = "Origen"
    link_cotizacion.admin_order_field = 'cotizacion'

    # 3.3 Botón PDF (Tu código original)
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html(
                '<a href="{}" target="_blank" class="button" style="background-color:#17a2b8; color:white; padding:3px 8px; border-radius:4px; text-decoration:none;">'
                '<i class="fas fa-file-pdf"></i> PDF</a>',
                obj.archivo_pdf.url
            )
        return "-"
    ver_pdf.short_description = "Documento"

    list_display = ('folio_solicitud', 'cliente', 'link_cotizacion', 'monto', 'fecha_solicitud', 'ver_pdf')
    list_display_links = ('folio_solicitud',)
    list_filter = ('fecha_solicitud', 'metodo_pago')
    search_fields = ('cliente__nombre', 'cliente__razon_social')
    autocomplete_fields = ['cotizacion', 'cliente']