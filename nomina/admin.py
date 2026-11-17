from datetime import date, timedelta

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import models as db_models
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html, format_html_join
from django.utils.timezone import now

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo
from core_erp.admin_utils import confirmar_accion_destructiva
from core_erp.descargas import url_descarga

from . import services_documentos, services_expediente
from .models import (
    AprobacionNomina,
    CondicionesPago,
    DocumentoEmpleado,
    Empleado,
    EscalonComision,
    ReciboNomina,
)

try:
    from .views import cargar_nomina, sync_jibble_view
except ImportError:
    cargar_nomina = sync_jibble_view = None


PERMISO_EXPEDIENTE = 'nomina.ver_expediente_empleado'

# Lo que el grupo Nómina ve de un empleado sin el permiso del expediente.
CAMPOS_BASICOS = ('nombre', 'puesto', 'telefono', 'tarifa_base', 'activo')


def _puede_expediente(request) -> bool:
    return request.user.has_perm(PERMISO_EXPEDIENTE)


class AuditoriaMixin:
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        instancias = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in instancias:
            if hasattr(obj, 'created_by') and not obj.pk:
                obj.created_by = request.user
            if hasattr(obj, 'updated_by'):
                obj.updated_by = request.user
            obj.save()
        formset.save_m2m()


class DocumentoEmpleadoInline(admin.TabularInline):
    model = DocumentoEmpleado
    extra = 1
    fields = ['tipo', 'archivo', 'vigencia', 'notas', 'descarga', 'activo']
    readonly_fields = ['descarga']
    can_delete = False
    formfield_overrides = {db_models.FileField: {'widget': forms.FileInput}}

    @admin.display(description='Descarga')
    def descarga(self, obj):
        url = url_descarga(obj, 'archivo') if obj and obj.pk else None
        return ui.boton_icono(url, 'file-download', 'Descargar documento', nueva_pestana=True) if url else ui.vacio()

    def has_view_or_change_permission(self, request, obj=None):
        return _puede_expediente(request) and super().has_view_or_change_permission(request, obj)

    def has_add_permission(self, request, obj=None):
        return _puede_expediente(request) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return _puede_expediente(request) and super().has_change_permission(request, obj)


class ExpedienteFiltro(admin.SimpleListFilter):
    title = 'Expediente'
    parameter_name = 'expediente'

    def lookups(self, request, model_admin):
        return [('completo', 'Completo'), ('incompleto', 'Incompleto')]

    def queryset(self, request, queryset):
        if self.value() not in ('completo', 'incompleto'):
            return queryset
        incompletos = {e.pk for e in queryset.filter(puesto='VENTAS') if services_expediente.expediente_faltantes(e)}
        if self.value() == 'incompleto':
            return queryset.filter(pk__in=incompletos)
        return queryset.exclude(pk__in=incompletos)


