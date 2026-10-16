from django import forms
from django.contrib import admin
from django.db import models as db_models
from django.db.models import F, OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.descargas import url_descarga

from . import services
from .models import AvisoVencimiento, DocumentoExpediente, RequisitoTramite, Tramite, Vigencia

MEDIA_CONFIG = {
    'css': {'all': ('css/admin_fix.css', 'css/mobile_fix_v4.css')},
    'js': ('js/tabs_fix.js',),
}

TONOS_SEMAFORO = services.TONO_POR_ESTADO

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
    change_list_template = 'cumplimiento/tramite_change_list.html'
    list_display = ['nombre', 'tipo_badge', 'semaforo', 'vence', 'costo_ultimo', 'documento_actual',
                    'requisitos_avance', 'critico_badge', 'acciones_fila']
    list_filter = [('tipo', con_titulo('Tipo')), ('critico', con_titulo('Crítico')), ('activo', con_titulo('Activo'))]
    search_fields = ['nombre', 'emisor']
    autocomplete_fields = ['proveedor']
    inlines = [RequisitoInline]
    columnas_texto = ('nombre',)
    readonly_fields = ['created_by', 'updated_by', 'created_at', 'updated_at']
    fieldsets = [
        (None, {'fields': ['nombre', 'tipo', 'emisor', 'proveedor', 'critico', 'responsable', 'activo']}),
        ('Vigencia y avisos', {'fields': ['periodicidad_meses', 'dias_aviso']}),
        ('Contrato de servicio', {'fields': ['renovacion_automatica', 'dias_preaviso_cancelacion']}),
        ('Otros', {'fields': ['notas', 'created_by', 'updated_by', 'created_at', 'updated_at']}),
    ]

    def get_queryset(self, request):
        ultima = Vigencia.objects.filter(tramite=OuterRef('pk'), estado='OBTENIDA').order_by(
            F('fecha_vencimiento').desc(nulls_first=True))
        return super().get_queryset(request).annotate(
            _vence=Subquery(ultima.values('fecha_vencimiento')[:1])
        ).prefetch_related('vigencias__documentos', 'requisitos')

    def get_ordering(self, request):
        # Críticos primero y, dentro de cada grupo, el vencimiento más cercano:
        # los vencidos quedan arriba. Sin registro (sin fecha) al final.
        # La anotación solo existe en el changelist; en otros usos (autocomplete
        # de FK) se ordena por lo que sí existe en el modelo.
        if request.resolver_match and request.resolver_match.url_name == 'cumplimiento_tramite_changelist':
            return ['-critico', F('_vence').asc(nulls_last=True), 'nombre']
        return ['-critico', 'nombre']

    def _estado(self, obj):
        return services.estado_tramite(obj)

    @admin.display(description='Semáforo')
    def semaforo(self, obj):
        est = self._estado(obj)
        etiqueta = services.ESTADOS_ETIQUETA[est.estado]
        insignia = ui.badge(etiqueta, TONOS_SEMAFORO[est.estado])
        if est.renovacion_en_curso and est.estado != services.EN_TRAMITE:
            return format_html('{} {}', insignia, ui.badge('Renovación en curso', ui.INFO, categoria=True))
        return insignia

    @admin.display(description='Vence')
    def vence(self, obj):
        est = self._estado(obj)
        if not est.vigencia:
            return ui.vacio()
        if not est.vigencia.fecha_vencimiento:
            return 'Sin vencimiento'
        return format_html('{}<br><small>{}</small>', est.vigencia.fecha_vencimiento.strftime('%d/%m/%Y'),
                           services.dias_texto(est.dias))

    @admin.display(description='Último costo')
    def costo_ultimo(self, obj):
        est = self._estado(obj)
        return ui.monto(est.vigencia.costo) if est.vigencia else ui.vacio(numerico=True)

    @admin.display(description='Documento')
    def documento_actual(self, obj):
        vigencia = self._estado(obj).vigencia
        return _boton_descarga(vigencia, 'documento', 'Descargar documento oficial') if vigencia else ui.vacio()

    @admin.display(description='Requisitos')
    def requisitos_avance(self, obj):
        est = self._estado(obj)
        vigencia = est.renovacion_en_curso or est.vigencia
        if not vigencia:
            return ui.vacio()
        cargados, total, _ = services.avance_requisitos(vigencia)
        if not total:
            return ui.vacio()
        return format_html('{} <small>{}/{}</small>', ui.avance(cargados * 100 / total), cargados, total)

    @admin.display(description='Tipo', ordering='tipo')
    def tipo_badge(self, obj):
        return ui.badge(obj.get_tipo_display(), categoria=True)

    @admin.display(description='Crítico', ordering='critico')
    def critico_badge(self, obj):
        return ui.badge('Crítico', ui.ERROR) if obj.critico else ui.vacio()

    @admin.display(description='Acciones')
    def acciones_fila(self, obj):
        return ui.acciones(ui.boton_icono(reverse('admin:cumplimiento_tramite_renovar', args=[obj.pk]),
                                          'rotate', 'Registrar renovación'))

    def get_urls(self):
        propias = [path('<int:tramite_id>/renovar/', self.admin_site.admin_view(self.renovar_view),
                        name='cumplimiento_tramite_renovar')]
        return propias + super().get_urls()

    def renovar_view(self, request, tramite_id):
        """Abre el formulario de la renovación: la que ya está en trámite, o
        un alta de Vigencia prellenada. No guarda nada hasta que se envía."""
        if not request.user.has_perm('cumplimiento.add_vigencia'):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        tramite = get_object_or_404(Tramite.objects.prefetch_related('vigencias'), pk=tramite_id)
        en_curso = services.renovacion_en_curso(tramite)
        if en_curso:
            return redirect(reverse('admin:cumplimiento_vigencia_change', args=[en_curso.pk]))
        from urllib.parse import urlencode
        return redirect(f"{reverse('admin:cumplimiento_vigencia_add')}?{urlencode(services.datos_renovacion(tramite))}")

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
    readonly_fields = ['documento_descarga', 'checklist_requisitos', 'created_by', 'updated_by', 'created_at', 'updated_at']
    formfield_overrides = {db_models.FileField: {'widget': forms.FileInput}}
    fieldsets = [
        (None, {'fields': ['tramite', 'estado', 'folio']}),
        ('Fechas', {'fields': ['fecha_emision', 'fecha_inicio', 'fecha_vencimiento']}),
        ('Requisitos', {'fields': ['checklist_requisitos']}),
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

    @admin.display(description='Requisitos del trámite')
    def checklist_requisitos(self, obj):
        if not obj or not obj.pk:
            return 'Guarda la vigencia para ver los requisitos pendientes.'
        _, total, faltantes = services.avance_requisitos(obj)
        if not total:
            return 'Este trámite no tiene requisitos obligatorios.'
        if not faltantes:
            return ui.badge('Todos los requisitos obligatorios están cargados', ui.EXITO)
        return format_html(
            '<div class="qkt-aviso qkt-aviso--alerta"><span class="qkt-aviso__titulo">Falta subir</span><ul>{}</ul></div>',
            format_html_join('', '<li>{}</li>', ((r.nombre,) for r in faltantes)))

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


@admin.register(AvisoVencimiento)
class AvisoVencimientoAdmin(admin.ModelAdmin):
    """Bitácora de avisos: solo lectura."""
    list_display = ['vigencia', 'umbral', 'canal', 'estado_badge', 'enviado_en']
    list_filter = [('canal', con_titulo('Canal')), ('estado', con_titulo('Estado'))]
    search_fields = ['vigencia__tramite__nombre']

    TONOS = {'ENVIADO': ui.EXITO, 'FALLIDO': ui.ERROR, 'PENDIENTE': ui.ALERTA}

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS, obj.get_estado_display())

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
