"""
Admin del Módulo de Facturación
===============================
Sistema de Diseño QKT v2.0
"""
from django.contrib import admin
from django.utils.html import format_html
from django.urls import path, reverse
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone

from .models import SolicitudFactura, ConfiguracionContador


@admin.register(ConfiguracionContador)
class ConfiguracionContadorAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'email', 'telefono_whatsapp', 'activo']
    list_filter = ['activo']
    
    def has_add_permission(self, request):
        # Solo permitir un registro activo
        try:
            if ConfiguracionContador.objects.filter(activo=True).exists():
                return False
        except Exception:
            pass  # Tabla no existe aún
        return True


@admin.register(SolicitudFactura)
class SolicitudFacturaAdmin(admin.ModelAdmin):
    change_list_template = 'admin/facturacion/solicitudfactura/change_list.html'
    
    list_display = [
        'folio_badge', 
        'cliente_link', 
        'monto_badge', 
        'forma_pago',
        'fecha_solicitud_fmt',
        'estado_badge', 
        'acciones_badge'
    ]
    list_filter = ['estado', 'forma_pago', 'fecha_solicitud']
    search_fields = ['cliente__nombre', 'rfc', 'razon_social', 'concepto']
    date_hierarchy = 'fecha_solicitud'
    ordering = ['-fecha_solicitud']
    autocomplete_fields = ['cliente', 'cotizacion', 'pago']
    readonly_fields = [
        'created_by', 'created_at', 'updated_at',
        'enviada_por', 'fecha_envio', 'metodo_envio',
        'uuid_factura'
    ]
    
    fieldsets = (
        ('Cliente y Origen', {
            'fields': ('cliente', 'cotizacion', 'pago')
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
    def folio_badge(self, obj):
        return format_html(
            '<span style="color:#4CAF50; font-weight:700;">SOL-{:04d}</span>',
            obj.id
        )
    
    @admin.display(description="Cliente", ordering="cliente__nombre")
    def cliente_link(self, obj):
        return format_html(
            '<a href="/admin/comercial/cliente/{}/change/" style="color:#4CAF50;">{}</a><br>'
            '<small style="color:#95a5a6;">{}</small>',
            obj.cliente.id,
            obj.cliente.nombre[:30],
            obj.rfc
        )
    
    @admin.display(description="Monto", ordering="monto")
    def monto_badge(self, obj):
        return format_html(
            '<span style="font-weight:600; color:#d4d1c8;">${:,.2f}</span>',
            obj.monto
        )
    
    @admin.display(description="Fecha", ordering="fecha_solicitud")
    def fecha_solicitud_fmt(self, obj):
        return obj.fecha_solicitud.strftime('%d/%m/%Y')
    
    @admin.display(description="Estado", ordering="estado")
    def estado_badge(self, obj):
        colores = {
            'PENDIENTE': '#e67e22',   # Warning
            'ENVIADA': '#3498db',     # Info
            'FACTURADA': '#27ae60',   # Success
            'CANCELADA': '#95a5a6',   # Neutral
        }
        color = colores.get(obj.estado, '#95a5a6')
        
        icono = ''
        if obj.estado == 'FACTURADA':
            icono = ' ✓'
        elif obj.estado == 'PENDIENTE':
            icono = ' ⏳'
        
        return format_html(
            '<span style="background:{}; color:#fff; padding:4px 12px; '
            'border-radius:12px; font-size:11px; font-weight:600;">{}{}</span>',
            color,
            obj.get_estado_display(),
            icono
        )
    
    @admin.display(description="Acciones")
    def acciones_badge(self, obj):
        if obj.estado == 'FACTURADA':
            # Mostrar link a descargar factura
            if obj.archivo_zip:
                return format_html(
                    '<a href="{}" target="_blank" class="btn btn-sm" '
                    'style="background:#27ae60; color:#fff; padding:4px 8px; '
                    'border-radius:4px; font-size:11px;">📥 ZIP</a>',
                    obj.archivo_zip.url
                )
            elif obj.archivo_pdf:
                return format_html(
                    '<a href="{}" target="_blank" class="btn btn-sm" '
                    'style="background:#27ae60; color:#fff; padding:4px 8px; '
                    'border-radius:4px; font-size:11px;">📄 PDF</a>',
                    obj.archivo_pdf.url
                )
            return format_html('<span style="color:#27ae60;">✓ Facturada</span>')
        
        if obj.estado == 'CANCELADA':
            return format_html('<span style="color:#95a5a6;">—</span>')
        
        # Botones de envío para PENDIENTE y ENVIADA
        whatsapp_url = obj.get_whatsapp_url()
        
        botones = []
        
        # Botón WhatsApp
        if whatsapp_url:
            botones.append(format_html(
                '<a href="{}" target="_blank" class="btn btn-sm" '
                'style="background:#25D366; color:#fff; padding:4px 8px; '
                'border-radius:4px; font-size:11px; margin-right:4px;" '
                'onclick="marcarEnviada({}, \'WHATSAPP\')">📱 WA</a>',
                whatsapp_url,
                obj.id
            ))
        
        # Botón Email
        botones.append(format_html(
            '<a href="/admin/facturacion/solicitudfactura/{}/enviar_email/" '
            'class="btn btn-sm" '
            'style="background:#3498db; color:#fff; padding:4px 8px; '
            'border-radius:4px; font-size:11px;">📧 Email</a>',
            obj.id
        ))
        
        return format_html(''.join([str(b) for b in botones]))
    
    # ─── Custom URLs ──────────────────────────────────────────
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
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
    
    def enviar_email_view(self, request, solicitud_id):
        """Envía la solicitud por email al contador."""
        solicitud = SolicitudFactura.objects.get(pk=solicitud_id)
        contador = ConfiguracionContador.get_activo()
        
        if not contador:
            messages.error(request, "No hay contador configurado. Ve a Configuración del Contador.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))
        
        try:
            asunto = f"Solicitud de Factura SOL-{solicitud.id:04d} | {solicitud.cliente.nombre}"
            cuerpo = solicitud.get_datos_para_contador()
            
            send_mail(
                subject=asunto,
                message=cuerpo,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[contador.email],
                fail_silently=False,
            )
            
            solicitud.marcar_enviada(request.user, 'EMAIL')
            messages.success(request, f"✓ Solicitud enviada por email a {contador.email}")
            
        except Exception as e:
            messages.error(request, f"Error al enviar email: {str(e)}")
        
        return HttpResponseRedirect(
            reverse('admin:facturacion_solicitudfactura_change', args=[solicitud_id])
        )
    
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
    
    @admin.action(description="📧 Marcar como enviadas")
    def marcar_enviadas(self, request, queryset):
        count = 0
        for sol in queryset.filter(estado='PENDIENTE'):
            sol.marcar_enviada(request.user, 'EMAIL')
            count += 1
        self.message_user(request, f"✓ {count} solicitud(es) marcada(s) como enviadas")
    
    @admin.action(description="✗ Cancelar solicitudes")
    def marcar_canceladas(self, request, queryset):
        count = queryset.exclude(estado='FACTURADA').update(estado='CANCELADA')
        self.message_user(request, f"✓ {count} solicitud(es) cancelada(s)")