@admin.register(Empleado)
class EmpleadoAdmin(AuditoriaMixin, admin.ModelAdmin):
    search_fields = ('nombre',)
    autocomplete_fields = ()
    inlines = [DocumentoEmpleadoInline]
    formfield_overrides = {db_models.FileField: {'widget': forms.FileInput}}

    FIELDSETS_EXPEDIENTE = (
        (None, {'fields': CAMPOS_BASICOS}),
        ('Identificación', {'fields': ('nombre_legal', 'fecha_nacimiento', 'curp', 'rfc', 'tipo_identificacion',
                                       'numero_identificacion', 'identificacion_vigencia')}),
        ('Contacto y domicilio', {'fields': ('email', 'calle_numero', 'colonia', 'codigo_postal', 'municipio',
                                             'estado')}),
        ('Contacto de emergencia', {'fields': ('emergencia_nombre', 'emergencia_parentesco',
                                               'emergencia_telefono')}),
        ('Relación con la Quinta', {'fields': ('usuario', 'fecha_alta', 'fecha_baja', 'motivo_baja')}),
        ('Seguridad social', {'fields': ('nss', 'afiliado_imss', 'fecha_alta_imss', 'notas_seguridad_social')}),
        ('Datos de pago', {'fields': ('forma_pago', 'banco', 'clabe', 'titular_cuenta')}),
        ('Expediente', {'fields': ('expediente_estado', 'acuerdo_pdf')}),
    )
    readonly_fields = ('expediente_estado', 'acuerdo_pdf')

    def get_list_display(self, request):
        columnas = ['nombre', 'puesto', 'tarifa_display', 'telefono', 'activo']
        if _puede_expediente(request):
            columnas.insert(4, 'expediente_badge')
        return columnas

    def get_list_filter(self, request):
        filtros = [('puesto', con_titulo('Puesto')), ('activo', con_titulo('Activo'))]
        if _puede_expediente(request):
            filtros.append(ExpedienteFiltro)
        return filtros

    def get_fieldsets(self, request, obj=None):
        if not _puede_expediente(request):
            return [(None, {'fields': CAMPOS_BASICOS})]
        return self.FIELDSETS_EXPEDIENTE

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if _puede_expediente(request) else ()

    def get_inlines(self, request, obj):
        return [DocumentoEmpleadoInline] if _puede_expediente(request) else []

    @admin.display(description='Tarifa base', ordering='tarifa_base')
    def tarifa_display(self, obj):
        return ui.monto(obj.tarifa_base)

    @admin.display(description='Expediente')
    def expediente_badge(self, obj):
        if obj.puesto != 'VENTAS':
            return ui.vacio()
        faltan = services_expediente.expediente_faltantes(obj)
        if faltan:
            return ui.badge(f'Falta ({len(faltan)})', ui.ALERTA)
        return ui.badge('Completo', ui.EXITO)

    @admin.display(description='Estado del expediente')
    def expediente_estado(self, obj):
        if not obj or not obj.pk:
            return 'Guarda al empleado para capturar sus documentos.'
        if obj.puesto != 'VENTAS':
            return 'El expediente completo solo se exige al puesto de Ventas.'
        faltan = services_expediente.expediente_faltantes(obj)
        if not faltan:
            return format_html('<div class="qkt-aviso qkt-aviso--exito">Expediente completo.</div>')
        return format_html(
            '<div class="qkt-aviso qkt-aviso--alerta"><span class="qkt-aviso__titulo">Falta</span><ul>{}</ul></div>',
            format_html_join('', '<li>{}</li>', ((f,) for f in faltan)))

    @admin.display(description='Acuerdo de confidencialidad')
    def acuerdo_pdf(self, obj):
        if not obj or not obj.pk:
            return ui.vacio()
        url = reverse('admin:nomina_empleado_acuerdo', args=[obj.pk])
        return ui.boton('Generar acuerdo (PDF)', url, icono='file-pdf', nueva_pestana=True, compacto=False)

    def get_urls(self):
        return [path('<int:object_id>/acuerdo/', self.admin_site.admin_view(self.acuerdo_view),
                     name='nomina_empleado_acuerdo')] + super().get_urls()

    def acuerdo_view(self, request, object_id):
        if not (_puede_expediente(request) and self.has_view_permission(request)):
            raise Http404
        empleado = self.get_object(request, str(object_id))
        if empleado is None:
            raise Http404
        try:
            pdf, nombre = services_documentos.pdf_acuerdo(empleado, request=request)
        except services_documentos.FaltanDatos as e:
            messages.error(request, 'No se puede generar el acuerdo. Falta: ' + '; '.join(e.faltantes) + '.')
            return redirect('admin:nomina_empleado_change', empleado.pk)
        respuesta = HttpResponse(pdf, content_type='application/pdf')
        respuesta['Content-Disposition'] = f'inline; filename="{nombre}"'
        return respuesta


@admin.register(DocumentoEmpleado)
class DocumentoEmpleadoAdmin(AuditoriaMixin, admin.ModelAdmin):
    """Lista de revisión del expediente: las altas se hacen desde el empleado."""
    list_display = ('empleado', 'tipo_badge', 'vigencia', 'activo', 'descarga')
    list_filter = [('tipo', con_titulo('Tipo')), ('activo', con_titulo('Activo'))]
    search_fields = ('empleado__nombre',)
    list_select_related = ('empleado',)

    def _permitido(self, request):
        return _puede_expediente(request)

    def has_module_permission(self, request):
        return self._permitido(request) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return self._permitido(request) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Tipo', ordering='tipo')
    def tipo_badge(self, obj):
        return ui.badge(obj.get_tipo_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='')
    def descarga(self, obj):
        url = url_descarga(obj, 'archivo')
        return ui.acciones(ui.boton_icono(url, 'file-download', 'Descargar documento', nueva_pestana=True)
                           if url else ui.hueco_icono())


class EscalonInline(admin.TabularInline):
    model = EscalonComision
    extra = 0
    fields = ['desde_contratacion', 'monto']

    def _editable(self, obj):
        return obj is None or not obj.inmutable

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


