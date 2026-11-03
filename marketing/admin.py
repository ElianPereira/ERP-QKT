from django.contrib import admin, messages
from django.utils import timezone
from django.utils.formats import date_format

from core_erp import admin_ui as ui
from core_erp.admin_filtros import filtro_periodo
from core_erp.admin_utils import confirmar_accion_destructiva

from .models import (
    AccionAnuncio,
    Anuncio,
    AutorizacionMedios,
    Campana,
    Decision,
    EventoConversion,
    GastoPublicitario,
    Lead,
    LeadEvento,
    ParametroMarketing,
    Referido,
    ResenaGoogle,
)
from .services import conversiones, medios, referidos


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


EDITABLES_LEAD = ('recomendado_por', 'recomendado_por_texto')


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
              'etapa_cambiada_en', 'anonimizado_en', 'motivo_perdida', 'motivo_perdida_origen',
              'recomendado_por', 'recomendado_por_texto')
    EDITABLES = EDITABLES_LEAD
    readonly_fields = tuple(f for f in fields if f not in ('motivo_perdida',) + EDITABLES_LEAD)
    autocomplete_fields = ('recomendado_por',)

    TONOS_ETAPA = {'NUEVO': ui.NEUTRO, 'CONTACTADO': ui.INFO, 'VISITA_AGENDADA': ui.INFO,
                   'COTIZADO': ui.ALERTA, 'GANADO': ui.EXITO, 'PERDIDO': ui.ERROR}

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.etapa != Lead.PERDIDO:
            return tuple(f for f in self.fields if f not in self.EDITABLES)
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
        recomendacion = {'recomendado_por', 'recomendado_por_texto'} & set(form.changed_data)
        if recomendacion:
            obj.updated_by = request.user
            super().save_model(request, obj, form, change)
            referidos.registrar(obj, obj.recomendado_por_texto, recomendador=obj.recomendado_por, usuario=request.user)
            if 'motivo_perdida' not in form.changed_data:
                return
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
        ('Límites para las decisiones', {'fields': (
            'cpl_max', 'frecuencia_max', 'umbral_ocupacion_pausa', 'pausa_gasto_minimo', 'pausa_dias',
            'reasignar_diferencia_pct', 'escalar_ocupacion_max', 'fines_libres_min', 'concentracion_max_pct',
            'sin_respuesta_minutos', 'anomalia_factor')}),
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


@admin.register(Decision)
class DecisionAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Historial de las recomendaciones de marketing. Se decide en la pantalla «Marketing»; aquí solo se lee."""
    list_display = ('titulo', 'regla', 'estado_badge', 'creada', 'expira', 'decidido_por')
    list_filter = ('estado', 'tipo_accion', 'regla')
    search_fields = ('titulo', 'objeto_clave')
    columnas_texto = ('titulo',)
    fields = ('titulo', 'regla', 'objeto_clave', 'tipo_accion', 'estado', 'hallazgo', 'evidencia', 'accion_propuesta',
              'expira_en', 'pospuesta_hasta', 'decidido_por', 'decidido_en', 'ejecutado_en', 'resultado',
              'created_at')
    readonly_fields = fields

    TONOS_ESTADO = {'PENDIENTE': ui.ALERTA, 'POSPUESTA': ui.ALERTA, 'APROBADA': ui.EXITO, 'EJECUTADA': ui.EXITO,
                    'RECHAZADA': ui.NEUTRO, 'EXPIRADA': ui.NEUTRO, 'FALLIDA': ui.ERROR}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.display(description='Creada', ordering='created_at')
    def creada(self, obj):
        return date_format(timezone.localtime(obj.created_at), 'd M Y')

    @admin.display(description='Expira', ordering='expira_en')
    def expira(self, obj):
        return date_format(timezone.localtime(obj.expira_en), 'd M Y')


@admin.register(AccionAnuncio)
class AccionAnuncioAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Bitácora de lo ejecutado (o simulado) en la plataforma de anuncios. Solo lectura."""
    list_display = ('creada', 'objeto', 'accion', 'cambio', 'modo_badge', 'estado_badge', 'created_by')
    list_filter = ('modo', 'estado', 'tipo')
    search_fields = ('objeto_nombre', 'objeto_id_externo')
    columnas_texto = ('objeto',)
    fields = ('decision', 'paso', 'reversion', 'tipo', 'objeto_tipo', 'objeto_id_externo', 'objeto_nombre',
              'valor_anterior', 'valor_nuevo', 'modo', 'estado', 'respuesta', 'created_by', 'created_at', 'updated_at')
    readonly_fields = fields

    TONOS_ESTADO = {'OK': ui.EXITO, 'FALLIDA': ui.ERROR, 'EJECUTANDO': ui.ALERTA, 'PENDIENTE': ui.NEUTRO}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Creada', ordering='created_at')
    def creada(self, obj):
        return date_format(timezone.localtime(obj.created_at), 'd M Y H:i')

    @admin.display(description='Objeto')
    def objeto(self, obj):
        return obj.objeto_nombre or obj.objeto_id_externo

    @admin.display(description='Acción', ordering='tipo')
    def accion(self, obj):
        return obj.get_tipo_display() + (' (reversión)' if obj.reversion else '')

    @admin.display(description='Cambio')
    def cambio(self, obj):
        if obj.valor_anterior is None and obj.valor_nuevo is None:
            return '—'
        return f'{obj.valor_anterior} → {obj.valor_nuevo}'

    @admin.display(description='Modo', ordering='modo')
    def modo_badge(self, obj):
        return ui.badge_por_valor(obj.modo, {'REAL': ui.ALERTA, 'SIMULACION': ui.NEUTRO}, obj.get_modo_display())

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())


