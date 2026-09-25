from datetime import date, timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.utils.timezone import now

from core_erp import admin_ui as ui
from core_erp.descargas import url_descarga

from .models import Empleado, ReciboNomina

try:
    from .views import cargar_nomina, sync_jibble_view
except ImportError:
    cargar_nomina = sync_jibble_view = None


@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'puesto', 'tarifa_display', 'telefono', 'activo')

    @admin.display(description='Tarifa base', ordering='tarifa_base')
    def tarifa_display(self, obj):
        return ui.monto(obj.tarifa_base)
    list_filter = ('puesto', 'activo')
    search_fields = ('nombre',)


@admin.register(ReciboNomina)
class ReciboNominaAdmin(admin.ModelAdmin):
    change_list_template = 'admin/nomina/recibonomina/change_list.html'

    list_display = ('folio_custom', 'empleado', 'periodo', 'total_display', 'estado_badge', 'ver_pdf')
    list_filter = ('estado', 'periodo', 'empleado')
    actions = ['marcar_como_pagado']

    def marcar_como_pagado(self, request, queryset):
        from .services import marcar_recibo_como_pagado
        pendientes = queryset.filter(estado='CALCULADO')
        if not pendientes.exists():
            self.message_user(request, "No hay recibos en estado CALCULADO en la selección.", level=messages.WARNING)
            return
        exitosos = 0
        for recibo in pendientes:
            marcar_recibo_como_pagado(recibo, fecha_pago=now().date(), usuario=request.user)
            exitosos += 1
        self.message_user(request, f"{exitosos} recibo(s) marcado(s) como pagados (administrativo, sin impacto contable).", level=messages.SUCCESS)
    marcar_como_pagado.short_description = "Marcar como pagado en efectivo (solo administrativo)"

    TONOS_ESTADO = {'CALCULADO': ui.ALERTA, 'PAGADO': ui.EXITO, 'CANCELADO': ui.ERROR}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('empleado')

    @admin.display(description='Folio', ordering='id')
    def folio_custom(self, obj):
        return f"NOM-{obj.id:03d}"

    @admin.display(description='Total', ordering='total_pagado')
    def total_display(self, obj):
        return ui.monto(obj.total_pagado)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='')
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return ui.acciones(ui.boton_icono(url_descarga(obj, 'archivo_pdf'), 'file-pdf', 'Recibo PDF',
                                              nueva_pestana=True))
        return ui.acciones(ui.hueco_icono())

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['boton_carga'] = True

        jibble_id = getattr(settings, 'JIBBLE_CLIENT_ID', '')
        jibble_secret = getattr(settings, 'JIBBLE_CLIENT_SECRET', '')
        jibble_configurado = bool(jibble_id and jibble_secret)
        extra_context['jibble_configurado'] = jibble_configurado

        if jibble_configurado:
            hoy = date.today()
            lunes_pasado = hoy - timedelta(days=hoy.weekday() + 7)
            domingo_pasado = lunes_pasado + timedelta(days=6)
            extra_context['jibble_fecha_inicio'] = lunes_pasado.strftime('%Y-%m-%d')
            extra_context['jibble_fecha_fin'] = domingo_pasado.strftime('%Y-%m-%d')

        return super().changelist_view(request, extra_context=extra_context)
