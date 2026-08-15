from django.contrib import admin

from .models import ComunicacionCliente


@admin.register(ComunicacionCliente)
class ComunicacionClienteAdmin(admin.ModelAdmin):
    list_display = ('fecha_envio', 'canal', 'tipo', 'estado', 'destinatario', 'cotizacion', 'trigger')
    list_filter = ('canal', 'tipo', 'estado', 'trigger')
    search_fields = ('destinatario', 'asunto', 'cotizacion__nombre_evento',
                     'cotizacion__cliente__nombre', 'clave_idempotencia')
    date_hierarchy = 'fecha_envio'
    # La clave es de solo lectura: editarla a mano permitiría reenviar un evento
    # ya notificado o bloquear uno que aún no salió.
    readonly_fields = ('fecha_envio', 'fecha_entrega', 'fecha_apertura', 'proveedor_id',
                       'cuerpo', 'error', 'clave_idempotencia')