@admin.register(CondicionesPago)
class CondicionesPagoAdmin(AuditoriaMixin, admin.ModelAdmin):
    """Condiciones económicas del personal: solo superusuario (Dirección)."""
    list_display = ('empleado', 'puesto_descripcion', 'vigente_desde', 'vigente_hasta', 'estado_badge', 'propuesta_pdf')
    list_filter = [('estado', con_titulo('Estado')), ('empleado', con_titulo('Empleado'))]
    search_fields = ('empleado__nombre',)
    inlines = [EscalonInline]
    actions = ['marcar_propuesta', 'aceptar', 'cancelar']
    readonly_fields = ('aceptada_en', 'created_at', 'updated_at', 'created_by', 'updated_by')
    list_select_related = ('empleado',)
    fieldsets = (
        (None, {'fields': ('empleado', 'puesto_descripcion', 'vigente_desde', 'vigente_hasta', 'estado',
                           'aceptada_en')}),
        ('Visitas', {'fields': ('pago_visita', 'tope_visitas_semana', 'periodicidad_visitas')}),
        ('Comisiones', {'fields': ('porcentaje_extras', 'dias_atribucion', 'tope_compensacion',
                                   'periodicidad_comisiones')}),
        ('Pago fijo', {'fields': ('tarifa_hora', 'pago_fijo', 'pago_fijo_periodo')}),
        ('Texto de la propuesta', {'fields': ('responsabilidades', 'horario', 'notas')}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at')}),
    )
    TONOS = {'BORRADOR': ui.NEUTRO, 'PROPUESTA': ui.INFO, 'ACEPTADA': ui.EXITO,
             'SUSTITUIDA': ui.NEUTRO, 'CANCELADA': ui.ERROR}

    def _solo_direccion(self, request):
        return request.user.is_superuser

    def has_module_permission(self, request):
        return self._solo_direccion(request)

    def has_view_permission(self, request, obj=None):
        return self._solo_direccion(request)

    def has_add_permission(self, request):
        return self._solo_direccion(request)

    def has_change_permission(self, request, obj=None):
        return self._solo_direccion(request) and (obj is None or not obj.inmutable)

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path('<int:object_id>/propuesta/', self.admin_site.admin_view(self.propuesta_view),
                     name='nomina_condicionespago_propuesta')] + super().get_urls()

    def propuesta_view(self, request, object_id):
        if not self._solo_direccion(request):
            raise Http404
        condiciones = self.get_object(request, str(object_id))
        if condiciones is None:
            raise Http404
        pdf, nombre = services_documentos.pdf_propuesta(condiciones, request=request)
        respuesta = HttpResponse(pdf, content_type='application/pdf')
        respuesta['Content-Disposition'] = f'inline; filename="{nombre}"'
        return respuesta

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS, obj.get_estado_display())

    @admin.display(description='')
    def propuesta_pdf(self, obj):
        url = reverse('admin:nomina_condicionespago_propuesta', args=[obj.pk])
        return ui.acciones(ui.boton_icono(url, 'file-pdf', 'Propuesta (PDF)', nueva_pestana=True))

    @admin.action(description='Marcar propuesta como entregada')
    def marcar_propuesta(self, request, queryset):
        hechas = queryset.filter(estado='BORRADOR').update(estado='PROPUESTA', updated_by=request.user)
        self.message_user(request, f'{hechas} propuesta(s) marcada(s) como entregada(s).',
                          messages.SUCCESS if hechas else messages.WARNING)

    @admin.action(description='Aceptar condiciones (sustituye la versión vigente)')
    @confirmar_accion_destructiva('Una condición aceptada ya no se edita y sustituye a la vigente del empleado.')
    def aceptar(self, request, queryset):
        hechas = 0
        for condiciones in queryset:
            try:
                services_expediente.aceptar_condiciones(condiciones, usuario=request.user)
                hechas += 1
            except ValidationError as e:
                self.message_user(request, f'{condiciones.empleado}: {" ".join(e.messages)}', messages.ERROR)
        if hechas:
            self.message_user(request, f'{hechas} condición(es) aceptada(s).', messages.SUCCESS)

    @admin.action(description='Cancelar borrador o propuesta')
    @confirmar_accion_destructiva('Las condiciones canceladas no se pueden reactivar.')
    def cancelar(self, request, queryset):
        hechas = queryset.filter(estado__in=('BORRADOR', 'PROPUESTA')).update(
            estado='CANCELADA', updated_by=request.user)
        self.message_user(request, f'{hechas} condición(es) cancelada(s).',
                          messages.SUCCESS if hechas else messages.WARNING)


