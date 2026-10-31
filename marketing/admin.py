from django.contrib import admin
from django.utils import timezone
from django.utils.formats import date_format

from core_erp import admin_ui as ui
from core_erp.admin_filtros import filtro_periodo

from .models import Lead, LeadEvento


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
