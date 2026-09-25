"""
Admin del Módulo de Facturación
===============================
Sistema de Diseño QKT v2.0
"""
import logging

from django.contrib import admin, messages
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.admin_utils import confirmar_accion_destructiva
from core_erp.descargas import url_descarga

from .models import ConfiguracionContador, SolicitudFactura
from .services import enviar_solicitud_por_email, enviar_solicitud_por_whatsapp, generar_pdf_solicitud

logger = logging.getLogger(__name__)


@admin.register(ConfiguracionContador)
class ConfiguracionContadorAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'email', 'telefono_whatsapp', 'activo']
    list_filter  = ['activo']

    def has_add_permission(self, request):
        try:
            if ConfiguracionContador.objects.filter(activo=True).exists():
                return False
        except Exception:
            pass
        return True


@admin.register(SolicitudFactura)
class SolicitudFacturaAdmin(admin.ModelAdmin):
    list_display = [
        'folio_display', 'cliente_display', 'linea_negocio_display', 'monto_display',
        'forma_pago', 'fecha_display', 'estado_display', 'acciones_display',
    ]
    list_filter    = ['estado', filtro_periodo('fecha_solicitud', 'Fecha de solicitud'), ('forma_pago', con_titulo('Forma de pago')),
                      ('linea_negocio', con_titulo('Línea de negocio'))]
    search_fields  = ['cliente__nombre', 'rfc', 'razon_social', 'concepto']
    date_hierarchy = 'fecha_solicitud'
    ordering       = ['-fecha_solicitud']
    readonly_fields = [
        'created_by', 'created_at', 'updated_at',
        'enviada_por', 'fecha_envio', 'metodo_envio', 'ultimo_recordatorio_enviado', 'uuid_factura'
    ]

    fieldsets = (
        ('Cliente', {'fields': ('cliente', 'linea_negocio')}),
        ('Datos Fiscales', {
            'fields': (('rfc', 'razon_social'), ('codigo_postal', 'regimen_fiscal'), 'uso_cfdi')
        }),
        ('Datos del Pago', {
            'fields': (('monto', 'concepto'), ('forma_pago', 'metodo_pago'), 'fecha_pago')
        }),
        ('Estado y Envío', {
            'fields': (
                'estado',
                ('enviada_por', 'fecha_envio', 'metodo_envio'),
                'ultimo_recordatorio_enviado',
            )
        }),
        ('Archivos de Factura', {
            'fields': ('archivo_zip', ('archivo_pdf', 'archivo_xml'), ('uuid_factura', 'fecha_factura')),
            'description': 'Sube el ZIP con PDF y XML, o ambos archivos por separado.'
        }),
        ('Notas',     {'fields': ('notas',), 'classes': ('collapse',)}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    # ─── Display methods ──────────────────────────────────────

    TONOS_ESTADO = {'PENDIENTE': ui.ALERTA, 'ENVIADA': ui.INFO, 'FACTURADA': ui.EXITO, 'CANCELADA': ui.ERROR}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cliente')

    @admin.display(description="Folio", ordering="id")
    def folio_display(self, obj):
        return f"SOL-{str(obj.id).zfill(4)}" if obj.id else ui.vacio()

    @admin.display(description="Cliente", ordering="cliente__nombre")
    def cliente_display(self, obj):
        if not obj.cliente:
            return ui.vacio()
        return format_html('{}<div class="qkt-sub">{}</div>', obj.cliente.nombre, obj.rfc or 'Público en general')

    @admin.display(description="Línea", ordering="linea_negocio")
    def linea_negocio_display(self, obj):
        return ui.badge(obj.get_linea_negocio_display(), ui.NEUTRO, categoria=True)

    @admin.display(description="Monto", ordering="monto")
    def monto_display(self, obj):
        return ui.monto(obj.monto) if obj.monto else ui.vacio(numerico=True)

    @admin.display(description="Fecha", ordering="fecha_solicitud")
    def fecha_display(self, obj):
        if not obj.fecha_solicitud:
            return ui.vacio()
        return date_format(timezone.localtime(obj.fecha_solicitud), 'd M Y')

    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description="")
    def acciones_display(self, obj):
        if not obj.id:
            return ui.vacio()
        if obj.estado == 'FACTURADA':
            if obj.archivo_zip:
                return ui.acciones(ui.boton_icono(url_descarga(obj, 'archivo_zip'), 'file-zipper', 'Descargar ZIP',
                                                  nueva_pestana=True))
            if obj.archivo_pdf:
                return ui.acciones(ui.boton_icono(url_descarga(obj, 'archivo_pdf'), 'file-pdf', 'Descargar factura',
                                                  nueva_pestana=True))
            return ui.vacio()
        if obj.estado == 'CANCELADA':
            return ui.vacio()
        return ui.acciones(
            ui.boton_icono(reverse('admin:solicitudfactura_generar_pdf', args=[obj.id]), 'file-pdf',
                           'PDF de la solicitud', nueva_pestana=True),
            ui.boton_icono(reverse('admin:solicitudfactura_enviar_whatsapp', args=[obj.id]), 'comment',
                           'Enviar al contador por WhatsApp'),
            ui.boton_icono(reverse('admin:solicitudfactura_enviar_email', args=[obj.id]), 'envelope',
                           'Enviar al contador por email'),
        )

    # ─── Custom URLs ──────────────────────────────────────────

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:solicitud_id>/generar_pdf/',
                 self.admin_site.admin_view(self.generar_pdf_view),
                 name='solicitudfactura_generar_pdf'),
            path('<int:solicitud_id>/enviar_whatsapp/',
                 self.admin_site.admin_view(self.enviar_whatsapp_view),
                 name='solicitudfactura_enviar_whatsapp'),
            path('<int:solicitud_id>/enviar_email/',
                 self.admin_site.admin_view(self.enviar_email_view),
                 name='solicitudfactura_enviar_email'),
            path('<int:solicitud_id>/marcar_enviada/',
                 self.admin_site.admin_view(self.marcar_enviada_view),
                 name='solicitudfactura_marcar_enviada'),
        ]
        return custom_urls + urls

    def generar_pdf_view(self, request, solicitud_id):
        """Descarga el PDF de la solicitud."""
        if not request.user.has_perm('facturacion.view_solicitudfactura'):
            messages.error(request, "No tienes permiso para ver solicitudes de factura.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))
        solicitud = SolicitudFactura.objects.select_related('cliente', 'cotizacion').get(pk=solicitud_id)
        pdf_bytes = generar_pdf_solicitud(solicitud)
        response  = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Solicitud_SOL-{solicitud.id:04d}.pdf"'
        return response

    def enviar_whatsapp_view(self, request, solicitud_id):
        """Genera el PDF y lo envía al contador via WhatsApp Cloud API."""
        if not request.user.has_perm('facturacion.change_solicitudfactura'):
            messages.error(request, "No tienes permiso para enviar solicitudes de factura.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))
        solicitud = SolicitudFactura.objects.select_related('cliente').get(pk=solicitud_id)
        folio = f"SOL-{solicitud.id:04d}"

        ok, error = enviar_solicitud_por_whatsapp(solicitud)
        if ok:
            solicitud.marcar_enviada(request.user, 'WHATSAPP')
            contador = ConfiguracionContador.get_activo()
            messages.success(request, f"PDF {folio} enviado por WhatsApp a {contador.nombre if contador else 'el contador'}.")
        else:
            messages.error(request, f"Error WhatsApp: {error}")

        return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

    def enviar_email_view(self, request, solicitud_id):
        """Envía email al contador con PDF adjunto."""
        if not request.user.has_perm('facturacion.change_solicitudfactura'):
            messages.error(request, "No tienes permiso para enviar solicitudes de factura.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))
        solicitud = SolicitudFactura.objects.select_related('cliente').get(pk=solicitud_id)

        ok, error = enviar_solicitud_por_email(solicitud)
        if ok:
            solicitud.marcar_enviada(request.user, 'EMAIL')
            contador = ConfiguracionContador.get_activo()
            messages.success(request, f"Email enviado a {contador.email if contador else 'el contador'} con PDF adjunto.")
        else:
            messages.error(request, f"Error al enviar email: {error}")

        return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

    def marcar_enviada_view(self, request, solicitud_id):
        if not request.user.has_perm('facturacion.change_solicitudfactura'):
            return JsonResponse({'status': 'error', 'detail': 'Sin permiso.'}, status=403)
        solicitud = SolicitudFactura.objects.get(pk=solicitud_id)
        metodo    = request.GET.get('metodo', 'WHATSAPP')
        solicitud.marcar_enviada(request.user, metodo)
        return JsonResponse({'status': 'ok'})

    # ─── Save model ───────────────────────────────────────────

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    # ─── Actions ──────────────────────────────────────────────

    actions = ['marcar_enviadas', 'marcar_canceladas']

    @admin.action(description="Marcar como enviadas")
    def marcar_enviadas(self, request, queryset):
        count = 0
        for sol in queryset.filter(estado='PENDIENTE'):
            sol.marcar_enviada(request.user, 'EMAIL')
            count += 1
        self.message_user(request, f"{count} solicitud(es) marcada(s) como enviadas.")

    @admin.action(description="Cancelar solicitudes")
    @confirmar_accion_destructiva(
        "¿Cancelar las solicitudes de factura seleccionadas? Las que ya "
        "estén FACTURADA no se tocan; el resto queda como CANCELADA."
    )
    def marcar_canceladas(self, request, queryset):
        count = queryset.exclude(estado='FACTURADA').update(estado='CANCELADA')
        self.message_user(request, f"{count} solicitud(es) cancelada(s).")

    class Media:
        js = ('admin/js/solicitud_factura.js',)
