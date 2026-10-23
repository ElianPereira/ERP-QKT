from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.formats import date_format

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo
from core_erp.admin_utils import confirmar_accion_destructiva
from core_erp.descargas import url_descarga

from . import (
    services_agente,
    services_direccion,
    services_pendientes,
    services_responsables,
    services_tablero,
    services_visitas,
)
from .models import (
    BajaWhatsApp,
    CambioResponsable,
    ComunicacionCliente,
    ConversacionWhatsApp,
    EncuestaPostEvento,
    FotoInspeccion,
    Hallazgo,
    HorarioVisita,
    InspeccionDeposito,
    MensajeWhatsApp,
    Pendiente,
    Responsable,
    Visita,
)
from .services import normalizar_telefono_wa


@admin.register(ComunicacionCliente)
class ComunicacionClienteAdmin(admin.ModelAdmin):
    list_display = ('fecha_display', 'canal_badge', 'tipo', 'estado_badge', 'destinatario', 'cotizacion', 'trigger')
    list_filter = ('estado', 'canal', 'tipo', ('trigger', con_titulo('Origen del envío')))
    columnas_texto = ('destinatario',)

    TONOS_ESTADO = {'PENDIENTE': ui.ALERTA, 'ENVIADO': ui.INFO, 'ENTREGADO': ui.EXITO, 'ABIERTO': ui.EXITO,
                    'FALLIDO': ui.ERROR}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cotizacion__cliente')

    @admin.display(description='Fecha', ordering='fecha_envio')
    def fecha_display(self, obj):
        return date_format(timezone.localtime(obj.fecha_envio), 'd M Y H:i') if obj.fecha_envio else ui.vacio()

    @admin.display(description='Canal', ordering='canal')
    def canal_badge(self, obj):
        return ui.badge(obj.get_canal_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())
    search_fields = ('destinatario', 'asunto', 'cotizacion__nombre_evento',
                     'cotizacion__cliente__nombre', 'clave_idempotencia')
    date_hierarchy = 'fecha_envio'
    # La clave es de solo lectura: editarla a mano permitiría reenviar un evento
    # ya notificado o bloquear uno que aún no salió.
    readonly_fields = ('fecha_envio', 'fecha_entrega', 'fecha_apertura', 'proveedor_id',
                       'cuerpo', 'error', 'clave_idempotencia')


class MensajeWhatsAppInline(admin.TabularInline):
    model = MensajeWhatsApp
    extra = 0
    can_delete = False
    fields = ('created_at', 'direccion', 'texto', 'enviado_por')
    readonly_fields = fields
    ordering = ('created_at', 'id')

    def has_add_permission(self, request, obj=None):
        return False


class ConversacionWhatsAppForm(forms.ModelForm):
    responder = forms.CharField(
        label='Responder', required=False, max_length=4000,
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='Se manda por WhatsApp al guardar y pausa al agente en esta conversación. '
                  'Solo funciona si el cliente escribió en las últimas 24 h.',
    )

    class Meta:
        model = ConversacionWhatsApp
        fields = '__all__'


class FiltroOrigenAnuncio(admin.SimpleListFilter):
    """Conversaciones por el anuncio de Meta que las trajo (o sin anuncio)."""
    title = 'Origen'
    parameter_name = 'origen'

    def lookups(self, request, model_admin):
        anuncios = (ConversacionWhatsApp.objects.exclude(anuncio_id='')
                    .values_list('anuncio_id', 'anuncio_titulo').distinct().order_by('anuncio_id'))
        vistos = {}
        for anuncio_id, titulo in anuncios:
            if titulo or anuncio_id not in vistos:
                vistos[anuncio_id] = titulo or anuncio_id
        return [('_sin', 'Sin anuncio'), ('_con', 'Cualquier anuncio')] + [
            (k, v[:60]) for k, v in vistos.items()]

    def queryset(self, request, queryset):
        valor = self.value()
        if valor == '_sin':
            return queryset.filter(anuncio_id='', anuncio_url='')
        if valor == '_con':
            return queryset.exclude(anuncio_id='', anuncio_url='')
        return queryset.filter(anuncio_id=valor) if valor else queryset


