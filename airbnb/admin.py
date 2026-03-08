"""
Admin del módulo Airbnb
=======================
Panel de administración para gestión de anuncios, reservas y pagos.
Compatible con Django 6.0+
"""
from decimal import Decimal
from django.contrib import admin
from django.urls import path, reverse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.db.models import Sum, Count
from django.utils import timezone
from django.http import HttpResponseRedirect

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
        'tipo',
        'afecta_eventos_quinta',
        'ultima_sincronizacion',
        'activo',
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
            'description': 'Las habitaciones dentro de la quinta deben tener "Afecta eventos" activo para bloquear fechas.'
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
    
    actions = ['sincronizar_seleccionados']
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'sincronizar-todos/',
                self.admin_site.admin_view(self.sincronizar_todos),
                name='airbnb_anuncioairbnb_sincronizar_todos'
            ),
        ]
        return custom_urls + urls
    
    @admin.action(description="🔄 Sincronizar anuncios seleccionados")
    def sincronizar_seleccionados(self, request, queryset):
        servicio = SincronizadorAirbnbService()
        total_creadas = 0
        total_actualizadas = 0
        
        for anuncio in queryset:
            try:
                creadas, actualizadas, errores = servicio.sincronizar_anuncio(anuncio)
                total_creadas += creadas
                total_actualizadas += actualizadas
            except Exception as e:
                messages.error(request, f"Error en {anuncio.nombre}: {str(e)}")
        
        # Detectar conflictos
        detector = DetectorConflictosService()
        conflictos = detector.detectar_conflictos()
        
        messages.success(request, f"✅ Sincronización: {total_creadas} nuevas, {total_actualizadas} actualizadas")
        if conflictos:
            messages.warning(request, f"⚠️ {len(conflictos)} nuevos conflictos detectados")
    
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
            messages.warning(request, f"⚠️ {len(conflictos)} nuevos conflictos detectados")
        
        return redirect('admin:airbnb_anuncioairbnb_changelist')
    
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['title'] = 'Anuncios Airbnb'
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
        'estado',
        'origen',
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
        'fecha_checkout',
        'monto_bruto',
        'monto_neto',
        'estado',
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
                for error in errores[:5]:
                    messages.error(request, f"❌ {error}")
            
            return redirect('admin:airbnb_pagoairbnb_changelist')
        
        context = {
            **self.admin_site.each_context(request),
            'title': 'Importar Pagos desde CSV de Airbnb',
            'opts': self.model._meta,
        }
        return render(request, 'admin/airbnb/importar_csv.html', context)
    
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['show_import_button'] = True
        extra_context['title'] = 'Pagos Airbnb'
        return super().changelist_view(request, extra_context=extra_context)


# ==========================================
# CONFLICTOS
# ==========================================
@admin.register(ConflictoCalendario)
class ConflictoCalendarioAdmin(admin.ModelAdmin):
    list_display = (
        'fecha_conflicto',
        'reserva_airbnb',
        'cotizacion',
        'estado',
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
    
    actions = ['marcar_resuelto', 'marcar_ignorado']
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    
    @admin.action(description="✅ Marcar como resuelto")
    def marcar_resuelto(self, request, queryset):
        queryset.update(
            estado='RESUELTO',
            resuelto_por=request.user,
            fecha_resolucion=timezone.now()
        )
        messages.success(request, f"✅ {queryset.count()} conflictos marcados como resueltos")
    
    @admin.action(description="⏭️ Marcar como ignorado")
    def marcar_ignorado(self, request, queryset):
        queryset.update(estado='IGNORADO')
        messages.success(request, f"⏭️ {queryset.count()} conflictos ignorados")
    
    def save_model(self, request, obj, form, change):
        if obj.estado == 'RESUELTO' and not obj.resuelto_por:
            obj.resuelto_por = request.user
            obj.fecha_resolucion = timezone.now()
        super().save_model(request, obj, form, change)
