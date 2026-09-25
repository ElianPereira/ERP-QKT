"""
Admin del Módulo de Reportes
============================
Panel centralizado de reportes + historial de auditoría.
Sistema de Diseño QKT v2.0
"""
from django.contrib import admin
from django.shortcuts import render
from django.urls import path, reverse
from django.utils import timezone
from django.utils.formats import date_format

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo

from .models import ReporteGenerado


@admin.register(ReporteGenerado)
class ReporteGeneradoAdmin(admin.ModelAdmin):
    change_list_template = 'admin/reportes/reportegenerado/change_list.html'
    list_display = ('tipo_badge', 'formato_badge', 'fecha_inicio', 'fecha_fin', 'created_by', 'generado_display')
    list_filter = (('tipo', con_titulo('Tipo')), 'formato', filtro_periodo('created_at', 'Generado'))
    date_hierarchy = 'created_at'
    list_per_page = 30
    readonly_fields = ('tipo', 'formato', 'fecha_inicio', 'fecha_fin', 'parametros', 'created_by', 'created_at')

    class Media:
        css = {'all': ('css/admin_fix.css', 'css/mobile_fix_v4.css')}
        js = ('js/tabs_fix.js',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Tipo", ordering="tipo")
    def tipo_badge(self, obj):
        return ui.badge(obj.get_tipo_display(), ui.NEUTRO, categoria=True)

    @admin.display(description="Formato", ordering="formato")
    def formato_badge(self, obj):
        return ui.badge(obj.formato, ui.INFO, categoria=True)

    @admin.display(description="Generado", ordering="created_at")
    def generado_display(self, obj):
        return date_format(timezone.localtime(obj.created_at), 'd M Y H:i') if obj.created_at else ui.vacio()

    def get_urls(self):
        custom_urls = [
            path('selector/', self.admin_site.admin_view(self.selector_view), name='reportes_selector'),
        ]
        return custom_urls + super().get_urls()

    def selector_view(self, request):
        """Vista principal: selector de reportes."""
        context = {
            **self.admin_site.each_context(request),
            'title': 'Centro de Reportes',
        }
        return render(request, 'reportes/selector.html', context)