@admin.register(ConversacionWhatsApp)
class ConversacionWhatsAppAdmin(admin.ModelAdmin):
    """Conversaciones del agente de WhatsApp (Issue #346). Nacen solas desde el
    webhook; aquí se leen, se contesta como persona y se decide si el agente
    sigue contestando."""
    form = ConversacionWhatsAppForm
    list_display = ('ultimo_display', 'nombre', 'telefono', 'estado_badge', 'anuncio_display', 'cliente')
    list_filter = ('requiere_humano', FiltroOrigenAnuncio)
    search_fields = ('telefono', 'nombre', 'cliente__nombre', 'mensajes__texto')
    fields = ('nombre', 'telefono', 'cliente', 'responder', 'requiere_humano', 'motivo_humano',
              'pausado_hasta', 'ultimo_mensaje', 'consentimiento_en', 'consentimiento_marketing',
              'anuncio_titulo', 'anuncio_id', 'anuncio_url')
    readonly_fields = ('nombre', 'telefono', 'ultimo_mensaje', 'consentimiento_en', 'consentimiento_marketing',
                       'anuncio_titulo', 'anuncio_id', 'anuncio_url')
    autocomplete_fields = ('cliente',)
    inlines = (MensajeWhatsAppInline,)
    actions = ('reactivar_agente',)

    def has_add_permission(self, request):
        return False

    def get_urls(self):
        return [
            path('tablero/', self.admin_site.admin_view(self.tablero_view), name='comunicacion_tablero_agente'),
        ] + super().get_urls()

    def tablero_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        try:
            dias = int(request.GET.get('dias', 30))
        except ValueError:
            dias = 30
        return TemplateResponse(request, 'admin/comunicacion/tablero_agente.html', {
            **self.admin_site.each_context(request),
            'title': 'Tablero de Kooxi',
            'm': services_tablero.metricas_agente(dias),
        })

    @admin.display(description='Último mensaje', ordering='ultimo_mensaje')
    def ultimo_display(self, obj):
        return date_format(timezone.localtime(obj.ultimo_mensaje), 'd M Y H:i')

    @admin.display(description='Anuncio')
    def anuncio_display(self, obj):
        if not (obj.anuncio_id or obj.anuncio_url):
            return ui.vacio()
        return ui.badge((obj.anuncio_titulo or obj.anuncio_id)[:40], ui.NEUTRO, categoria=True)

    @admin.display(description='Agente')
    def estado_badge(self, obj):
        if obj.requiere_humano:
            return ui.badge('Requiere humano', ui.ERROR)
        if obj.agente_en_pausa():
            return ui.badge('En pausa', ui.ALERTA)
        return ui.badge('Contestando', ui.EXITO)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        texto = form.cleaned_data.get('responder')
        if not texto:
            return
        try:
            services_agente.responder_como_persona(obj, texto, request.user)
        except (services_agente.VentanaCerrada, ValueError) as e:
            self.message_user(request, f'No se envió la respuesta: {e}', level=messages.ERROR)
        else:
            self.message_user(request, 'Respuesta enviada por WhatsApp; el agente queda en pausa.')

    @admin.action(description='Reactivar el agente en las seleccionadas')
    def reactivar_agente(self, request, queryset):
        n = queryset.update(requiere_humano=False, motivo_humano='', pausado_hasta=None)
        self.message_user(request, f'Agente reactivado en {n} conversación(es).')


