"""Bitácora de los estados de cuenta que llegan por correo (Issue #401). Solo lectura, solo Dirección."""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.descargas import url_descarga

from . import services_estados_correo as servicio
from .admin_sat import SoloDireccion
from .models import CorreoEstadoCuenta
from .views_estados_correo import MAX_CUERPO


@admin.register(CorreoEstadoCuenta)
class CorreoEstadoCuentaAdmin(SoloDireccion, admin.ModelAdmin):
    change_list_template = 'admin/contabilidad/correoestadocuenta/change_list.html'
    list_display = ['recibido_en', 'asunto_display', 'resultado_display', 'detalle_display', 'intentos',
                    'estado_cuenta', 'eml_display']
    columnas_texto = ('asunto_display', 'detalle_display')
    list_filter = [('resultado', con_titulo('Resultado')), filtro_periodo('recibido_en', 'Recibido')]
    search_fields = ['asunto', 'remitente', 'message_id']
    search_help_text = 'Asunto, remitente o Message-ID'
    actions = ['reprocesar']
    TONOS = {'PROCESADO': ui.EXITO, 'ERROR': ui.ERROR, 'PENDIENTE': ui.ALERTA, 'PROCESANDO': ui.ALERTA, 'IGNORADO': ui.NEUTRO,
             'DUPLICADO': ui.NEUTRO}

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path('subir/', self.admin_site.admin_view(self.subir_view),
                     name='contabilidad_correoestadocuenta_subir')] + super().get_urls()

    def subir_view(self, request):
        """Sube el original de un correo de BBVA (Gmail → Mostrar original → Descargar) y lo
        procesa con el mismo flujo que el webhook: bitácora, firma DKIM, contraseña y periodo.
        Sirve para correos que ya estaban en Gmail antes de activar el reenvío."""
        if not request.user.is_superuser:
            raise PermissionDenied
        lista = reverse('admin:contabilidad_correoestadocuenta_changelist')
        if request.method == 'POST':
            archivo = request.FILES.get('eml')
            if not archivo:
                messages.error(request, "Elige el archivo .eml del correo.")
                return redirect(request.path)
            if archivo.size > MAX_CUERPO:
                messages.error(request, "El archivo pasa de 15 MB; no es un estado de cuenta de BBVA.")
                return redirect(request.path)
            bitacora = servicio.registrar_correo(archivo.read())
            if bitacora.resultado == 'DUPLICADO':
                messages.warning(request, "Ese correo ya estaba registrado; usa «Reprocesar» si hace falta.")
                return redirect(lista)
            bitacora = servicio.procesar_correo(bitacora)
            nivel = {'PROCESADO': messages.SUCCESS, 'IGNORADO': messages.WARNING}.get(bitacora.resultado, messages.ERROR)
            messages.add_message(request, nivel, f"{bitacora.get_resultado_display()}: {bitacora.detalle or 'listo'}")
            return redirect(lista)
        return TemplateResponse(request, 'admin/contabilidad/correoestadocuenta/subir.html', {
            **self.admin_site.each_context(request), 'title': 'Subir correo del banco', 'opts': self.model._meta,
        })

    @admin.display(description="Asunto", ordering="asunto")
    def asunto_display(self, obj):
        return format_html('{}<div class="qkt-sub">{}</div>', obj.asunto[:80] or '(sin asunto)', obj.remitente[:80])

    @admin.display(description="Resultado", ordering="resultado")
    def resultado_display(self, obj):
        return ui.badge_por_valor(obj.resultado, self.TONOS, obj.get_resultado_display())

    @admin.display(description="Detalle")
    def detalle_display(self, obj):
        texto = obj.detalle or obj.advertencia
        return format_html('{}', texto[:140]) if texto else ui.vacio()

    @admin.display(description="")
    def eml_display(self, obj):
        url = url_descarga(obj, 'archivo_eml')
        return ui.acciones(ui.boton_icono(url, 'envelope', 'Correo (.eml)', nueva_pestana=True)
                           if url else ui.hueco_icono())

    @admin.action(description="Reprocesar")
    def reprocesar(self, request, queryset):
        procesados = 0
        for bitacora in queryset.filter(resultado__in=('PENDIENTE', 'ERROR', 'IGNORADO')):
            bitacora.resultado, bitacora.intentos = 'PENDIENTE', 0
            bitacora.save(update_fields=['resultado', 'intentos', 'updated_at'])
            if servicio.procesar_correo(bitacora).resultado == 'PROCESADO':
                procesados += 1
        messages.info(request, f"{procesados} correo(s) procesado(s) de {queryset.count()} seleccionado(s).")
