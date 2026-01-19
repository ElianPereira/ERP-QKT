from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import SolicitudFactura

@admin.register(SolicitudFactura)
class SolicitudFacturaAdmin(admin.ModelAdmin):
    # 1. Folio Solicitud
    def folio_solicitud(self, obj):
        # Calculamos el string directo, sin format_html complicado
        return f"SOL-{int(obj.id):03d}"
    folio_solicitud.short_description = "Folio"
    folio_solicitud.admin_order_field = 'id'

    # 2. Link a la Cotización (AQUÍ ESTABA EL ERROR)
    def link_cotizacion(self, obj):
        if obj.cotizacion:
            url = reverse('admin:comercial_cotizacion_change', args=[obj.cotizacion.id])
            # PASO CLAVE: Formateamos el texto "COT-005" AQUÍ, afuera del format_html
            texto_boton = f"COT-{int(obj.cotizacion.id):03d}"
            
            # Ahora format_html solo recibe el texto listo, sin {:03d}
            return format_html(
                '<a href="{}" style="color: #2c3e50; font-weight: bold;">'
                '<i class="fas fa-file-contract"></i> {}</a>',
                url, texto_boton
            )
        return format_html('<span style="color: #999;">Directa</span>')
    link_cotizacion.short_description = "Origen"
    link_cotizacion.admin_order_field = 'cotizacion'

    # 3. Botón PDF
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html(
                '<a href="{}" target="_blank" class="button" style="background-color:#17a2b8; color:white; padding:3px 8px; border-radius:4px;">'
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