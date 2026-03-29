"""
Admin del Módulo de Facturación
===============================
Sistema de Diseño QKT v2.0
"""
import os
from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.urls import path, reverse
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from .models import SolicitudFactura, ConfiguracionContador
from decimal import Decimal
from django.template.loader import render_to_string
from django.core.files.base import ContentFile
from weasyprint import HTML

@admin.register(ConfiguracionContador)
class ConfiguracionContadorAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'email', 'telefono_whatsapp', 'activo']
    list_filter = ['activo']
    
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
        'folio_display',
        'cliente_display',
        'monto_display',
        'forma_pago',
        'fecha_display',
        'estado_display',
        'acciones_display',
    ]
    list_filter = ['estado', 'forma_pago', 'fecha_solicitud']
    search_fields = ['cliente__nombre', 'rfc', 'razon_social', 'concepto']
    date_hierarchy = 'fecha_solicitud'
    ordering = ['-fecha_solicitud']
    readonly_fields = [
        'created_by', 'created_at', 'updated_at',
        'enviada_por', 'fecha_envio', 'metodo_envio',
        'uuid_factura'
    ]
    
    fieldsets = (
        ('Cliente', {
            'fields': ('cliente',)
        }),
        ('Datos Fiscales', {
            'fields': (
                ('rfc', 'razon_social'),
                ('codigo_postal', 'regimen_fiscal'),
                'uso_cfdi'
            )
        }),
        ('Datos del Pago', {
            'fields': (
                ('monto', 'concepto'),
                ('forma_pago', 'metodo_pago'),
                'fecha_pago'
            )
        }),
        ('Estado y Envío', {
            'fields': (
                'estado',
                ('enviada_por', 'fecha_envio', 'metodo_envio'),
            )
        }),
        ('Archivos de Factura', {
            'fields': (
                'archivo_zip',
                ('archivo_pdf', 'archivo_xml'),
                ('uuid_factura', 'fecha_factura'),
            ),
            'description': 'Sube el ZIP con PDF y XML, o ambos archivos por separado.'
        }),
        ('Notas', {
            'fields': ('notas',),
            'classes': ('collapse',)
        }),
        ('Auditoría', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    # ─── Display methods ──────────────────────────────────────
    
    @admin.display(description="Folio", ordering="id")
    def folio_display(self, obj):
        if not obj.id:
            return "-"
        folio = str(obj.id).zfill(4)
        return format_html(
            '<span style="color:#4CAF50; font-weight:600;">SOL-{}</span>',
            folio
        )
    
    @admin.display(description="Cliente", ordering="cliente__nombre")
    def cliente_display(self, obj):
        if not obj.cliente:
            return "-"
        nombre = obj.cliente.nombre
        if len(nombre) > 35:
            nombre = nombre[:35] + "..."
        return format_html(
            '<span style="color:#d4d1c8;">{}</span>',
            nombre
        )
    
    @admin.display(description="Monto", ordering="monto")
    def monto_display(self, obj):
        if not obj.monto:
            return "-"
        monto_str = "${:,.2f}".format(float(obj.monto))
        return format_html(
            '<span style="font-weight:600; color:#d4d1c8;">{}</span>',
            monto_str
        )
    
    @admin.display(description="Fecha", ordering="fecha_solicitud")
    def fecha_display(self, obj):
        if not obj.fecha_solicitud:
            return "-"
        return obj.fecha_solicitud.strftime('%d/%m/%Y')
    
    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        colores = {
            'PENDIENTE': '#e67e22',
            'ENVIADA': '#3498db',
            'FACTURADA': '#27ae60',
            'CANCELADA': '#95a5a6',
        }
        color = colores.get(obj.estado, '#95a5a6')
        estado_texto = obj.get_estado_display()
        return format_html(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}</span>',
            color,
            estado_texto
        )
    
    @admin.display(description="Acciones")
    def acciones_display(self, obj):
        if not obj.id:
            return "-"
        
        obj_id = int(obj.id)
        
        if obj.estado == 'FACTURADA':
            if obj.archivo_zip:
                return format_html(
                    '<a href="{}" target="_blank" '
                    'style="background:#27ae60; color:#fff; padding:4px 10px; '
                    'border-radius:4px; font-size:11px; text-decoration:none; font-weight:600;">'
                    'Descargar ZIP</a>',
                    obj.archivo_zip.url
                )
            elif obj.archivo_pdf:
                return format_html(
                    '<a href="{}" target="_blank" '
                    'style="background:#27ae60; color:#fff; padding:4px 10px; '
                    'border-radius:4px; font-size:11px; text-decoration:none; font-weight:600;">'
                    'Descargar PDF</a>',
                    obj.archivo_pdf.url
                )
            return mark_safe(
                '<span style="color:#27ae60; font-weight:600;">Facturada</span>'
            )
        
        if obj.estado == 'CANCELADA':
            return mark_safe(
                '<span style="color:#95a5a6;">Cancelada</span>'
            )
        
        html_parts = []

        pdf_url = '/admin/facturacion/solicitudfactura/{}/generar_pdf/'.format(obj_id)
        pdf_btn = (
            '<a href="{pdf_url}" target="_blank" '
            'style="background:#8e44ad; color:#fff; padding:4px 10px; '
            'border-radius:4px; font-size:11px; text-decoration:none; font-weight:600; margin-right:4px;">'
            'PDF</a>'
        ).format(pdf_url=pdf_url)
        html_parts.append(pdf_btn)        
        
        whatsapp_url = obj.get_whatsapp_url()
        if whatsapp_url:
            wa_btn = (
                '<a href="{wa_url}" target="_blank" '
                'style="background:#25D366; color:#fff; padding:4px 10px; '
                'border-radius:4px; font-size:11px; text-decoration:none; font-weight:600; margin-right:4px;" '
                'onclick="marcarEnviada({obj_id}, \'WHATSAPP\')">'
                'WhatsApp</a>'
            ).format(wa_url=whatsapp_url, obj_id=obj_id)
            html_parts.append(wa_btn)
        
        email_url = '/admin/facturacion/solicitudfactura/{}/enviar_email/'.format(obj_id)
        email_btn = (
            '<a href="{email_url}" '
            'style="background:#3498db; color:#fff; padding:4px 10px; '
            'border-radius:4px; font-size:11px; text-decoration:none; font-weight:600;">'
            'Email</a>'
        ).format(email_url=email_url)
        html_parts.append(email_btn)
        
        return mark_safe(''.join(html_parts))
    
    # ─── Custom URLs ──────────────────────────────────────────
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:solicitud_id>/generar_pdf/',
                self.admin_site.admin_view(self.generar_pdf_view),
                name='solicitudfactura_generar_pdf'
            ),
            path(
                '<int:solicitud_id>/enviar_email/',
                self.admin_site.admin_view(self.enviar_email_view),
                name='solicitudfactura_enviar_email'
            ),
            path(
                '<int:solicitud_id>/marcar_enviada/',
                self.admin_site.admin_view(self.marcar_enviada_view),
                name='solicitudfactura_marcar_enviada'
            ),
        ]
        return custom_urls + urls
    
    def generar_pdf_view(self, request, solicitud_id):
        """Genera y retorna el PDF de la solicitud para el contador."""
        solicitud = SolicitudFactura.objects.select_related('cliente', 'cotizacion').get(pk=solicitud_id)
        cliente = solicitud.cliente

        ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
        if os.name == 'nt':
            logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}"
        else:
            logo_url = f"file://{ruta_logo}"

        # Todo ingreso tiene IVA — el monto del pago ya incluye IVA
        total = Decimal(str(solicitud.monto))
        subtotal = (total / Decimal('1.16')).quantize(Decimal('0.01'))
        iva = (total - subtotal).quantize(Decimal('0.01'))
        ret_isr = Decimal('0.00')
        if getattr(cliente, 'tipo_persona', None) == 'MORAL':
            ret_isr = (subtotal * Decimal('0.0125')).quantize(Decimal('0.01'))

        context = {
            'solicitud': solicitud,
            'cliente': cliente,
            'folio': f"SOL-{int(solicitud.id):03d}",
            'logo_url': logo_url,
            'calc_subtotal': subtotal,
            'calc_iva': iva,
            'calc_ret_isr': ret_isr,
            'calc_total': total,
        }

        html_string = render_to_string('facturacion/solicitud_pdf.html', context)
        pdf_file = HTML(string=html_string).write_pdf()

        response = HttpResponse(pdf_file, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="Solicitud_SOL-{solicitud.id:04d}.pdf"'
        return response
    
    def enviar_email_view(self, request, solicitud_id):
        """Envía la solicitud por email al contador."""
        solicitud = SolicitudFactura.objects.get(pk=solicitud_id)
        contador = ConfiguracionContador.get_activo()
        
        if not contador:
            messages.error(request, "No hay contador configurado. Ve a Configuración del Contador.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))
        
        try:
            folio = str(solicitud.id).zfill(4)
            asunto = "Solicitud de Factura SOL-{} | {}".format(folio, solicitud.cliente.nombre)
            cuerpo = solicitud.get_datos_para_contador()
            
            send_mail(
                subject=asunto,
                message=cuerpo,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[contador.email],
                fail_silently=False,
            )
            
            solicitud.marcar_enviada(request.user, 'EMAIL')
            messages.success(request, "Solicitud enviada por email a {}".format(contador.email))
            
        except Exception as e:
            messages.error(request, "Error al enviar email: {}".format(str(e)))
        
        return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))
    
    def marcar_enviada_view(self, request, solicitud_id):
        """Marca la solicitud como enviada (llamado desde JS después de WhatsApp)."""
        solicitud = SolicitudFactura.objects.get(pk=solicitud_id)
        metodo = request.GET.get('metodo', 'WHATSAPP')
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
        self.message_user(request, "{} solicitud(es) marcada(s) como enviadas".format(count))
    
    @admin.action(description="Cancelar solicitudes")
    def marcar_canceladas(self, request, queryset):
        count = queryset.exclude(estado='FACTURADA').update(estado='CANCELADA')
        self.message_user(request, "{} solicitud(es) cancelada(s)".format(count))
    
    class Media:
        js = ('admin/js/solicitud_factura.js',)