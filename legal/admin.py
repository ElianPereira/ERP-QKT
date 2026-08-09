from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    AceptacionLegal,
    DocumentoLegal,
    Finalidad,
    SolicitudARCO,
)


@admin.register(DocumentoLegal)
class DocumentoLegalAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'version', 'vigente', 'vigente_desde', 'hash_corto_display')
    list_filter = ('tipo', 'vigente')
    search_fields = ('titulo', 'version')
    actions = ['publicar_version']

    def hash_corto_display(self, obj):
        return obj.hash_corto
    hash_corto_display.short_description = 'SHA-256'

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
    list_display = ('clave', 'nombre', 'requiere_consentimiento', 'activa', 'orden')
    list_filter = ('requiere_consentimiento', 'activa')


@admin.register(AceptacionLegal)
class AceptacionLegalAdmin(admin.ModelAdmin):
    """Evidencia: solo lectura total."""
    list_display = ('id', 'correo', 'cliente', 'origen', 'aceptado_en')
    list_filter = ('origen', 'aceptado_en')
    search_fields = ('correo',)
    list_select_related = ('cliente',)
    date_hierarchy = 'aceptado_en'

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
    list_display = ('folio', 'tipo', 'titular_nombre', 'estado', 'fecha_limite',
                    'dias_restantes_display')
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

    def dias_restantes_display(self, obj):
        dias = obj.dias_restantes
        if dias < 0:
            return format_html('<span style="color:#c62828;font-weight:700;">'
                               'VENCIDA ({} días)</span>', abs(dias))
        if dias < 5:
            return format_html('<span style="color:#c62828;font-weight:700;">{}</span>',
                               dias)
        return dias
    dias_restantes_display.short_description = 'Días restantes'