@admin.register(BajaWhatsApp)
class BajaWhatsAppAdmin(admin.ModelAdmin):
    """Solo consulta: quién pidió no recibir promociones ni seguimientos."""
    list_display = ('telefono', 'origen', 'texto', 'created_at')
    list_filter = ('origen',)
    search_fields = ('telefono',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(EncuestaPostEvento)
class EncuestaPostEventoAdmin(admin.ModelAdmin):
    """Solo consulta: la calificación llega por WhatsApp un día después del servicio."""
    list_display = ('folio', 'cliente', 'calificacion_badge', 'estado_badge', 'enviada', 'contestada')
    list_filter = ('estado', 'calificacion')
    search_fields = ('cotizacion__cliente__nombre', 'telefono')
    list_select_related = ('cotizacion__cliente',)
    filtros_visibles = 2
    fields = ('cotizacion', 'telefono', 'estado', 'calificacion', 'resena_solicitada', 'enviada_en', 'contestada_en')
    readonly_fields = fields

    TONOS_ESTADO = {'ENVIADA': ui.ALERTA, 'CONTESTADA': ui.EXITO}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Cotización', ordering='cotizacion')
    def folio(self, obj):
        return f'COT-{obj.cotizacion_id:03d}'

    @admin.display(description='Cliente', ordering='cotizacion__cliente__nombre')
    def cliente(self, obj):
        return obj.cotizacion.cliente.nombre

    @admin.display(description='Calificación', ordering='calificacion')
    def calificacion_badge(self, obj):
        if obj.calificacion is None:
            return ui.vacio()
        tono = ui.EXITO if obj.calificacion >= 4 else ui.ERROR
        return ui.badge(f'{obj.calificacion} de 5', tono)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='Enviada', ordering='enviada_en')
    def enviada(self, obj):
        return date_format(timezone.localtime(obj.enviada_en), 'd M H:i')

    @admin.display(description='Contestada', ordering='contestada_en')
    def contestada(self, obj):
        return date_format(timezone.localtime(obj.contestada_en), 'd M H:i') if obj.contestada_en else ui.vacio()


class FotoInspeccionInline(admin.TabularInline):
    model = FotoInspeccion
    extra = 0
    fields = ('descarga', 'created_at')
    readonly_fields = fields

    @admin.display(description='Foto')
    def descarga(self, obj):
        url = url_descarga(obj, 'archivo') if obj.pk else None
        return ui.boton_icono(url, 'file-download', 'Descargar foto', nueva_pestana=True) if url else ui.vacio()

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InspeccionDeposito)
class InspeccionDepositoAdmin(admin.ModelAdmin):
    """Solo consulta: la inspección se atiende por WhatsApp (ver `services_inspeccion`).
    El dinero se mueve únicamente por `services_deposito.liquidar`."""
    list_display = ('folio', 'cliente', 'estado_badge', 'monto', 'resolucion_badge', 'solicitada', 'vence')
    list_filter = ('estado', 'resolucion')
    search_fields = ('deposito__cotizacion__cliente__nombre',)
    list_select_related = ('deposito__cotizacion__cliente',)
    filtros_visibles = 2
    inlines = [FotoInspeccionInline]
    fields = ('deposito', 'estado', 'resolucion', 'solicitada_en', 'vence_en', 'reportado_por', 'monto_danos',
              'desglose', 'nota', 'resuelto_en')
    readonly_fields = fields

    TONOS_ESTADO = {'SOLICITADA': ui.ALERTA, 'DANOS': ui.ALERTA, 'POR_APROBAR': ui.INFO, 'PROCESANDO': ui.INFO,
                    'LIQUIDADA': ui.EXITO, 'MANUAL': ui.ERROR}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Cotización', ordering='deposito__cotizacion')
    def folio(self, obj):
        return f'COT-{obj.deposito.cotizacion_id:03d}'

    @admin.display(description='Cliente', ordering='deposito__cotizacion__cliente__nombre')
    def cliente(self, obj):
        return obj.deposito.cotizacion.cliente.nombre

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='Daños', ordering='monto_danos')
    def monto(self, obj):
        return ui.monto(obj.monto_danos) if obj.monto_danos is not None else ui.vacio(numerico=True)

    @admin.display(description='Resolución', ordering='resolucion')
    def resolucion_badge(self, obj):
        return ui.badge(obj.get_resolucion_display(), ui.NEUTRO, categoria=True) if obj.resolucion else ui.vacio()

    @admin.display(description='Solicitada', ordering='solicitada_en')
    def solicitada(self, obj):
        return date_format(timezone.localtime(obj.solicitada_en), 'd M H:i')

    @admin.display(description='Devolución automática', ordering='vence_en')
    def vence(self, obj):
        return date_format(timezone.localtime(obj.vence_en), 'd M H:i')


