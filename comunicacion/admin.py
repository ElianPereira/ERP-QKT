from django.contrib import admin
from django.utils import timezone
from django.utils.formats import date_format

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo

from .models import ComunicacionCliente, ConversacionWhatsApp, MensajeWhatsApp


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


class MensajeWhatsAppInline(admin.TabularInline):
    model = MensajeWhatsApp
    extra = 0
    can_delete = False
    fields = ('created_at', 'direccion', 'texto')
    readonly_fields = fields
    ordering = ('created_at', 'id')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ConversacionWhatsApp)
class ConversacionWhatsAppAdmin(admin.ModelAdmin):
    """Conversaciones del agente de WhatsApp (Issue #346). Nacen solas desde el
    webhook; aquí solo se leen y se decide si el agente sigue contestando."""
    list_display = ('ultimo_display', 'nombre', 'telefono', 'estado_badge', 'cliente')
    list_filter = ('requiere_humano',)
    search_fields = ('telefono', 'nombre', 'cliente__nombre', 'mensajes__texto')
    fields = ('nombre', 'telefono', 'cliente', 'requiere_humano', 'motivo_humano',
              'pausado_hasta', 'ultimo_mensaje')
    readonly_fields = ('nombre', 'telefono', 'ultimo_mensaje')
    autocomplete_fields = ('cliente',)
    inlines = (MensajeWhatsAppInline,)
    actions = ('reactivar_agente',)

    def has_add_permission(self, request):
        return False

    @admin.display(description='Último mensaje', ordering='ultimo_mensaje')
    def ultimo_display(self, obj):
        return date_format(timezone.localtime(obj.ultimo_mensaje), 'd M Y H:i')

    @admin.display(description='Agente')
    def estado_badge(self, obj):
        if obj.requiere_humano:
            return ui.badge('Requiere humano', ui.ERROR)
        if obj.agente_en_pausa():
            return ui.badge('En pausa', ui.ALERTA)
        return ui.badge('Contestando', ui.EXITO)

    @admin.action(description='Reactivar el agente en las seleccionadas')
    def reactivar_agente(self, request, queryset):
        n = queryset.update(requiere_humano=False, motivo_humano='', pausado_hasta=None)
        self.message_user(request, f'Agente reactivado en {n} conversación(es).')
