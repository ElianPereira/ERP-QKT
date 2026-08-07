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
from django.utils.html import format_html

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
    
    @admin.action(description="Sincronizar seleccionados")
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
        
        messages.success(request, f"Sincronización: {total_creadas} nuevas, {total_actualizadas} actualizadas")
        if conflictos:
            messages.warning(request, f" {len(conflictos)} nuevos conflictos detectados")
    
    def sincronizar_todos(self, request):
        servicio = SincronizadorAirbnbService()
        resultados = servicio.sincronizar_todos()
        
        exitos = sum(1 for r in resultados.values() if r.get('status') == 'ok')
        errores = sum(1 for r in resultados.values() if r.get('status') == 'error')
        
        if exitos > 0:
            messages.success(request, f" {exitos} anuncios sincronizados correctamente")
        if errores > 0:
            messages.error(request, f" {errores} anuncios con errores")
        
        # Detectar conflictos
        detector = DetectorConflictosService()
        conflictos = detector.detectar_conflictos()
        if conflictos:
            messages.warning(request, f" {len(conflictos)} nuevos conflictos detectados")
        
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
    change_list_template = 'admin/airbnb/pagoairbnb/change_list.html'

    list_display = (
        'codigo_confirmacion',
        'huesped',
        'anuncio',
        'fecha_checkin',
        'fecha_pago',
        'monto_bruto',
        'monto_neto',
        'estado',
        'cuadra_display',
    )
    list_filter = ('estado', 'anuncio', 'origen', 'fecha_pago')
    search_fields = ('codigo_confirmacion', 'huesped', 'payout_id')
    # El período fiscal lo marca la fecha de pago, no el check-in.
    date_hierarchy = 'fecha_pago'
    list_select_related = ('anuncio',)
    readonly_fields = (
        'archivo_csv_origen',
        'created_by',
        'created_at',
        'updated_at',
        'retenciones_esperadas_display',
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
            'fields': ('monto_bruto', 'comision_airbnb', 'retencion_isr',
                       'retencion_iva', 'impuesto_hospedaje', 'monto_neto',
                       'retenciones_esperadas_display'),
            'description': (
                'Las retenciones son las que Airbnb aplicó realmente, tal como '
                'vienen en el CSV. Debajo se muestra lo que la ley haría esperar, '
                'solo como contraste — lo que se declara es la constancia de la '
                'plataforma, no este cálculo.'
            ),
        }),
        ('Estado', {
            'fields': ('estado', 'origen', 'notas')
        }),
        ('Auditoría', {
            'fields': ('payout_id', 'archivo_csv_origen', 'created_by',
                       'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
    
    @admin.display(description='Cuadra', boolean=True)
    def cuadra_display(self, obj):
        return obj.cuadra

    @admin.display(description='Retenciones esperadas por la ley')
    def retenciones_esperadas_display(self, obj):
        if not obj.pk:
            return '—'
        r = obj.retenciones_esperadas()
        diferencia = obj.diferencia_neto
        aviso = ''
        if not obj.cuadra:
            aviso = format_html(
                '<br><span style="color:#c62828;font-weight:700;">'
                'El neto difiere ${} de sus componentes.</span>', diferencia)
        return format_html(
            'Base ${} · IVA trasladado ${} · ISR esperado ${} · IVA retenido esperado ${}{}',
            r['base'], r['iva_trasladado'], r['ret_isr'], r['ret_iva'], aviso)

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
    
    # Un CSV de un año de operación no llega a 1 MB. El tope evita que una
    # subida equivocada (un ZIP, un video) se lea entera en memoria.
    TAMANO_MAXIMO_CSV = 5 * 1024 * 1024

    def importar_csv_view(self, request):
        if request.method != 'POST':
            context = {
                **self.admin_site.each_context(request),
                'title': 'Importar Pagos desde CSV de Airbnb',
                'opts': self.model._meta,
            }
            return render(request, 'admin/airbnb/importar_csv.html', context)

        archivo = request.FILES.get('archivo_csv')
        if not archivo:
            messages.error(request, "Debes seleccionar un archivo CSV")
            return redirect('admin:airbnb_pagoairbnb_changelist')

        if archivo.size > self.TAMANO_MAXIMO_CSV:
            messages.error(
                request,
                f"El archivo pesa {archivo.size / 1024 / 1024:.1f} MB y el máximo "
                f"son {self.TAMANO_MAXIMO_CSV // 1024 // 1024} MB. "
                "¿Seguro que es el CSV de transacciones de Airbnb?",
            )
            return redirect('admin:airbnb_pagoairbnb_changelist')

        if not archivo.name.lower().endswith('.csv'):
            messages.error(request, "El archivo debe tener extensión .csv")
            return redirect('admin:airbnb_pagoairbnb_changelist')

        contenido = None
        for codificacion in ('utf-8', 'latin-1'):
            try:
                archivo.seek(0)
                contenido = archivo.read().decode(codificacion)
                break
            except UnicodeDecodeError:
                continue
        if contenido is None:
            messages.error(request, "No se pudo leer el archivo. Verifica la codificación.")
            return redirect('admin:airbnb_pagoairbnb_changelist')

        # La vista previa corre la importación completa dentro de una
        # transacción que se revierte: el resumen es exacto, no una estimación.
        simular = bool(request.POST.get('previsualizar'))
        importador = ImportadorCSVPagosService(archivo_nombre=archivo.name)
        resumen = importador.importar(contenido, usuario=request.user, simular=simular)

        if simular:
            context = {
                **self.admin_site.each_context(request),
                'title': 'Vista previa de la importación',
                'opts': self.model._meta,
                'resumen': resumen,
                'archivo_nombre': archivo.name,
            }
            return render(request, 'admin/airbnb/importar_csv.html', context)

        self._reportar(request, resumen)
        return redirect('admin:airbnb_pagoairbnb_changelist')

    @staticmethod
    def _reportar(request, resumen):
        if resumen['creados']:
            messages.success(request, f"{len(resumen['creados'])} pagos nuevos importados.")
        if resumen['actualizados']:
            messages.success(
                request,
                f"{len(resumen['actualizados'])} pagos actualizados con los datos más recientes.",
            )
        if resumen['sin_cambios']:
            messages.info(request, f"{len(resumen['sin_cambios'])} pagos ya estaban al día.")
        for codigo, diferencia in resumen['descuadrados']:
            messages.warning(
                request,
                f"{codigo}: el neto difiere ${diferencia} de sus componentes. "
                "Revísalo antes de declararlo.",
            )
        # Se dice el total, no solo los primeros: saber que hubo 40 errores y
        # ver 5 es distinto de creer que hubo 5.
        for error in resumen['errores'][:5]:
            messages.error(request, error)
        if len(resumen['errores']) > 5:
            messages.error(
                request,
                f"… y {len(resumen['errores']) - 5} errores más "
                f"({len(resumen['errores'])} en total).",
            )
    
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['show_import_button'] = True
        extra_context['title'] = 'Pagos Airbnb'
        extra_context['reporte_fiscal_url'] = '/admin/airbnb/reporte-fiscal/'
        hoy = timezone.localdate()
        extra_context['mes_actual'] = hoy.month
        extra_context['anio_actual'] = hoy.year
        extra_context['meses'] = [
            (1,'Enero'),(2,'Febrero'),(3,'Marzo'),(4,'Abril'),
            (5,'Mayo'),(6,'Junio'),(7,'Julio'),(8,'Agosto'),
            (9,'Septiembre'),(10,'Octubre'),(11,'Noviembre'),(12,'Diciembre')
        ]
        extra_context['anios'] = list(range(hoy.year - 2, hoy.year + 1))
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
    
    @admin.action(description="Marcar como resuelto")
    def marcar_resuelto(self, request, queryset):
        queryset.update(
            estado='RESUELTO',
            resuelto_por=request.user,
            fecha_resolucion=timezone.now()
        )
        messages.success(request, f"{queryset.count()} conflictos marcados como resueltos")
    
    @admin.action(description="Marcar como ignorado")
    def marcar_ignorado(self, request, queryset):
        queryset.update(estado='IGNORADO')
        messages.success(request, f"{queryset.count()} conflictos ignorados")
    
    def save_model(self, request, obj, form, change):
        if obj.estado == 'RESUELTO' and not obj.resuelto_por:
            obj.resuelto_por = request.user
            obj.fecha_resolucion = timezone.now()
        super().save_model(request, obj, form, change)
