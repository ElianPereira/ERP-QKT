from datetime import date, timedelta
from django.contrib import admin, messages
from django.conf import settings
from django.utils.html import format_html
from django.utils.timezone import now
from .models import Empleado, ReciboNomina

try:
    from .views import cargar_nomina, sync_jibble_view
except ImportError:
    cargar_nomina = sync_jibble_view = None


@admin.register(Empleado)
class EmpleadoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'puesto', 'tarifa_base', 'telefono', 'activo')
    list_filter = ('puesto', 'activo')
    search_fields = ('nombre',)


@admin.register(ReciboNomina)
class ReciboNominaAdmin(admin.ModelAdmin):
    change_list_template = 'admin/nomina/recibonomina/change_list.html'

    list_display = ('folio_custom', 'empleado', 'periodo', 'total_pagado', 'estado', 'ver_pdf')
    list_filter = ('periodo', 'empleado', 'estado')
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

    def folio_custom(self, obj):
        return f"NOM-{obj.id:03d}"
    folio_custom.short_description = "Folio"

    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return format_html(
                '<a href="{}" target="_blank" style="background:#2E7D32; color:white; '
                'padding:4px 10px; border-radius:4px; text-decoration:none; '
                'font-size:11px; font-weight:600;">PDF</a>',
                obj.archivo_pdf.url
            )
        return "-"
    ver_pdf.short_description = "Recibo"

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