class ResponsableForm(forms.ModelForm):
    telefono = forms.CharField(
        max_length=20, label='Número de WhatsApp',
        help_text='Si cambia, el número nuevo queda en espera y se activa cuando responda ACEPTO desde él; '
                  'mientras tanto los avisos siguen al número anterior.')

    class Meta:
        model = Responsable
        fields = ('rol', 'nombre', 'email', 'modo_copia', 'activo', 'vigente_desde', 'vigente_hasta')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields['telefono'].initial = self.instance.telefono_pendiente or self.instance.telefono_wa

    def clean_telefono(self):
        normalizado = normalizar_telefono_wa(self.cleaned_data['telefono'])
        if not normalizado:
            raise forms.ValidationError('Escribe un número de WhatsApp válido (10 dígitos).')
        return normalizado

    def clean(self):
        datos = super().clean()
        desde, hasta = datos.get('vigente_desde'), datos.get('vigente_hasta')
        if desde and hasta and hasta < desde:
            self.add_error('vigente_hasta', 'La fecha final no puede ser anterior a la inicial.')
        return datos


class CambioResponsableInline(admin.TabularInline):
    model = CambioResponsable
    extra = 0
    can_delete = False
    fields = ('created_at', 'campo', 'antes', 'despues', 'usuario')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Responsable)