@admin.register(EventoConversion)
class EventoConversionAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Bandeja de eventos hacia la Conversions API de Meta y las conversiones offline de Google. Solo lectura; se reintenta lo fallido."""
    list_display = ('creado', 'plataforma', 'evento', 'origen', 'valor_sin_iva', 'estado_badge', 'intentos', 'ultimo_error')
    list_filter = ('estado', 'plataforma', 'nombre_evento', 'origen', filtro_periodo('created_at', 'Creado'))
    columnas_texto = ('ultimo_error',)
    actions = ['reintentar_fallidos']
    fields = ('lead', 'plataforma', 'nombre_evento', 'event_id', 'valor', 'moneda', 'origen', 'event_time', 'estado', 'intentos',
              'proximo_intento', 'ultimo_error', 'enviado_en', 'created_at', 'updated_at')
    readonly_fields = fields

    TONOS_ESTADO = {'PENDIENTE': ui.INFO, 'ENVIADO': ui.EXITO, 'FALLIDO': ui.ERROR,
                    'OMITIDO_SIN_CONSENTIMIENTO': ui.NEUTRO, 'CANCELADO': ui.NEUTRO}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Creado', ordering='created_at')
    def creado(self, obj):
        return date_format(timezone.localtime(obj.created_at), 'd M Y H:i')

    @admin.display(description='Evento', ordering='nombre_evento')
    def evento(self, obj):
        return obj.get_nombre_evento_display()

    @admin.display(description='Valor sin IVA', ordering='valor')
    def valor_sin_iva(self, obj):
        return ui.monto(obj.valor) if obj.valor is not None else '—'

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    @admin.action(description='Reintentar los fallidos seleccionados')
    @confirmar_accion_destructiva('Los eventos fallidos volverán a la cola y se enviarán en la siguiente corrida '
                                  '(solo si hay consentimiento de marketing vigente).')
    def reintentar_fallidos(self, request, queryset):
        if not request.user.is_superuser:
            return
        n = conversiones.reintentar(queryset)
        self.message_user(request, f'{n} evento(s) devueltos a la cola.', messages.SUCCESS)


@admin.register(Referido)
class ReferidoAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Recomendaciones y su beneficio. El estado lo mueven las reglas, no se captura; solo se puede anular a mano."""
    list_display = ('codigo', 'recomendador', 'recomendado', 'estado_badge', 'ganado', 'vence', 'usado_en_cotizacion')
    list_filter = ('estado', filtro_periodo('created_at', 'Registrado'))
    search_fields = ('codigo', 'recomendador__nombre', 'recomendador__telefono')
    list_select_related = ('recomendador', 'recomendado_cotizacion', 'usado_en_cotizacion')
    actions = ['anular_seleccionados']
    fields = ('codigo', 'recomendador', 'recomendado_lead', 'recomendado_cotizacion', 'estado', 'ganado_en', 'vence_en',
              'usado_en', 'usado_en_cotizacion', 'uso_confirmado_en', 'monto_beneficio', 'motivo_cierre',
              'notificado_en', 'created_at', 'updated_at')
    readonly_fields = fields

    TONOS = {'REGISTRADO': ui.NEUTRO, 'GANADO': ui.EXITO, 'USADO': ui.INFO, 'VENCIDO': ui.ALERTA, 'ANULADO': ui.ERROR}

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Recomendado')
    def recomendado(self, obj):
        return obj.recomendado_cotizacion or obj.recomendado_lead or ui.vacio()

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS, obj.get_estado_display())

    @admin.display(description='Ganado', ordering='ganado_en')
    def ganado(self, obj):
        return date_format(timezone.localtime(obj.ganado_en), 'd M Y') if obj.ganado_en else ui.vacio()

    @admin.display(description='Vence', ordering='vence_en')
    def vence(self, obj):
        return date_format(timezone.localtime(obj.vence_en), 'd M Y') if obj.vence_en else ui.vacio()

    @admin.action(description='Anular los referidos seleccionados (sin usar)')
    @confirmar_accion_destructiva('Los referidos anulados dejan de dar beneficio. Los ya usados no se tocan.')
    def anular_seleccionados(self, request, queryset):
        if not request.user.is_superuser:
            return
        n = queryset.filter(estado__in=[Referido.REGISTRADO, Referido.GANADO]).update(
            estado=Referido.ANULADO, motivo_cierre='Anulado por el equipo', updated_by=request.user,
            updated_at=timezone.now())
        self.message_user(request, f'{n} referido(s) anulados.', messages.SUCCESS)


