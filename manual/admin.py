from django.contrib import admin

from core_erp import admin_ui as ui

from .models import DescargaManual


@admin.register(DescargaManual)
class DescargaManualAdmin(admin.ModelAdmin):
    """Bitácora de solo lectura, solo superusuario."""
    list_display = ('created_at', 'usuario', 'rol_badge', 'secciones', 'version')
    list_filter = ('rol_exportado',)
    search_fields = ('usuario__username', 'usuario__first_name', 'usuario__last_name')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)

    @admin.display(description='Manual', ordering='rol_exportado')
    def rol_badge(self, obj):
        return ui.badge(obj.rol_exportado.title(), ui.INFO)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
