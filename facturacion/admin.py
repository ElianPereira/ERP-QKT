"""
Admin del Módulo de Facturación
===============================
Sistema de Diseño QKT v2.0
"""
import os
import io
import requests
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import path, reverse
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from .models import SolicitudFactura, ConfiguracionContador, EmisorFiscal
from .services.facturama_client import FacturamaError
from .services.facturama_service import (
    emitir_cfdi_desde_solicitud,
    SolicitudNoFacturableError,
)
from decimal import Decimal
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from weasyprint import HTML
from decouple import config
import logging

logger = logging.getLogger(__name__)


def _generar_pdf_solicitud(solicitud):
    """Genera el PDF de la solicitud y retorna los bytes."""
    cliente = solicitud.cliente

    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    if os.name == 'nt':
        logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}"
    else:
        logo_url = f"file://{ruta_logo}"

    total    = Decimal(str(solicitud.monto))
    subtotal = (total / Decimal('1.16')).quantize(Decimal('0.01'))
    iva      = (total - subtotal).quantize(Decimal('0.01'))
    ret_isr  = Decimal('0.00')
    if getattr(cliente, 'tipo_persona', None) == 'MORAL':
        ret_isr = (subtotal * Decimal('0.0125')).quantize(Decimal('0.01'))

    context = {
        'solicitud':    solicitud,
        'cliente':      cliente,
        'folio':        f"SOL-{int(solicitud.id):03d}",
        'logo_url':     logo_url,
        'calc_subtotal':subtotal,
        'calc_iva':     iva,
        'calc_ret_isr': ret_isr,
        'calc_total':   total,
    }
    html_string = render_to_string('facturacion/solicitud_pdf.html', context)
    return HTML(string=html_string).write_pdf()


def _enviar_pdf_whatsapp(pdf_bytes, filename, telefono, folio, cliente_nombre):
    """
    Envía el PDF de la solicitud al contador via WhatsApp Cloud API.
    1. Sube el PDF al Media API → obtiene media_id
    2. Envía mensaje tipo 'document' con el media_id
    Retorna (True, '') o (False, 'mensaje de error')
    """
    wa_token    = config('WA_CLOUD_API_TOKEN', default='')
    wa_phone_id = config('WA_PHONE_NUMBER_ID', default='')

    if not wa_token or not wa_phone_id:
        return False, "WA_CLOUD_API_TOKEN o WA_PHONE_NUMBER_ID no configurados."

    headers_auth = {"Authorization": f"Bearer {wa_token}"}

    # 1. Subir PDF
    try:
        resp_upload = requests.post(
            f"https://graph.facebook.com/v19.0/{wa_phone_id}/media",
            headers=headers_auth,
            files={
                'file':               (filename, io.BytesIO(pdf_bytes), 'application/pdf'),
                'messaging_product':  (None, 'whatsapp'),
                'type':               (None, 'application/pdf'),
            },
            timeout=30,
        )
    except Exception as e:
        return False, f"Error al subir PDF: {e}"

    if resp_upload.status_code != 200:
        return False, f"Error upload ({resp_upload.status_code}): {resp_upload.text[:200]}"

    media_id = resp_upload.json().get('id')
    if not media_id:
        return False, f"No se obtuvo media_id: {resp_upload.text[:200]}"

    # 2. Enviar documento
    try:
        resp_send = requests.post(
            f"https://graph.facebook.com/v19.0/{wa_phone_id}/messages",
            headers={**headers_auth, "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": telefono,
                "type": "document",
                "document": {
                    "id": media_id,
                    "filename": filename,
                    "caption": f"Solicitud de Factura {folio} — {cliente_nombre}",
                },
            },
            timeout=15,
        )
    except Exception as e:
        return False, f"Error al enviar documento: {e}"

    if resp_send.status_code == 200:
        return True, ''
    return False, f"Error envío ({resp_send.status_code}): {resp_send.text[:200]}"


