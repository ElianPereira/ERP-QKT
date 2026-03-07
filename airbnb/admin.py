"""
Admin del módulo Airbnb
=======================
Panel de administración para gestión de anuncios, reservas y pagos.
"""
from decimal import Decimal
from django.contrib import admin
from django.utils.html import format_html, mark_safe
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Sum, Count
from django.utils import timezone

from .models import AnuncioAirbnb, ReservaAirbnb, PagoAirbnb, ConflictoCalendario
from .services import SincronizadorAirbnbService, DetectorConflictosService, ImportadorCSVPagosService


# ==========================================
# CONFIGURACIÓN COMÚN
# ==========================================
MEDIA_CONFIG = {
    'css': {'all': ('css/admin_fix.css', 'css/mobile_fix.css')},
    'js': ('js/tabs_fix.js',)
}


# ==========================================
# ANUNCIOS AIRBNB
# ==========================================
@admin.register(AnuncioAirbnb)
class AnuncioAirbnbAdmin(admin.ModelAdmin):
    list_display = (
        'nombre', 
        'tipo_badge', 
        'afecta_quinta_badge',
        'reservas_count',
        'ultima_sync',
        'activo',
        'acciones_btn'
    )
    list_filter = ('tipo', 'afecta_eventos_quinta', 'activo')
    list_editable = ('activo',)
    search_fields = ('nombre', 'airbnb_listing_id')
    readonly_fields = ('airbnb_listing_id', 'ultima_sincronizacion', 'created_at', 'updated_at')
    
    fieldsets = (
        ('Información del Anuncio', {
            'fields': ('nombre', 'tipo', 'url_ical', 'airbnb_listing_id')
        }),
        ('Configuración', {
            'fields': ('afecta_eventos_quinta', 'activo'),
            'description': 'Las habitaciones dentro de la quinta deben tener "Afecta eventos" activo.'
        }),
        ('Sincronización', {
            'fields': ('ultima_sincronizacion',),
            'classes': ('collapse',)
        }),
        ('Auditoría', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<int:object_id>/sincronizar/',
                self.admin_site.admin_view(self.sincronizar_anuncio),
                name='airbnb_anuncioairbnb_sincronizar'
            ),
            path(
                'sincronizar-todos/',
                self.admin_site.admin_view(self.sincronizar_todos),
                name='airbnb_anuncioairbnb_sincronizar_todos'
            ),
        ]
        return custom_urls + urls
    
    def tipo_badge(self, obj):
        color = '#3498db' if obj.tipo == 'HABITACION' else '#27ae60'
        return format_html(
            '<span style="background:{}; color:white; padding:3px 8px; '
            'border-radius:4px; font-size:11px;">{}</span>',
            color, obj.get_tipo_display()
        )
    tipo_badge.short_description = "Tipo"
    
    def afecta_quinta_badge(self, obj):
        if obj.afecta_eventos_quinta:
            return format_html(
                '<span style="color:#e74c3c;">⚠️ Sí</span>'
            )
        return format_html('<span style="color:#95a5a6;">No</span>')
    afecta_quinta_badge.short_description = "Afecta Eventos"
    
    def reservas_count(self, obj):
        count = obj.reservas.filter(estado='CONFIRMADA').count()
        return format_html(
            '<span style="background:#34495e; color:white; padding:2px 8px; '
            'border-radius:10px;">{}</span>',
            count
        )
    reservas_count.short_description = "Reservas"
    
    def ultima_sync(self, obj):
        if obj.ultima_sincronizacion:
            diff = timezone.now() - obj.ultima_sincronizacion
            if diff.days > 0:
                return format_html('<span style="color:#e74c3c;">Hace {} días</span>', diff.days)
            hours = diff.seconds // 3600
            if hours > 0:
                return format_html('<span style="color:#f39c12;">Hace {} hrs</span>', hours)
            return format_html('<span style="color:#27ae60;">Reciente</span>')
        return format_html('<span style="color:#95a5a6;">Nunca</span>')
    ultima_sync.short_description = "Última Sync"
    
    def acciones_btn(self, obj):
        url = reverse('admin:airbnb_anuncioairbnb_sincronizar', args=[obj.pk])
        return format_html(
            '<a href="{}" class="button" style="padding:5px 10px; '
            'background:#3498db; color:white; border-radius:4px; '
            'text-decoration:none;">🔄 Sincronizar</a>',
            url
        )
    acciones_btn.short_description = "Acciones"
    
    def sincronizar_anuncio(self, request, object_id):
        anuncio = AnuncioAirbnb.objects.get(pk=object_id)
        servicio = SincronizadorAirbnbService()
        
        try:
            creadas, actualizadas, errores = servicio.sincronizar_anuncio(anuncio)
            messages.success(
                request, 
                f"✅ Sincronización completada: {creadas} nuevas, {actualizadas} actualizadas, {errores} errores"
            )
            
            # Detectar conflictos
            detector = DetectorConflictosService()
            conflictos = detector.detectar_conflictos()
            if conflictos:
                messages.warning(
                    request,
                    f"⚠️ Se detectaron {len(conflictos)} nuevos conflictos con eventos de la quinta"
                )
        except Exception as e:
            messages.error(request, f"❌ Error: {str(e)}")
        
        return redirect('admin:airbnb_anuncioairbnb_changelist')
    
    def sincronizar_todos(self, request):
        servicio = SincronizadorAirbnbService()
        resultados = servicio.sincronizar_todos()
        
        exitos = sum(1 for r in resultados.values() if r.get('status') == 'ok')
        errores = sum(1 for r in resultados.values() if r.get('status') == 'error')
        
        if exitos > 0:
            messages.success(request, f"✅ {exitos} anuncios sincronizados correctamente")
        if errores > 0:
            messages.error(request, f"❌ {errores} anuncios con errores")
        
        # Detectar conflictos
        detector = DetectorConflictosService()
        conflictos = detector.detectar_conflictos()
        if conflictos:
            messages.warning(
                request,
                f"⚠️ Se detectaron {len(conflictos)} nuevos conflictos"
            )
        
        return redirect('admin:airbnb_anuncioairbnb_changelist')
    
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['show_sync_all_button'] = True
        return super().changelist_view(request, extra_context=extra_context)


