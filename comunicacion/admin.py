from django.contrib import admin
from django.utils import timezone
from django.utils.formats import date_format

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo

from .models import ComunicacionCliente


@admin.register(ComunicacionCliente)
class ComunicacionClienteAdmin(admin.ModelAdmin):
    list_display = ('fecha_display', 'canal_badge', 'tipo', 'estado_badge', 'destinatario', 'cotizacion', 'trigger')
    list_filter = ('estado', 'canal', 'tipo', ('trigger', con_titulo('Origen del envío')))
    columnas_texto = ('destinatario',)

    TONOS_ESTADO = {'PENDIENTE': ui.ALERTA, 'ENVIADO': ui.INFO, 'ENTREGADO': ui.EXITO, 'ABIERTO': ui.EXITO,
                    'FALLIDO': ui.ERROR}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cotizacion__cliente')

    @admin.display(description='Fecha', ordering='fecha_envio')
    def fecha_display(self, obj):
        return date_format(timezone.localtime(obj.fecha_envio), 'd M Y H:i') if obj.fecha_envio else ui.vacio()

    @admin.display(description='Canal', ordering='canal')
    def canal_badge(self, obj):
        return ui.badge(obj.get_canal_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())
    search_fields = ('destinatario', 'asunto', 'cotizacion__nombre_evento',
                     'cotizacion__cliente__nombre', 'clave_idempotencia')
    date_hierarchy = 'fecha_envio'
    # La clave es de solo lectura: editarla a mano permitiría reenviar un evento
    # ya notificado o bloquear uno que aún no salió.
    readonly_fields = ('fecha_envio', 'fecha_entrega', 'fecha_apertura', 'proveedor_id',
                       'cuerpo', 'error', 'clave_idempotencia')
