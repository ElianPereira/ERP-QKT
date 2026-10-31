from django.contrib import admin
from django.utils import timezone
from django.utils.formats import date_format

from core_erp import admin_ui as ui
from core_erp.admin_filtros import filtro_periodo

from .models import Anuncio, Campana, GastoPublicitario, Lead, LeadEvento, ParametroMarketing


class SoloSuperusuarioMixin:
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)


class LeadEventoInline(admin.TabularInline):
    model = LeadEvento
    extra = 0
    can_delete = False
    fields = ('created_at', 'tipo', 'etapa_anterior', 'etapa_nueva', 'origen', 'detalle', 'created_by')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Lead)
class LeadAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Embudo de prospectos. La etapa la deriva el sistema; solo el motivo de
    pérdida se corrige a mano (y queda en la bitácora)."""
    list_display = ('prospecto', 'etapa_badge', 'fuente_badge', 'tipo_servicio', 'cotizacion',
                    'primer_contacto', 'ultima_interaccion', 'motivo')
    list_filter = ('etapa', 'fuente', 'tipo_servicio', filtro_periodo('fecha_primer_contacto', 'Primer contacto'))
    search_fields = ('nombre', 'telefono', 'anuncio_titulo')
    list_select_related = ('cotizacion',)
    columnas_texto = ('prospecto',)
    inlines = [LeadEventoInline]
    fields = ('nombre', 'telefono', 'etapa', 'fuente', 'anuncio_id', 'anuncio_titulo', 'tipo_servicio',
              'conversacion', 'cliente', 'cotizacion', 'fecha_primer_contacto', 'fecha_ultima_interaccion',
              'etapa_cambiada_en', 'anonimizado_en', 'motivo_perdida', 'motivo_perdida_origen')
    readonly_fields = tuple(f for f in fields if f not in ('motivo_perdida',))

    TONOS_ETAPA = {'NUEVO': ui.NEUTRO, 'CONTACTADO': ui.INFO, 'VISITA_AGENDADA': ui.INFO,
                   'COTIZADO': ui.ALERTA, 'GANADO': ui.EXITO, 'PERDIDO': ui.ERROR}

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.etapa != Lead.PERDIDO:
            return self.fields
        return self.readonly_fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    @admin.display(description='Prospecto', ordering='nombre')
    def prospecto(self, obj):
        return obj.nombre or ('Anonimizado' if obj.anonimizado_en else f'Prospecto {obj.pk}')

    @admin.display(description='Etapa', ordering='etapa')
    def etapa_badge(self, obj):
        return ui.badge_por_valor(obj.etapa, self.TONOS_ETAPA, obj.get_etapa_display())

    @admin.display(description='Fuente', ordering='fuente')
    def fuente_badge(self, obj):
        return ui.badge(obj.get_fuente_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Primer contacto', ordering='fecha_primer_contacto')
    def primer_contacto(self, obj):
        return date_format(timezone.localtime(obj.fecha_primer_contacto), 'd M Y')

    @admin.display(description='Última interacción', ordering='fecha_ultima_interaccion')
    def ultima_interaccion(self, obj):
        return date_format(timezone.localtime(obj.fecha_ultima_interaccion), 'd M Y')

    @admin.display(description='Motivo', ordering='motivo_perdida')
    def motivo(self, obj):
        return obj.get_motivo_perdida_display() if obj.motivo_perdida else ui.vacio()

    def save_model(self, request, obj, form, change):
        if 'motivo_perdida' in form.changed_data:
            anterior = form.initial.get('motivo_perdida', '')
            obj.motivo_perdida_origen = 'USUARIO' if obj.motivo_perdida else ''
            obj.updated_by = request.user
            super().save_model(request, obj, form, change)
            LeadEvento.objects.create(
                lead=obj, tipo='MOTIVO', etapa_anterior=obj.etapa, etapa_nueva=obj.etapa, origen='USUARIO',
                detalle={'antes': anterior, 'despues': obj.motivo_perdida}, created_by=request.user)
        else:
            super().save_model(request, obj, form, change)


class SoloLecturaPlataformaMixin(SoloSuperusuarioMixin):
    """Lo que viene de la plataforma no se captura a mano: solo se corrige la atribución."""

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Campana)
class CampanaAdmin(SoloLecturaPlataformaMixin, admin.ModelAdmin):
    list_display = ('nombre', 'plataforma_badge', 'estado', 'objetivo', 'tipo_servicio', 'presupuesto')
    list_filter = ('plataforma', 'objetivo', 'tipo_servicio')
    search_fields = ('nombre', 'id_externo')
    columnas_texto = ('nombre',)
    fields = ('nombre', 'plataforma', 'id_externo', 'estado', 'objetivo_plataforma', 'presupuesto_diario',
              'objetivo', 'tipo_servicio')
    readonly_fields = ('nombre', 'plataforma', 'id_externo', 'estado', 'objetivo_plataforma', 'presupuesto_diario')

    @admin.display(description='Plataforma', ordering='plataforma')
    def plataforma_badge(self, obj):
        return ui.badge(obj.get_plataforma_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Presupuesto diario', ordering='presupuesto_diario')
    def presupuesto(self, obj):
        return ui.monto(obj.presupuesto_diario)


@admin.register(Anuncio)
class AnuncioAdmin(SoloLecturaPlataformaMixin, admin.ModelAdmin):
    list_display = ('nombre', 'campana', 'estado', 'prospectos_ligados')
    list_filter = ('campana__plataforma', 'estado')
    search_fields = ('nombre', 'id_externo', 'campana__nombre')
    list_select_related = ('campana',)
    columnas_texto = ('nombre', 'campana')
    fields = ('nombre', 'campana', 'id_externo', 'estado')
    readonly_fields = fields

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description='Prospectos')
    def prospectos_ligados(self, obj):
        return obj.prospectos.count()


@admin.register(GastoPublicitario)
class GastoPublicitarioAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Pauta diaria. Lo de la API de Meta es de solo lectura; solo se capturan a
    mano los gastos de fuente MANUAL (p. ej. un cargo que la API no reporta)."""
    list_display = ('fecha', 'campana', 'anuncio', 'gasto', 'impresiones', 'clics', 'fuente_badge',
                    'conciliacion_badge')
    list_filter = ('fuente', 'estado_conciliacion', filtro_periodo('fecha', 'Fecha'), 'campana__plataforma')
    search_fields = ('campana__nombre', 'anuncio__nombre')
    list_select_related = ('campana', 'anuncio')
    columnas_texto = ('campana', 'anuncio')
    fields = ('fecha', 'campana', 'anuncio', 'monto', 'impresiones', 'clics', 'frecuencia', 'fuente',
              'estado_conciliacion')
    TONOS_CONCILIACION = {'CONCILIADO': ui.EXITO, 'DIFERENCIA': ui.ERROR, 'PENDIENTE': ui.NEUTRO}

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ('fuente', 'estado_conciliacion')
        return self.fields if obj.fuente != GastoPublicitario.MANUAL else ('fuente', 'estado_conciliacion')

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.fuente = GastoPublicitario.MANUAL
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='Gasto', ordering='monto')
    def gasto(self, obj):
        return ui.monto(obj.monto)

    @admin.display(description='Fuente', ordering='fuente')
    def fuente_badge(self, obj):
        return ui.badge(obj.get_fuente_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Conciliación', ordering='estado_conciliacion')
    def conciliacion_badge(self, obj):
        return ui.badge_por_valor(obj.estado_conciliacion, self.TONOS_CONCILIACION,
                                  obj.get_estado_conciliacion_display())


@admin.register(ParametroMarketing)
class ParametroMarketingAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Supuestos de marketing. Oculto del menú: se abre desde la pantalla «Marketing». Un juego activo a la
    vez; guardar uno nuevo activo desactiva el anterior (queda el historial). Nada se borra."""
    list_display = ('vigencia', 'estado', 'reinversion', 'muestra', 'ventana', 'actualizado')
    list_filter = ('activo',)
    fieldsets = (
        ('Cuánto invertir', {'fields': ('pct_reinversion', 'cac_tope_manual')}),
        ('Cuándo confiar en una cifra', {'fields': ('muestra_minima_leads', 'ventana_dias', 'ciclo_dias_evento',
                                                     'ciclo_dias_pasadia', 'ciclo_dias_hospedaje')}),
        ('Prospectos y pronóstico', {'fields': ('dias_sin_respuesta', 'prob_etapa')}),
        ('Límites para las decisiones', {'fields': ('cpl_max', 'frecuencia_max', 'umbral_ocupacion_pausa')}),
        ('Vigencia', {'fields': ('vigente_desde', 'activo')}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at')}),
    )
    readonly_fields = ('created_by', 'created_at', 'updated_by', 'updated_at')

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description='Vigente desde', ordering='vigente_desde')
    def vigencia(self, obj):
        return date_format(obj.vigente_desde, 'd M Y')

    @admin.display(description='Estado', ordering='activo')
    def estado(self, obj):
        return ui.badge('Activo', ui.EXITO) if obj.activo else ui.badge('Anterior', ui.NEUTRO)

    @admin.display(description='Reinversión')
    def reinversion(self, obj):
        return f'{obj.pct_reinversion} %'

    @admin.display(description='Muestra mínima')
    def muestra(self, obj):
        return obj.muestra_minima_leads

    @admin.display(description='Ventana')
    def ventana(self, obj):
        return f'{obj.ventana_dias} días'

    @admin.display(description='Actualizado', ordering='updated_at')
    def actualizado(self, obj):
        return date_format(timezone.localtime(obj.updated_at), 'd M Y')