@admin.register(AutorizacionMedios)
class AutorizacionMediosAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Permiso de fotos y testimonios. Se captura con su evidencia y se revoca sin borrarse."""
    list_display = ('cliente', 'cotizacion', 'alcances_txt', 'estado_badge', 'origen', 'decidida')
    list_filter = ('estado', 'origen')
    search_fields = ('cliente__nombre', 'cliente__telefono')
    list_select_related = ('cliente', 'cotizacion')
    actions = ['revocar_seleccionadas']
    fields = ('cliente', 'cotizacion', 'alcances', 'estado', 'origen', 'texto_aceptado', 'texto_sha256',
              'referencia_externa', 'solicitada_en', 'decidida_en', 'revocada_en', 'revocada_por', 'motivo_revocacion')
    TONOS = {'SOLICITADA': ui.NEUTRO, 'AUTORIZADA': ui.EXITO, 'RECHAZADA': ui.ALERTA, 'REVOCADA': ui.ERROR}

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ('estado', 'texto_sha256', 'solicitada_en', 'decidida_en', 'revocada_en', 'revocada_por',
                    'motivo_revocacion', 'referencia_externa')
        return self.fields

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            registro = medios.registrar(
                cliente=obj.cliente, cotizacion=obj.cotizacion, alcances=obj.alcances, origen='ADMIN',
                texto=obj.texto_aceptado, usuario=request.user)
            obj.pk, obj.id = registro.pk, registro.pk

    @admin.display(description='Alcances')
    def alcances_txt(self, obj):
        return ', '.join(medios.ETIQUETAS.get(a, a) for a in obj.alcances)

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS, obj.get_estado_display())

    @admin.display(description='Decidida', ordering='decidida_en')
    def decidida(self, obj):
        return date_format(timezone.localtime(obj.decidida_en), 'd M Y') if obj.decidida_en else ui.vacio()

    @admin.action(description='Revocar las autorizaciones seleccionadas')
    @confirmar_accion_destructiva('Se revoca el permiso y se desactivan las fotos y testimonios publicados que dependan de él.')
    def revocar_seleccionadas(self, request, queryset):
        if not request.user.is_superuser:
            return
        desactivados = sum(medios.revocar(a, usuario=request.user, motivo='Revocada por el equipo') for a in queryset)
        self.message_user(request, f'Revocadas. Contenido público desactivado: {desactivados}.', messages.SUCCESS)


@admin.register(ResenaGoogle)
class ResenaGoogleAdmin(SoloSuperusuarioMixin, admin.ModelAdmin):
    """Reseñas de Google ya vistas por el ERP (solo estrellas y fechas, sin datos de quien reseña)."""
    list_display = ('estrellas_txt', 'publicada', 'respondida', 'avisada')
    list_filter = ('estrellas', 'respondida', filtro_periodo('publicada_en', 'Publicada'))
    fields = ('id_externo', 'estrellas', 'publicada_en', 'actualizada_en', 'respondida', 'avisada')
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Estrellas', ordering='estrellas')
    def estrellas_txt(self, obj):
        tono = ui.ERROR if obj.estrellas <= 3 else ui.EXITO
        return ui.badge(f'{obj.estrellas} de 5', tono)

    @admin.display(description='Publicada', ordering='publicada_en')
    def publicada(self, obj):
        return date_format(timezone.localtime(obj.publicada_en), 'd M Y') if obj.publicada_en else ui.vacio()