@admin.register(EmisorFiscal)
class EmisorFiscalAdmin(admin.ModelAdmin):
    list_display = [
        'nombre_interno', 'rfc', 'razon_social',
        'regimen_fiscal', 'unidad_negocio', 'serie_folio', 'activo',
    ]
    list_filter = ['activo', 'regimen_fiscal', 'unidad_negocio']
    search_fields = ['nombre_interno', 'rfc', 'razon_social']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Identificación', {
            'fields': ('nombre_interno', 'unidad_negocio', 'activo'),
        }),
        ('Datos fiscales', {
            'fields': (
                ('rfc', 'razon_social'),
                ('regimen_fiscal', 'codigo_postal'),
                'lugar_expedicion',
            ),
        }),
        ('Facturación', {
            'fields': ('serie_folio',),
        }),
        ('Notas', {'fields': ('notas',), 'classes': ('collapse',)}),
        ('Auditoría', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


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
        'folio_display', 'cliente_display', 'emisor_display', 'monto_display',
        'forma_pago', 'fecha_display', 'estado_display', 'acciones_display',
    ]
    list_filter    = ['estado', 'emisor', 'forma_pago', 'fecha_solicitud']
    search_fields  = ['cliente__nombre', 'rfc', 'razon_social', 'concepto']
    date_hierarchy = 'fecha_solicitud'
    ordering       = ['-fecha_solicitud']
    readonly_fields = [
        'created_by', 'created_at', 'updated_at',
        'enviada_por', 'fecha_envio', 'metodo_envio', 'uuid_factura'
    ]

    fieldsets = (
        ('Cliente', {'fields': ('cliente',)}),
        ('Emisor Fiscal (RFC desde el que se emite)', {
            'fields': ('emisor',),
            'description': (
                'Elige el RFC emisor. Las nuevas solicitudes creadas '
                'desde pagos se asignan automáticamente según la unidad '
                'de negocio.'
            ),
        }),
        ('Datos Fiscales', {
            'fields': (('rfc', 'razon_social'), ('codigo_postal', 'regimen_fiscal'), 'uso_cfdi')
        }),
        ('Datos del Pago', {
            'fields': (('monto', 'concepto'), ('forma_pago', 'metodo_pago'), 'fecha_pago')
        }),
        ('Estado y Envío', {
            'fields': ('estado', ('enviada_por', 'fecha_envio', 'metodo_envio'))
        }),
        ('Archivos de Factura', {
            'fields': ('archivo_zip', ('archivo_pdf', 'archivo_xml'), ('uuid_factura', 'fecha_factura')),
            'description': 'Sube el ZIP con PDF y XML, o ambos archivos por separado.'
        }),
        ('Notas',     {'fields': ('notas',), 'classes': ('collapse',)}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    # ─── Display methods ──────────────────────────────────────

    @admin.display(description="Folio", ordering="id")
    def folio_display(self, obj):
        if not obj.id:
            return "-"
        return format_html(
            '<span style="color:#4CAF50; font-weight:600;">SOL-{}</span>',
            str(obj.id).zfill(4)
        )

    @admin.display(description="Emisor", ordering="emisor__nombre_interno")
    def emisor_display(self, obj):
        if not obj.emisor:
            return format_html(
                '<span style="color:#e67e22;">— sin emisor —</span>'
            )
        return format_html(
            '<span style="color:#8e44ad; font-weight:600;">{}</span>',
            obj.emisor.nombre_interno,
        )

    @admin.display(description="Cliente", ordering="cliente__nombre")
    def cliente_display(self, obj):
        if not obj.cliente:
            return "-"
        nombre = obj.cliente.nombre
        if len(nombre) > 35:
            nombre = nombre[:35] + "..."
        return format_html('<span style="color:#d4d1c8;">{}</span>', nombre)

    @admin.display(description="Monto", ordering="monto")
    def monto_display(self, obj):
        if not obj.monto:
            return "-"
        return format_html(
            '<span style="font-weight:600; color:#d4d1c8;">{}</span>',
            "${:,.2f}".format(float(obj.monto))
        )

    @admin.display(description="Fecha", ordering="fecha_solicitud")
    def fecha_display(self, obj):
        if not obj.fecha_solicitud:
            return "-"
        return obj.fecha_solicitud.strftime('%d/%m/%Y')

    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        colores = {
            'PENDIENTE': '#e67e22', 'ENVIADA': '#3498db',
            'FACTURADA': '#27ae60', 'CANCELADA': '#95a5a6',
        }
        return format_html(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            colores.get(obj.estado, '#95a5a6'), obj.get_estado_display()
        )

    @admin.display(description="Acciones")
    def acciones_display(self, obj):
        if not obj.id:
            return "-"

        obj_id = int(obj.id)

        if obj.estado == 'FACTURADA':
            if obj.archivo_zip:
                return format_html(
                    '<a href="{}" target="_blank" style="background:#27ae60; color:#fff; '
                    'padding:4px 10px; border-radius:4px; font-size:11px; '
                    'text-decoration:none; font-weight:600;">Descargar ZIP</a>',
                    obj.archivo_zip.url
                )
            elif obj.archivo_pdf:
                return format_html(
                    '<a href="{}" target="_blank" style="background:#27ae60; color:#fff; '
                    'padding:4px 10px; border-radius:4px; font-size:11px; '
                    'text-decoration:none; font-weight:600;">Descargar PDF</a>',
                    obj.archivo_pdf.url
                )
            return mark_safe('<span style="color:#27ae60; font-weight:600;">Facturada</span>')

        if obj.estado == 'CANCELADA':
            return mark_safe('<span style="color:#95a5a6;">Cancelada</span>')

        btn = '<a href="{url}" {extra} style="background:{bg}; color:{fg}; padding:4px 10px; border-radius:4px; font-size:11px; text-decoration:none; font-weight:600; margin-right:4px;">{label}</a>'

        html_parts = [
            btn.format(
                url=f'/admin/facturacion/solicitudfactura/{obj_id}/generar_pdf/',
                extra='target="_blank"', bg='#8e44ad', fg='#fff', label='PDF'
            ),
            btn.format(
                url=f'/admin/facturacion/solicitudfactura/{obj_id}/enviar_whatsapp/',
                extra='', bg='#25D366', fg='#fff', label='WhatsApp'
            ),
            btn.format(
                url=f'/admin/facturacion/solicitudfactura/{obj_id}/enviar_email/',
                extra='', bg='#3498db', fg='#fff', label='Email'
            ),
        ]
        return mark_safe(''.join(html_parts))

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
        solicitud = SolicitudFactura.objects.select_related('cliente', 'cotizacion').get(pk=solicitud_id)
        pdf_bytes = _generar_pdf_solicitud(solicitud)
        response  = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Solicitud_SOL-{solicitud.id:04d}.pdf"'
        return response

    def enviar_whatsapp_view(self, request, solicitud_id):
        """Genera el PDF y lo envía al contador via WhatsApp Cloud API."""
        solicitud = SolicitudFactura.objects.select_related('cliente').get(pk=solicitud_id)
        contador  = ConfiguracionContador.get_activo()

        if not contador:
            messages.error(request, "No hay contador configurado.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

        telefono = ''.join(filter(str.isdigit, contador.telefono_whatsapp or ''))
        if not telefono:
            messages.error(request, "El contador no tiene teléfono WhatsApp configurado.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

        try:
            pdf_bytes = _generar_pdf_solicitud(solicitud)
        except Exception as e:
            messages.error(request, f"Error al generar PDF: {e}")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

        folio    = f"SOL-{solicitud.id:04d}"
        filename = f"Solicitud_{folio}.pdf"
        ok, error = _enviar_pdf_whatsapp(
            pdf_bytes, filename, telefono, folio, solicitud.cliente.nombre
        )

        if ok:
            solicitud.marcar_enviada(request.user, 'WHATSAPP')
            messages.success(request, f"PDF {folio} enviado por WhatsApp a {contador.nombre}.")
        else:
            messages.error(request, f"Error WhatsApp: {error}")

        return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

    def enviar_email_view(self, request, solicitud_id):
        """Envía email al contador con PDF adjunto."""
        solicitud = SolicitudFactura.objects.select_related('cliente').get(pk=solicitud_id)
        contador  = ConfiguracionContador.get_activo()

        if not contador:
            messages.error(request, "No hay contador configurado.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

        try:
            from django.core.mail import EmailMessage
            pdf_bytes = _generar_pdf_solicitud(solicitud)
            folio     = f"SOL-{solicitud.id:04d}"
            email     = EmailMessage(
                subject=f"Solicitud de Factura {folio} | {solicitud.cliente.nombre}",
                body=solicitud.get_datos_para_contador(),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[contador.email],
            )
            email.attach(f"Solicitud_{folio}.pdf", pdf_bytes, 'application/pdf')
            email.send()
            solicitud.marcar_enviada(request.user, 'EMAIL')
            messages.success(request, f"Email enviado a {contador.email} con PDF adjunto.")
        except Exception as e:
            messages.error(request, f"Error al enviar email: {e}")

        return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))

    def marcar_enviada_view(self, request, solicitud_id):
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

    actions = ['marcar_enviadas', 'marcar_canceladas', 'emitir_ante_sat']

    @admin.action(description="Marcar como enviadas")
    def marcar_enviadas(self, request, queryset):
        count = 0
        for sol in queryset.filter(estado='PENDIENTE'):
            sol.marcar_enviada(request.user, 'EMAIL')
            count += 1
        self.message_user(request, f"{count} solicitud(es) marcada(s) como enviadas.")

    @admin.action(description="Cancelar solicitudes")
    def marcar_canceladas(self, request, queryset):
        count = queryset.exclude(estado='FACTURADA').update(estado='CANCELADA')
        self.message_user(request, f"{count} solicitud(es) cancelada(s).")

    @admin.action(description="Emitir CFDI ante el SAT (Facturama)")
    def emitir_ante_sat(self, request, queryset):
        """
        Emite cada solicitud seleccionada como CFDI ante el SAT.
        Omite las ya facturadas/canceladas y reporta errores uno por uno.
        """
        ok = 0
        errores = []
        for solicitud in queryset.select_related('emisor', 'cliente'):
            try:
                resultado = emitir_cfdi_desde_solicitud(solicitud)
                ok += 1
                messages.success(
                    request,
                    f"SOL-{solicitud.pk:04d} timbrada: UUID {resultado.uuid}"
                )
            except SolicitudNoFacturableError as exc:
                errores.append(f"SOL-{solicitud.pk:04d}: {exc.message}")
            except FacturamaError as exc:
                errores.append(f"SOL-{solicitud.pk:04d}: {exc.message}")
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Error inesperado emitiendo SolicitudFactura #%s", solicitud.pk
                )
                errores.append(
                    f"SOL-{solicitud.pk:04d}: error inesperado ({exc})"
                )

        if ok:
            messages.success(request, f"{ok} CFDI(s) emitido(s) correctamente.")
        for err in errores:
            messages.error(request, err)

    class Media:
        js = ('admin/js/solicitud_factura.js',)