# ==========================================
# RESERVAS AIRBNB
# ==========================================
@admin.register(ReservaAirbnb)
class ReservaAirbnbAdmin(admin.ModelAdmin):
    list_display = (
        'anuncio',
        'titulo',
        'fecha_inicio',
        'fecha_fin',
        'noches_badge',
        'estado_badge',
        'origen_badge',
        'tiene_conflicto',
    )
    list_filter = ('anuncio', 'estado', 'origen', 'fecha_inicio')
    search_fields = ('titulo', 'uid_ical', 'notas')
    date_hierarchy = 'fecha_inicio'
    readonly_fields = ('uid_ical', 'created_at', 'updated_at')
    raw_id_fields = ('anuncio',)
    
    fieldsets = (
        ('Reserva', {
            'fields': ('anuncio', 'titulo', 'fecha_inicio', 'fecha_fin', 'estado', 'origen')
        }),
        ('Notas', {
            'fields': ('notas',),
            'classes': ('collapse',)
        }),
        ('Sistema', {
            'fields': ('uid_ical', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    
    def noches_badge(self, obj):
        return format_html(
            '<span style="background:#9b59b6; color:white; padding:2px 8px; '
            'border-radius:10px;">{} noches</span>',
            obj.noches
        )
    noches_badge.short_description = "Duración"
    
    def estado_badge(self, obj):
        colores = {
            'CONFIRMADA': '#27ae60',
            'CANCELADA': '#e74c3c',
            'BLOQUEADA': '#f39c12',
        }
        color = colores.get(obj.estado, '#95a5a6')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 8px; '
            'border-radius:4px; font-size:11px;">{}</span>',
            color, obj.get_estado_display()
        )
    estado_badge.short_description = "Estado"
    
    def origen_badge(self, obj):
        iconos = {
            'AIRBNB': '🏠',
            'MANUAL': '✏️',
            'EVENTO': '🎉',
        }
        return format_html('{} {}', iconos.get(obj.origen, ''), obj.get_origen_display())
    origen_badge.short_description = "Origen"
    
    def tiene_conflicto(self, obj):
        conflictos = obj.conflictos.filter(estado='PENDIENTE').count()
        if conflictos > 0:
            return format_html(
                '<span style="background:#e74c3c; color:white; padding:2px 8px; '
                'border-radius:10px;">⚠️ {}</span>',
                conflictos
            )
        return format_html('<span style="color:#27ae60;">✓</span>')
    tiene_conflicto.short_description = "Conflictos"


# ==========================================
# PAGOS AIRBNB
# ==========================================
@admin.register(PagoAirbnb)
class PagoAirbnbAdmin(admin.ModelAdmin):
    list_display = (
        'codigo_confirmacion',
        'huesped',
        'anuncio',
        'fecha_checkin',
        'noches_badge',
        'monto_bruto_fmt',
        'retenciones_fmt',
        'monto_neto_fmt',
        'estado_badge',
    )
    list_filter = ('estado', 'anuncio', 'fecha_checkin')
    search_fields = ('codigo_confirmacion', 'huesped')
    date_hierarchy = 'fecha_checkin'
    readonly_fields = (
        'retencion_isr', 
        'retencion_iva', 
        'archivo_csv_origen',
        'created_by',
        'created_at', 
        'updated_at'
    )
    raw_id_fields = ('anuncio', 'reserva')
    
    fieldsets = (
        ('Reserva', {
            'fields': ('anuncio', 'reserva', 'codigo_confirmacion', 'huesped')
        }),
        ('Fechas', {
            'fields': ('fecha_checkin', 'fecha_checkout', 'fecha_pago')
        }),
        ('Montos', {
            'fields': ('monto_bruto', 'comision_airbnb', 'retencion_isr', 'retencion_iva', 'monto_neto'),
            'description': 'Las retenciones se calculan automáticamente (ISR 4%, IVA 8%)'
        }),
        ('Estado', {
            'fields': ('estado', 'notas')
        }),
        ('Auditoría', {
            'fields': ('archivo_csv_origen', 'created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'importar-csv/',
                self.admin_site.admin_view(self.importar_csv_view),
                name='airbnb_pagoairbnb_importar_csv'
            ),
        ]
        return custom_urls + urls
    
    def noches_badge(self, obj):
        return format_html(
            '<span style="background:#9b59b6; color:white; padding:2px 6px; '
            'border-radius:10px; font-size:11px;">{}</span>',
            obj.noches
        )
    noches_badge.short_description = "Noches"
    
    def monto_bruto_fmt(self, obj):
        return format_html('<strong>${:,.2f}</strong>', obj.monto_bruto)
    monto_bruto_fmt.short_description = "Bruto"
    
    def retenciones_fmt(self, obj):
        total_ret = obj.retencion_isr + obj.retencion_iva + obj.comision_airbnb
        return format_html(
            '<span style="color:#e74c3c;">-${:,.2f}</span>',
            total_ret
        )
    retenciones_fmt.short_description = "Retenciones"
    
    def monto_neto_fmt(self, obj):
        return format_html(
            '<span style="color:#27ae60; font-weight:bold;">${:,.2f}</span>',
            obj.monto_neto
        )
    monto_neto_fmt.short_description = "Neto"
    
    def estado_badge(self, obj):
        colores = {
            'PENDIENTE': '#f39c12',
            'PAGADO': '#27ae60',
            'CANCELADO': '#e74c3c',
        }
        color = colores.get(obj.estado, '#95a5a6')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 8px; '
            'border-radius:4px; font-size:11px;">{}</span>',
            color, obj.get_estado_display()
        )
    estado_badge.short_description = "Estado"
    
    def importar_csv_view(self, request):
        if request.method == 'POST':
            archivo = request.FILES.get('archivo_csv')
            if not archivo:
                messages.error(request, "❌ Debes seleccionar un archivo CSV")
                return redirect('admin:airbnb_pagoairbnb_changelist')
            
            try:
                contenido = archivo.read().decode('utf-8')
            except UnicodeDecodeError:
                try:
                    archivo.seek(0)
                    contenido = archivo.read().decode('latin-1')
                except:
                    messages.error(request, "❌ No se pudo leer el archivo. Verifica la codificación.")
                    return redirect('admin:airbnb_pagoairbnb_changelist')
            
            importador = ImportadorCSVPagosService(archivo_nombre=archivo.name)
            importados, duplicados, errores = importador.importar(contenido, usuario=request.user)
            
            if importados > 0:
                messages.success(request, f"✅ {importados} pagos importados correctamente")
            if duplicados > 0:
                messages.warning(request, f"⚠️ {duplicados} pagos ya existían (omitidos)")
            if errores:
                for error in errores[:5]:  # Mostrar máximo 5 errores
                    messages.error(request, f"❌ {error}")
            
            return redirect('admin:airbnb_pagoairbnb_changelist')
        
        # GET: Mostrar formulario
        context = {
            **self.admin_site.each_context(request),
            'title': 'Importar Pagos desde CSV de Airbnb',
            'opts': self.model._meta,
        }
        return render(request, 'admin/airbnb/importar_csv.html', context)
    
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['show_import_button'] = True
        
        # Calcular totales del mes
        hoy = timezone.now()
        pagos_mes = PagoAirbnb.objects.filter(
            fecha_checkin__year=hoy.year,
            fecha_checkin__month=hoy.month,
            estado='PAGADO'
        ).aggregate(
            total_bruto=Sum('monto_bruto'),
            total_neto=Sum('monto_neto'),
            total_isr=Sum('retencion_isr'),
            total_iva=Sum('retencion_iva'),
        )
        extra_context['totales_mes'] = pagos_mes
        
        return super().changelist_view(request, extra_context=extra_context)


# ==========================================
# CONFLICTOS
# ==========================================
@admin.register(ConflictoCalendario)
class ConflictoCalendarioAdmin(admin.ModelAdmin):
    list_display = (
        'fecha_conflicto',
        'reserva_info',
        'evento_info',
        'estado_badge',
        'acciones_btn',
    )
    list_filter = ('estado', 'fecha_conflicto')
    search_fields = (
        'reserva_airbnb__anuncio__nombre',
        'cotizacion__nombre_evento',
        'cotizacion__cliente__nombre'
    )
    date_hierarchy = 'fecha_conflicto'
    readonly_fields = ('reserva_airbnb', 'cotizacion', 'fecha_conflicto', 'descripcion', 'created_at')
    raw_id_fields = ('resuelto_por',)
    
    fieldsets = (
        ('Conflicto', {
            'fields': ('fecha_conflicto', 'reserva_airbnb', 'cotizacion', 'descripcion')
        }),
        ('Resolución', {
            'fields': ('estado', 'resuelto_por', 'fecha_resolucion', 'notas_resolucion')
        }),
    )
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    
    def reserva_info(self, obj):
        return format_html(
            '<strong>{}</strong><br>'
            '<small style="color:#666;">{} → {}</small>',
            obj.reserva_airbnb.anuncio.nombre,
            obj.reserva_airbnb.fecha_inicio.strftime('%d/%m'),
            obj.reserva_airbnb.fecha_fin.strftime('%d/%m')
        )
    reserva_info.short_description = "Reserva Airbnb"
    
    def evento_info(self, obj):
        return format_html(
            '<strong>{}</strong><br>'
            '<small style="color:#666;">{}</small>',
            obj.cotizacion.nombre_evento[:30],
            obj.cotizacion.cliente.nombre
        )
    evento_info.short_description = "Evento Quinta"
    
    def estado_badge(self, obj):
        colores = {
            'PENDIENTE': '#e74c3c',
            'RESUELTO': '#27ae60',
            'IGNORADO': '#95a5a6',
        }
        color = colores.get(obj.estado, '#95a5a6')
        return format_html(
            '<span style="background:{}; color:white; padding:3px 8px; '
            'border-radius:4px; font-size:11px;">{}</span>',
            color, obj.get_estado_display()
        )
    estado_badge.short_description = "Estado"
    
    def acciones_btn(self, obj):
        if obj.estado == 'PENDIENTE':
            return format_html(
                '<a href="{}" class="button" style="padding:3px 8px; '
                'background:#27ae60; color:white; border-radius:4px; '
                'text-decoration:none; font-size:11px;">Resolver</a>',
                reverse('admin:airbnb_conflictocalendario_change', args=[obj.pk])
            )
        return '-'
    acciones_btn.short_description = "Acción"
    
    def save_model(self, request, obj, form, change):
        if obj.estado == 'RESUELTO' and not obj.resuelto_por:
            obj.resuelto_por = request.user
            obj.fecha_resolucion = timezone.now()
        super().save_model(request, obj, form, change)
