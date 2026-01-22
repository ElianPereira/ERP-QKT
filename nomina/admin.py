from django.contrib import admin
from django.utils.html import format_html
from .models import Empleado, ReciboNomina

# Intentamos importar la vista de carga masiva
try:
    from .views import cargar_nomina 
except ImportError:
    pass

@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'puesto', 'tarifa_base', 'telefono', 'activo')
    list_filter = ('puesto', 'activo')
    search_fields = ('nombre',)

@admin.register(ReciboNomina)
class ReciboNominaAdmin(admin.ModelAdmin):
    # --- ESTA ES LA LÍNEA QUE TE FALTABA ---
    # Sin esto, Django usa la tabla default y no muestra tu barra de carga
    change_list_template = 'admin/nomina/recibonomina/change_list.html'

    list_display = ('folio_custom', 'empleado', 'periodo', 'total_pagado', 'ver_pdf')
    list_filter = ('periodo', 'empleado')
    
    def folio_custom(self, obj):
        return f"NOM-{obj.id:03d}"
    folio_custom.short_description = "Folio"

    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html(
                '<a href="{}" target="_blank" style="background-color:#17a2b8; color:white; padding:5px 10px; border-radius:5px; text-decoration:none;">'
                '<i class="fas fa-file-pdf"></i> Ver PDF</a>',
                obj.archivo_pdf.url
            )
        return "-"
    ver_pdf.short_description = "Recibo"

    # Inyectamos la variable para que el template sepa que debe mostrar el botón
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['boton_carga'] = True 
        return super().changelist_view(request, extra_context=extra_context)