@admin.register(ReciboNomina)
class ReciboNominaAdmin(admin.ModelAdmin):
    change_list_template = 'admin/nomina/recibonomina/change_list.html'

    list_display = ('folio_custom', 'empleado', 'periodo', 'total_display', 'estado_badge', 'ver_pdf')
    list_filter = ('estado', 'periodo', 'empleado')
    actions = ['marcar_como_pagado']

    def marcar_como_pagado(self, request, queryset):
        from .services import marcar_recibo_como_pagado
        pendientes = queryset.filter(estado='CALCULADO')
        if not pendientes.exists():
            self.message_user(request, "No hay recibos en estado CALCULADO en la selección.", level=messages.WARNING)
            return
        exitosos = 0
        for recibo in pendientes:
            marcar_recibo_como_pagado(recibo, fecha_pago=now().date(), usuario=request.user)
            exitosos += 1
        self.message_user(request, f"{exitosos} recibo(s) marcado(s) como pagados (administrativo, sin impacto contable).", level=messages.SUCCESS)
    marcar_como_pagado.short_description = "Marcar como pagado en efectivo (solo administrativo)"

    TONOS_ESTADO = {'CALCULADO': ui.ALERTA, 'PAGADO': ui.EXITO, 'CANCELADO': ui.ERROR}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('empleado')

    @admin.display(description='Folio', ordering='id')
    def folio_custom(self, obj):
        return f"NOM-{obj.id:03d}"

    @admin.display(description='Total', ordering='total_pagado')
    def total_display(self, obj):
        return ui.monto(obj.total_pagado)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='')
    def ver_pdf(self, obj):
        if obj.archivo_pdf:
            return ui.acciones(ui.boton_icono(url_descarga(obj, 'archivo_pdf'), 'file-pdf', 'Recibo PDF',
                                              nueva_pestana=True))
        return ui.acciones(ui.hueco_icono())

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['boton_carga'] = True

        jibble_id = getattr(settings, 'JIBBLE_NOMINA_ID', '')
        jibble_secret = getattr(settings, 'JIBBLE_NOMINA_SECRET', '')
        jibble_configurado = bool(jibble_id and jibble_secret)
        extra_context['jibble_configurado'] = jibble_configurado

        if jibble_configurado:
            hoy = date.today()
            lunes_pasado = hoy - timedelta(days=hoy.weekday() + 7)
            domingo_pasado = lunes_pasado + timedelta(days=6)
            extra_context['jibble_fecha_inicio'] = lunes_pasado.strftime('%Y-%m-%d')
            extra_context['jibble_fecha_fin'] = domingo_pasado.strftime('%Y-%m-%d')

        return super().changelist_view(request, extra_context=extra_context)


@admin.register(AprobacionNomina)
class AprobacionNominaAdmin(admin.ModelAdmin):
    """Visto bueno semanal de Dirección (Issue #431). Solo deja constancia de quién y
    cuándo: sin póliza ni transferencia. Nace del cron del viernes; aquí solo se aprueba."""
    list_display = ('semana', 'recibos', 'total_display', 'estado_badge', 'aprobada_por_nombre', 'aprobada')
    list_filter = ('semana_inicio',)
    actions = ['aprobar']
    fields = ('semana_inicio', 'semana_fin', 'recibos', 'total', 'notificada_en', 'aprobada_en',
              'aprobada_por_nombre', 'aprobada_por_telefono')
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Semana', ordering='semana_inicio')
    def semana(self, obj):
        return f'{obj.semana_inicio:%d/%m/%Y} al {obj.semana_fin:%d/%m/%Y}'

    @admin.display(description='Total a pagar', ordering='total')
    def total_display(self, obj):
        return ui.monto(obj.total)

    @admin.display(description='Estado')
    def estado_badge(self, obj):
        return ui.badge('Aprobada', ui.EXITO) if obj.aprobada else ui.badge('Por aprobar', ui.ALERTA)

    @admin.display(description='Fecha de aprobación', ordering='aprobada_en')
    def aprobada(self, obj):
        return date_format(timezone.localtime(obj.aprobada_en), 'd M H:i') if obj.aprobada_en else ui.vacio()

    @admin.action(description='Aprobar (solo constancia)', permissions=['aprobar'])
    def aprobar(self, request, queryset):
        from comunicacion.services_direccion import aprobar_nomina

        hechas = sum(1 for a in queryset if aprobar_nomina(a, usuario=request.user))
        self.message_user(request, f'{hechas} semana(s) aprobada(s). La transferencia se hace en el banco.',
                          messages.SUCCESS if hechas else messages.WARNING)

    def has_aprobar_permission(self, request):
        return request.user.is_superuser
