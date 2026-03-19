"""
Admin del Módulo de Facturación
===============================
Versión simplificada compatible con Django 6.x + Jazzmin
"""
from django.contrib import admin
from django.urls import path, reverse
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings

from .models import SolicitudFactura, ConfiguracionContador


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
        'id',
        'cliente',
        'monto',
        'forma_pago',
        'fecha_solicitud',
        'estado',
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
        ('Cliente y Origen', {
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
            messages.error(request, "No hay contador configurado.")
            return HttpResponseRedirect(reverse('admin:facturacion_solicitudfactura_changelist'))
        
        try:
            asunto = f"Solicitud de Factura #{solicitud.id} | {solicitud.cliente.nombre}"
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
        """Marca la solicitud como enviada."""
        solicitud = SolicitudFactura.objects.get(pk=solicitud_id)
        metodo = request.GET.get('metodo', 'WHATSAPP')
        solicitud.marcar_enviada(request.user, metodo)
        return JsonResponse({'status': 'ok'})
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
    
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