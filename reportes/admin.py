"""
Admin del Módulo de Reportes
============================
Panel centralizado de reportes + historial de auditoría.
Sistema de Diseño QKT v2.0
"""
from django.contrib import admin
from django.urls import path, reverse
from django.shortcuts import render
from django.utils.html import format_html

from .models import ReporteGenerado


@admin.register(ReporteGenerado)
class ReporteGeneradoAdmin(admin.ModelAdmin):
    change_list_template = 'admin/reportes/reportegenerado/change_list.html'
    list_display = ('tipo_badge', 'formato_badge', 'fecha_inicio', 'fecha_fin', 'created_by', 'created_at')
    list_filter = ('tipo', 'formato', 'created_at')
    date_hierarchy = 'created_at'
    list_per_page = 30
    readonly_fields = ('tipo', 'formato', 'fecha_inicio', 'fecha_fin', 'parametros', 'created_by', 'created_at')

    class Media:
        css = {'all': ('css/admin_fix.css', 'css/mobile_fix.css')}
        js = ('js/tabs_fix.js',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Tipo", ordering="tipo")
    def tipo_badge(self, obj):
        colores = {
            'BALANZA': '#3498db',
            'EDO_RESULTADOS': '#27ae60',
            'BALANCE_GRAL': '#2E7D32',
            'LIBRO_MAYOR': '#9b59b6',
            'AUXILIAR': '#1abc9c',
            'CXC_CARTERA': '#e67e22',
            'COT_PERIODO': '#F5C518',
            'OCUPACION': '#e74c3c',
            'COMPARATIVO': '#e74c3c',
            'FACTURAS': '#95a5a6',
        }
        color = colores.get(obj.tipo, '#95a5a6')
        text_color = '#333' if obj.tipo == 'COT_PERIODO' else '#fff'
        return format_html(
            '<span style="background:{}; color:{}; padding:4px 10px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            color, text_color, obj.get_tipo_display()
        )

    @admin.display(description="Formato")
    def formato_badge(self, obj):
        color = '#e74c3c' if obj.formato == 'PDF' else '#3498db'
        return format_html(
            '<span style="background:{}; color:#fff; padding:3px 8px; '
            'border-radius:12px; font-size:10px; font-weight:600;">{}</span>',
            color, obj.formato
        )

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
