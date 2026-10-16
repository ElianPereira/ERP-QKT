from django import forms
from django.contrib import admin
from django.db import models as db_models

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.descargas import url_descarga

from .models import DocumentoExpediente, RequisitoTramite, Tramite, Vigencia

MEDIA_CONFIG = {
    'css': {'all': ('css/admin_fix.css', 'css/mobile_fix_v4.css')},
    'js': ('js/tabs_fix.js',),
}

TONOS_ESTADO = {'EN_TRAMITE': ui.INFO, 'OBTENIDA': ui.EXITO, 'CANCELADA': ui.NEUTRO}


def _boton_descarga(obj, campo, titulo):
    """Ícono de descarga protegida; nunca `archivo.url`."""
    url = url_descarga(obj, campo)
    if not url:
        return ui.vacio()
    return ui.boton_icono(url, 'file-download', titulo, nueva_pestana=True)


class AuditoriaAdminMixin:
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class RequisitoInline(admin.TabularInline):
    model = RequisitoTramite
    extra = 1
    fields = ['orden', 'nombre', 'obligatorio', 'notas', 'activo']
    can_delete = False


@admin.register(Tramite)
class TramiteAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ['nombre', 'tipo_badge', 'emisor', 'critico_badge', 'responsable', 'activo']
    list_filter = [('tipo', con_titulo('Tipo')), ('critico', con_titulo('Crítico')), ('activo', con_titulo('Activo'))]
    search_fields = ['nombre', 'emisor']
    autocomplete_fields = ['proveedor']
    inlines = [RequisitoInline]
    columnas_texto = ('nombre', 'emisor')
    readonly_fields = ['created_by', 'updated_by', 'created_at', 'updated_at']
    fieldsets = [
        (None, {'fields': ['nombre', 'tipo', 'emisor', 'proveedor', 'critico', 'responsable', 'activo']}),
        ('Vigencia y avisos', {'fields': ['periodicidad_meses', 'dias_aviso']}),
        ('Contrato de servicio', {'fields': ['renovacion_automatica', 'dias_preaviso_cancelacion']}),
        ('Otros', {'fields': ['notas', 'created_by', 'updated_by', 'created_at', 'updated_at']}),
    ]

    @admin.display(description='Tipo', ordering='tipo')
    def tipo_badge(self, obj):
        return ui.badge(obj.get_tipo_display(), categoria=True)

    @admin.display(description='Crítico', ordering='critico', boolean=False)
    def critico_badge(self, obj):
        return ui.badge('Crítico', ui.ERROR) if obj.critico else ui.vacio()

    def has_delete_permission(self, request, obj=None):
        return False

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']


class DocumentoExpedienteInline(admin.TabularInline):
    model = DocumentoExpediente
    extra = 1
    fields = ['requisito', 'nombre', 'archivo', 'fecha_documento', 'descarga', 'subido_por', 'activo']
    readonly_fields = ['descarga', 'subido_por']
    can_delete = False
    formfield_overrides = {db_models.FileField: {'widget': forms.FileInput}}

    @admin.display(description='Descarga')
    def descarga(self, obj):
        return _boton_descarga(obj, 'archivo', 'Descargar documento') if obj.pk else ui.vacio()

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'requisito':
            vigencia_id = request.resolver_match.kwargs.get('object_id') if request.resolver_match else None
            vigencia = Vigencia.objects.filter(pk=vigencia_id).first() if vigencia_id else None
            kwargs['queryset'] = RequisitoTramite.objects.filter(
                tramite=vigencia.tramite_id if vigencia else None, activo=True)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Vigencia)
class VigenciaAdmin(AuditoriaAdminMixin, admin.ModelAdmin):
    list_display = ['tramite', 'estado_badge', 'folio', 'fecha_vencimiento', 'costo_display', 'documento_descarga']
    list_filter = [('estado', con_titulo('Estado')), ('tramite', con_titulo('Trámite')),
                   filtro_periodo('fecha_vencimiento', 'Vencimiento')]
    search_fields = ['tramite__nombre', 'folio']
    autocomplete_fields = ['tramite', 'compra']
    date_hierarchy = 'fecha_vencimiento'
    inlines = [DocumentoExpedienteInline]
    readonly_fields = ['documento_descarga', 'created_by', 'updated_by', 'created_at', 'updated_at']
    formfield_overrides = {db_models.FileField: {'widget': forms.FileInput}}
    fieldsets = [
        (None, {'fields': ['tramite', 'estado', 'folio']}),
        ('Fechas', {'fields': ['fecha_emision', 'fecha_inicio', 'fecha_vencimiento']}),
        ('Costo y documento', {'fields': ['costo', 'compra', 'documento', 'documento_descarga']}),
        ('Otros', {'fields': ['notas', 'created_by', 'updated_by', 'created_at', 'updated_at']}),
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('tramite')

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='Costo', ordering='costo')
    def costo_display(self, obj):
        return ui.monto(obj.costo)

    @admin.display(description='Documento actual')
    def documento_descarga(self, obj):
        return _boton_descarga(obj, 'documento', 'Descargar documento oficial') if obj.pk else ui.vacio()

    def save_formset(self, request, form, formset, change):
        instancias = formset.save(commit=False)
        for instancia in instancias:
            if isinstance(instancia, DocumentoExpediente) and not instancia.pk:
                instancia.subido_por = request.user
            instancia.save()
        formset.save_m2m()

    def has_delete_permission(self, request, obj=None):
        return False

    class Media:
        css = MEDIA_CONFIG['css']
        js = MEDIA_CONFIG['js']
