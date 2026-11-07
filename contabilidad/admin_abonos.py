"""Abonos del estado de cuenta atribuidos a cotizaciones. Solo lectura, solo Dirección.

Las acciones son el respaldo del botón de WhatsApp (ventana de 24 h cerrada o botón vencido)."""
from django.contrib import admin, messages
from django.utils.html import format_html

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.admin_utils import confirmar_accion_destructiva

from . import services_abonos as servicio
from .admin_sat import SoloDireccion
from .models import AbonoIdentificado


@admin.register(AbonoIdentificado)
class AbonoIdentificadoAdmin(SoloDireccion, admin.ModelAdmin):
    list_display = ['fecha', 'importe_display', 'descripcion_display', 'cotizacion_display', 'capa', 'estado_display',
                    'motivo_display']
    columnas_texto = ('descripcion_display', 'motivo_display')
    list_filter = [('estado', con_titulo('Estado')), ('capa', con_titulo('Pista')), filtro_periodo('fecha', 'Fecha')]
    search_fields = ['descripcion', 'huella']
    search_help_text = 'Concepto del banco'
    actions = ['registrar_pago', 'descartar']
    TONOS = {'REGISTRADO': ui.EXITO, 'PROPUESTO': ui.ALERTA, 'ERROR': ui.ERROR, 'DESCARTADO': ui.NEUTRO,
             'SIN_IDENTIFICAR': ui.NEUTRO}

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Importe", ordering="importe")
    def importe_display(self, obj):
        return ui.monto(obj.importe)

    @admin.display(description="Concepto del banco")
    def descripcion_display(self, obj):
        return format_html('{}', obj.descripcion[:90]) if obj.descripcion else ui.vacio()

    @admin.display(description="Cotización")
    def cotizacion_display(self, obj):
        return f'COT-{obj.cotizacion_id:03d}' if obj.cotizacion_id else ui.vacio()

    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS, obj.get_estado_display())

    @admin.display(description="Motivo")
    def motivo_display(self, obj):
        return format_html('{}', obj.motivo[:120]) if obj.motivo else ui.vacio()

    @admin.action(description="Registrar como pago de la cotización propuesta")
    @confirmar_accion_destructiva("Se creará un pago de transferencia por el importe del abono en la cotización propuesta.")
    def registrar_pago(self, request, queryset):
        if not request.user.is_superuser:
            messages.error(request, "Solo Dirección puede registrar abonos.")
            return
        for abono in queryset.filter(estado__in=('PROPUESTO', 'ERROR')):
            resultado = servicio.registrar_propuesto(abono.pk, telefono=f'admin:{request.user.username}')
            messages.add_message(request, messages.SUCCESS if resultado.pago else messages.WARNING,
                                 f"{abono}: {resultado.texto}")

    @admin.action(description="Descartar: no es del cliente propuesto")
    @confirmar_accion_destructiva("El abono quedará descartado y se avisará al encargado para identificarlo.")
    def descartar(self, request, queryset):
        if not request.user.is_superuser:
            messages.error(request, "Solo Dirección puede descartar abonos.")
            return
        for abono in queryset.filter(estado__in=('PROPUESTO', 'ERROR')):
            resultado = servicio.descartar_propuesto(abono.pk, telefono=f'admin:{request.user.username}')
            messages.info(request, f"{abono}: {resultado.texto}")
