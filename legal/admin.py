from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.admin_utils import confirmar_accion_destructiva

from .models import (
    AceptacionLegal,
    DocumentoLegal,
    EstadoARCO,
    Finalidad,
    SolicitudARCO,
)

TONO_ESTADO_ARCO = {
    EstadoARCO.RECIBIDA: ui.INFO,
    EstadoARCO.EN_TRAMITE: ui.ALERTA,
    EstadoARCO.PREVENCION: ui.ALERTA,
    EstadoARCO.PROCEDENTE: ui.EXITO,
    EstadoARCO.IMPROCEDENTE: ui.NEUTRO,
}
# Con respuesta dada, el plazo de 20 días hábiles ya se cumplió: la cuenta
# regresiva deja de ser una alerta.
ESTADOS_ARCO_RESPONDIDOS = (EstadoARCO.PROCEDENTE, EstadoARCO.IMPROCEDENTE)


@admin.register(DocumentoLegal)
class DocumentoLegalAdmin(admin.ModelAdmin):
    list_display = ('tipo_display', 'version', 'vigente_display', 'vigente_desde_display', 'hash_corto_display')
    list_filter = ('tipo', ('vigente', con_titulo('Vigente')))
    search_fields = ('titulo', 'version')
    actions = ['publicar_version']

    @admin.display(description='Documento', ordering='tipo')
    def tipo_display(self, obj):
        return obj.get_tipo_display()

    @admin.display(description='Estado', ordering='vigente')
    def vigente_display(self, obj):
        return ui.badge('Vigente', ui.EXITO) if obj.vigente else ui.badge('Histórica', ui.NEUTRO)

    @admin.display(description='Vigente desde', ordering='vigente_desde')
    def vigente_desde_display(self, obj):
        return date_format(obj.vigente_desde, 'd M Y') if obj.vigente_desde else ui.vacio()

    @admin.display(description='SHA-256')
    def hash_corto_display(self, obj):
        return format_html('<span class="qkt-codigo">{}</span>', obj.hash_corto)

    def get_readonly_fields(self, request, obj=None):
        # El contenido es inmutable una vez creado el documento.
        if obj:
            return ('tipo', 'version', 'contenido_md', 'hash_contenido', 'creado_en',
                    'creado_por')
        return ('hash_contenido', 'creado_en', 'creado_por')

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.creado_por = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Publicar esta versión (desmarca la anterior)")
    @confirmar_accion_destructiva(
        "¿Publicar esta versión? Queda vigente de inmediato en la página "
        "pública correspondiente, y desmarca la versión anterior."
    )
    def publicar_version(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Selecciona exactamente un documento.",
                              level=messages.ERROR)
            return
        doc = queryset.first()
        pendientes = doc.marcadores_pendientes()
        if pendientes:
            self.message_user(
                request,
                f"No se puede publicar: quedan {len(pendientes)} marcadores "
                "[CONFIRMAR:]/[PENDIENTE:] sin resolver en el contenido.",
                level=messages.ERROR,
            )
            return
        doc.vigente = True
        doc.save()
        self.message_user(request, f"Publicado: {doc}", level=messages.SUCCESS)


@admin.register(Finalidad)
class FinalidadAdmin(admin.ModelAdmin):
    list_display = ('clave_display', 'nombre', 'consentimiento_display', 'activa_display', 'orden')
    list_filter = (('requiere_consentimiento', con_titulo('Requiere consentimiento')),
                   ('activa', con_titulo('Activa')))

    @admin.display(description='Clave', ordering='clave')
    def clave_display(self, obj):
        return format_html('<span class="qkt-codigo">{}</span>', obj.clave)

    @admin.display(description='Consentimiento', ordering='requiere_consentimiento')
    def consentimiento_display(self, obj):
        if obj.requiere_consentimiento:
            return ui.badge('Requiere', ui.INFO, categoria=True)
        return ui.badge('No requiere', ui.NEUTRO, categoria=True)

    @admin.display(description='Estado', ordering='activa')
    def activa_display(self, obj):
        return ui.badge('Activa', ui.EXITO) if obj.activa else ui.badge('Inactiva', ui.NEUTRO)


@admin.register(AceptacionLegal)
class AceptacionLegalAdmin(admin.ModelAdmin):
    """Evidencia: solo lectura total."""
    list_display = ('id', 'correo', 'cliente', 'origen_display', 'aceptado_en_display')
    list_filter = ('origen', filtro_periodo('aceptado_en', 'Fecha'))
    search_fields = ('correo',)
    list_select_related = ('cliente',)
    date_hierarchy = 'aceptado_en'

    @admin.display(description='Origen', ordering='origen')
    def origen_display(self, obj):
        return ui.badge(obj.get_origen_display(), ui.INFO, categoria=True)

    @admin.display(description='Aceptado', ordering='aceptado_en')
    def aceptado_en_display(self, obj):
        return date_format(timezone.localtime(obj.aceptado_en), 'd M Y H:i')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields] + ['documentos']


@admin.register(SolicitudARCO)
class SolicitudARCOAdmin(admin.ModelAdmin):
    list_display = ('folio_display', 'tipo_display', 'titular_nombre', 'estado_display',
                    'fecha_limite_display', 'dias_restantes_display')
    list_filter = ('tipo', 'estado')
    search_fields = ('folio', 'titular_nombre', 'correo')
    readonly_fields = ('folio', 'recibida_en', 'fecha_limite')
    ordering = ['fecha_limite']

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if 'identificacion' in fields:
            indice = fields.index('identificacion')
            fields.pop(indice)
            fields = [field for field in fields if field != 'identificacion_protegida']
            if request.user.has_perm('legal.ver_identificacion_arco'):
                fields.insert(indice, 'identificacion_protegida')
        return fields

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if request.user.has_perm('legal.ver_identificacion_arco'):
            fields.append('identificacion_protegida')
        return fields

    @admin.display(description='Identificación')
    def identificacion_protegida(self, obj):
        if not obj or not obj.identificacion:
            return 'Sin archivo'
        url = reverse('legal:descargar_identificacion_arco', args=[obj.pk])
        return format_html('<a href="{}" target="_blank" rel="noopener">Ver identificación</a>', url)

    @admin.display(description='Folio', ordering='folio')
    def folio_display(self, obj):
        return format_html('<span class="qkt-codigo">{}</span>', obj.folio)

    @admin.display(description='Derecho', ordering='tipo')
    def tipo_display(self, obj):
        return ui.badge(obj.get_tipo_display(), ui.INFO, categoria=True)

    @admin.display(description='Estado', ordering='estado')
    def estado_display(self, obj):
        return ui.badge_por_valor(obj.estado, TONO_ESTADO_ARCO, obj.get_estado_display())

    @admin.display(description='Fecha límite', ordering='fecha_limite')
    def fecha_limite_display(self, obj):
        return date_format(obj.fecha_limite, 'd M Y') if obj.fecha_limite else ui.vacio()

    @admin.display(description='Plazo', ordering='fecha_limite')
    def dias_restantes_display(self, obj):
        if obj.estado in ESTADOS_ARCO_RESPONDIDOS:
            return ui.badge('Respondida', ui.NEUTRO)
        dias = obj.dias_restantes
        if dias < 0:
            return ui.badge(f'Vencida hace {abs(dias)} d', ui.ERROR)
        if dias < 5:
            return ui.badge(f'{dias} d', ui.ERROR)
        return ui.badge(f'{dias} d', ui.EXITO if dias >= 10 else ui.ALERTA)
