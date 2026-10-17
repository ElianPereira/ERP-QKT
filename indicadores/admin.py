"""
Admin de Indicadores (Issue #402). Solo Dirección (superusuario) ve y edita
parámetros y metas: son supuestos de negocio, no captura operativa.
"""
from django.contrib import admin, messages

from core_erp import admin_ui as ui

from .models import MetaMensual, ParametrosNegocio


class SoloDireccionMixin:
    """Visible y editable únicamente para el superusuario; nada se borra."""

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ParametrosNegocio)
class ParametrosNegocioAdmin(SoloDireccionMixin, admin.ModelAdmin):
    list_display = ('vigencia', 'estado', 'costo_fijo', 'utilidad', 'tope_eventos', 'actualizado')
    list_filter = ('activo',)
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        ('Vigencia', {'fields': ('vigente_desde', 'activo', 'inicio_datos', 'meses_promedio')}),
        ('Calendario de ventas', {'fields': (
            'dias_evento', 'dias_pasadia', 'dias_hospedaje', 'max_eventos_semana',
            'dias_preparacion_evento')}),
        ('Costos', {'fields': (
            'pct_costo_variable_evento', 'pct_costo_variable_pasadia', 'pct_costo_variable_hospedaje',
            'costo_fijo_mensual_override', 'utilidad_mensual_objetivo')}),
        ('Demanda máxima esperada', {'fields': (
            'demanda_max_evento_mes', 'demanda_max_pasadia_mes', 'demanda_max_hospedaje_mes')}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at')}),
    )

    @admin.display(description='Vigencia', ordering='vigente_desde')
    def vigencia(self, obj):
        return f'Desde {obj.vigente_desde:%d/%m/%Y}'

    @admin.display(description='Estado', ordering='activo')
    def estado(self, obj):
        return ui.badge('Vigente' if obj.activo else 'Anterior', ui.EXITO if obj.activo else ui.NEUTRO)

    @admin.display(description='Costo fijo')
    def costo_fijo(self, obj):
        return ui.monto(obj.costo_fijo_mensual_override) if obj.costo_fijo_mensual_override is not None \
            else ui.vacio(numerico=True)

    @admin.display(description='Utilidad objetivo')
    def utilidad(self, obj):
        return ui.monto(obj.utilidad_mensual_objetivo)

    @admin.display(description='Eventos por semana')
    def tope_eventos(self, obj):
        return obj.max_eventos_semana

    @admin.display(description='Actualizado', ordering='updated_at')
    def actualizado(self, obj):
        return obj.updated_at.strftime('%d/%m/%Y') if obj.updated_at else ui.vacio()


@admin.register(MetaMensual)
class MetaMensualAdmin(SoloDireccionMixin, admin.ModelAdmin):
    list_display = ('periodo', 'estado', 'venta', 'ocupacion', 'unidades', 'marketing')
    list_filter = ('activo', 'anio')
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')

    @admin.display(description='Mes', ordering='mes')
    def periodo(self, obj):
        return f'{obj.mes:02d}/{obj.anio}'

    @admin.display(description='Estado', ordering='activo')
    def estado(self, obj):
        return ui.badge('Activa' if obj.activo else 'Inactiva', ui.EXITO if obj.activo else ui.NEUTRO)

    @admin.display(description='Venta neta', ordering='meta_venta_neta')
    def venta(self, obj):
        return ui.monto(obj.meta_venta_neta)

    @admin.display(description='Ocupación')
    def ocupacion(self, obj):
        return f'{obj.meta_ocupacion_pct}%'

    @admin.display(description='Eventos / pasadías / noches')
    def unidades(self, obj):
        return f'{obj.meta_eventos} / {obj.meta_pasadias} / {obj.meta_noches}'

    @admin.display(description='Marketing')
    def marketing(self, obj):
        return ui.monto(obj.presupuesto_marketing)

    actions = ['proponer_desde_equilibrio']

    @admin.action(description='Proponer desde equilibrio')
    def proponer_desde_equilibrio(self, request, queryset):
        from .services.metas import proponer_meta

        meses = sorted({(m.anio, m.mes) for m in queryset})
        for anio, mes in meses:
            proponer_meta(anio, mes, request.user)
        self.message_user(request, f'Metas propuestas desde el equilibrio: {len(meses)} mes(es).',
                          messages.SUCCESS)
