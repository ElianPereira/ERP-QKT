from django.contrib import admin
from django.utils.html import format_html
from .models import Empleado, ReciboNomina
# Si tienes la vista de carga masiva, déjala importada, si no, borra esta línea:
try:
    from .views import cargar_nomina 
except ImportError:
    pass

@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'puesto', 'tarifa_base', 'telefono', 'activo') # Agregado puesto y telefono
    list_filter = ('puesto', 'activo') # Filtro por puesto
    search_fields = ('nombre',)

@admin.register(ReciboNomina)
class ReciboNominaAdmin(admin.ModelAdmin):
    # Aquí está tu botón de PDF integrado
    list_display = ('folio_custom', 'empleado', 'periodo', 'total_pagado', 'ver_pdf')
    list_filter = ('periodo', 'empleado')
    
    def folio_custom(self, obj):
        return f"NOM-{obj.id:03d}"
    folio_custom.short_description = "Folio"

    # --- BOTÓN ESTILO FACTURACIÓN ---
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html(
                '<a href="{}" target="_blank" style="background-color:#17a2b8; color:white; padding:5px 10px; border-radius:5px; text-decoration:none;">'
                '<i class="fas fa-file-pdf"></i> Ver PDF</a>',
                obj.archivo_pdf.url
            )
        return "-"
    ver_pdf.short_description = "Recibo"

    # Opcional: Si tienes la lógica de carga masiva
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['boton_carga'] = True 
        return super().changelist_view(request, extra_context=extra_context)