class ResponsableAdmin(admin.ModelAdmin):
    """Directorio de a quién le llega cada aviso interno. Solo superusuario."""
    form = ResponsableForm
    list_display = ('nombre', 'rol_badge', 'telefono_actual', 'estado_badge', 'copia', 'vigencia', 'activo')
    list_filter = ('rol', 'activo')
    search_fields = ('nombre', 'telefono_wa', 'telefono_pendiente')
    fields = ('rol', 'nombre', 'telefono', 'estado_numero', 'email', 'modo_copia', 'activo',
              'vigente_desde', 'vigente_hasta')
    readonly_fields = ('estado_numero',)
    inlines = [CambioResponsableInline]
    columnas_texto = ('nombre',)
    CAMPOS_BITACORA = ('rol', 'nombre', 'email', 'modo_copia', 'activo', 'vigente_desde', 'vigente_hasta')

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Rol', ordering='rol')
    def rol_badge(self, obj):
        return ui.badge(obj.get_rol_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Teléfono')
    def telefono_actual(self, obj):
        return obj.telefono_wa or obj.telefono_pendiente or ui.vacio()

    @admin.display(description='Número')
    def estado_badge(self, obj):
        if obj.telefono_pendiente:
            return ui.badge('Esperando ACEPTO', ui.ALERTA)
        if obj.telefono_wa:
            return ui.badge('Confirmado', ui.EXITO)
        return ui.badge('Sin número', ui.ERROR)

    @admin.display(description='Estado del número')
    def estado_numero(self, obj):
        return self.estado_badge(obj) if obj and obj.pk else ui.vacio()

    @admin.display(description='Copia')
    def copia(self, obj):
        return obj.get_modo_copia_display() if obj.rol == 'DIRECCION' else ui.vacio()

    @admin.display(description='Vigencia')
    def vigencia(self, obj):
        if not (obj.vigente_desde or obj.vigente_hasta):
            return ui.badge('Permanente', ui.NEUTRO, categoria=True)
        desde = date_format(obj.vigente_desde, 'd M Y') if obj.vigente_desde else '…'
        hasta = date_format(obj.vigente_hasta, 'd M Y') if obj.vigente_hasta else '…'
        return f'{desde} – {hasta}'

    def save_model(self, request, obj, form, change):
        previo = Responsable.objects.get(pk=obj.pk) if change else None
        antes = {c: getattr(previo, c) for c in self.CAMPOS_BITACORA} if change else {}
        if change:
            obj.updated_by = request.user
        else:
            obj.created_by = obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        if not change:
            services_responsables.registrar_cambio(obj, 'alta', '', f'{obj.get_rol_display()} — {obj.nombre}',
                                                   request.user)
        for campo in self.CAMPOS_BITACORA:
            if change and getattr(obj, campo) != antes[campo]:
                services_responsables.registrar_cambio(obj, campo, antes[campo], getattr(obj, campo), request.user)
        if services_responsables.proponer_telefono(obj, form.cleaned_data['telefono'], request.user):
            self.message_user(request, 'Se envió la solicitud de confirmación. El número se activa cuando '
                                       'responda ACEPTO; mientras tanto los avisos siguen al número anterior.')


@admin.register(Pendiente)
class PendienteAdmin(admin.ModelAdmin):
    """Bandeja única de lo que requiere a una persona (Issue #422). Los
    pendientes nacen de las alertas y los avisos; aquí solo se atienden."""
    list_display = ('titulo', 'tipo_badge', 'severidad_badge', 'rol_badge', 'estado_badge', 'vence', 'creado')
    list_filter = ('estado', 'rol', 'severidad', 'tipo')
    search_fields = ('titulo', 'detalle')
    filtros_visibles = 4
    columnas_texto = ('titulo',)
    actions = ['resolver', 'descartar', 'tomar']
    fields = ('titulo', 'detalle', 'tipo', 'severidad', 'rol', 'estado', 'vence_en', 'tomado_por_direccion',
              'escalado_en', 'resuelto_por', 'resuelto_en', 'created_at')
    readonly_fields = fields

    TONOS_ESTADO = {'ABIERTO': ui.ALERTA, 'RESUELTO': ui.EXITO, 'DESCARTADO': ui.NEUTRO}
    TONOS_SEVERIDAD = {'URGENTE': ui.ERROR, 'NORMAL': ui.NEUTRO}

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Tipo', ordering='tipo')
    def tipo_badge(self, obj):
        return ui.badge(obj.get_tipo_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Severidad', ordering='severidad')
    def severidad_badge(self, obj):
        return ui.badge_por_valor(obj.severidad, self.TONOS_SEVERIDAD, obj.get_severidad_display())

    @admin.display(description='Responsable', ordering='rol')
    def rol_badge(self, obj):
        return ui.badge(obj.get_rol_display(), ui.INFO if obj.tomado_por_direccion else ui.NEUTRO, categoria=True)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='Vence', ordering='vence_en')
    def vence(self, obj):
        texto = date_format(timezone.localtime(obj.vence_en), 'd M H:i')
        return ui.badge(f'Vencido · {texto}', ui.ERROR) if obj.vencido else texto

    @admin.display(description='Creado', ordering='created_at')
    def creado(self, obj):
        return date_format(timezone.localtime(obj.created_at), 'd M H:i')

    def _aplicar(self, request, queryset, funcion, verbo):
        hechos = sum(1 for p in queryset.filter(estado='ABIERTO') if funcion(p, request.user))
        self.message_user(request, f'{hechos} pendiente(s) {verbo}.', messages.SUCCESS if hechos else messages.WARNING)

    @admin.action(description='Marcar como resueltos')
    def resolver(self, request, queryset):
        self._aplicar(request, queryset, services_pendientes.resolver, 'resuelto(s)')

    @admin.action(description='Descartar')
    @confirmar_accion_destructiva('Los pendientes descartados salen de la bandeja sin haberse atendido.')
    def descartar(self, request, queryset):
        self._aplicar(request, queryset, services_pendientes.descartar, 'descartado(s)')

    @admin.action(description='Tomar como Dirección', permissions=['tomar'])
    def tomar(self, request, queryset):
        self._aplicar(request, queryset, services_pendientes.tomar, 'tomado(s)')

    def has_tomar_permission(self, request):
        return request.user.is_superuser


@admin.register(HorarioVisita)
class HorarioVisitaAdmin(admin.ModelAdmin):
    """Ventanas semanales en que se reciben visitas. Kooxi ofrece solo estas;
    se desactivan, no se borran."""
    list_display = ('dia_badge', 'desde', 'hasta', 'duracion', 'activo')
    list_filter = ('activo', 'dia_semana')
    fields = ('dia_semana', 'hora_inicio', 'hora_fin', 'duracion_minutos', 'activo')

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Día', ordering='dia_semana')
    def dia_badge(self, obj):
        return ui.badge(obj.get_dia_semana_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Desde', ordering='hora_inicio')
    def desde(self, obj):
        return obj.hora_inicio.strftime('%H:%M')

    @admin.display(description='Hasta', ordering='hora_fin')
    def hasta(self, obj):
        return obj.hora_fin.strftime('%H:%M')

    @admin.display(description='Cada visita', ordering='duracion_minutos')
    def duracion(self, obj):
        return f'{obj.duracion_minutos} min'

    def save_model(self, request, obj, form, change):
        if change:
            obj.updated_by = request.user
        else:
            obj.created_by = obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Visita)
class VisitaAdmin(admin.ModelAdmin):
    """Visitas que Kooxi agenda. Nacen por confirmar y solo una persona las
    confirma (aquí o con el botón de WhatsApp)."""
    TONOS_ESTADO = {'POR_CONFIRMAR': ui.ALERTA, 'CONFIRMADA': ui.EXITO, 'RECHAZADA': ui.ERROR,
                    'CANCELADA': ui.NEUTRO}
    list_display = ('cuando', 'nombre', 'telefono', 'personas', 'estado_badge', 'decidida_por')
    list_filter = ('estado', 'inicio')
    search_fields = ('nombre', 'telefono')
    fields = ('nombre', 'telefono', 'personas', 'inicio', 'estado', 'cliente', 'conversacion', 'decidida_en',
              'decidida_por', 'recordatorio_en')
    readonly_fields = fields
    columnas_texto = ('nombre',)
    actions = ('confirmar_visitas', 'marcar_no_disponible', 'cancelar_visitas')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Fecha y hora', ordering='inicio')
    def cuando(self, obj):
        return services_visitas.etiqueta(obj.inicio)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    def _decidir(self, request, queryset, aceptar):
        hechas = sum(1 for v in queryset if services_visitas.decidir(v.pk, aceptar, usuario=request.user))
        self.message_user(request, f'{hechas} visita(s) actualizada(s); solo cambian las que estaban por confirmar.')

    @admin.action(description='Confirmar visitas')
    def confirmar_visitas(self, request, queryset):
        self._decidir(request, queryset, True)

    @admin.action(description='Marcar como no disponible')
    def marcar_no_disponible(self, request, queryset):
        self._decidir(request, queryset, False)

    @admin.action(description='Cancelar visitas')
    @confirmar_accion_destructiva('Se cancelarán las visitas seleccionadas, se liberará su horario y se avisará '
                                  'al cliente por WhatsApp.')
    def cancelar_visitas(self, request, queryset):
        hechas = sum(1 for v in queryset if services_visitas.cancelar(v.pk, request.user))
        self.message_user(request, f'{hechas} visita(s) cancelada(s).')


@admin.register(Hallazgo)
class HallazgoAdmin(admin.ModelAdmin):
    """Lo que el ERP detectó y propuso a Dirección en el resumen semanal (Issue #431).
    Nace del cron del lunes; aquí se convierte en tarea o se ignora."""
    list_display = ('titulo', 'tipo_badge', 'estado_badge', 'propuesto', 'creado')
    list_filter = ('estado', 'tipo')
    search_fields = ('titulo', 'detalle')
    columnas_texto = ('titulo',)
    actions = ['crear_tarea', 'ignorar']
    fields = ('titulo', 'detalle', 'tipo', 'estado', 'pendiente', 'propuesto_en', 'resuelto_en', 'resuelto_por',
              'created_at')
    readonly_fields = fields

    TONOS_ESTADO = {'PROPUESTO': ui.ALERTA, 'TAREA': ui.EXITO, 'IGNORADO': ui.NEUTRO}

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Tipo', ordering='tipo')
    def tipo_badge(self, obj):
        return ui.badge(obj.get_tipo_display(), ui.NEUTRO, categoria=True)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='Propuesto', ordering='propuesto_en')
    def propuesto(self, obj):
        return date_format(timezone.localtime(obj.propuesto_en), 'd M H:i') if obj.propuesto_en else ui.vacio()

    @admin.display(description='Creado', ordering='created_at')
    def creado(self, obj):
        return date_format(timezone.localtime(obj.created_at), 'd M H:i')

    @admin.action(description='Crear tarea en la bandeja')
    def crear_tarea(self, request, queryset):
        hechos = sum(1 for h in queryset.filter(estado='PROPUESTO')
                     if services_direccion.crear_tarea(h, request.user) is not None)
        self.message_user(request, f'{hechos} tarea(s) creada(s) en la bandeja.',
                          messages.SUCCESS if hechos else messages.WARNING)

    @admin.action(description='Ignorar')
    @confirmar_accion_destructiva('Los hallazgos ignorados no se vuelven a proponer.')
    def ignorar(self, request, queryset):
        hechos = sum(1 for h in queryset.filter(estado='PROPUESTO') if services_direccion.ignorar(h, request.user))
        self.message_user(request, f'{hechos} hallazgo(s) ignorado(s).', messages.SUCCESS if hechos else messages.WARNING)
