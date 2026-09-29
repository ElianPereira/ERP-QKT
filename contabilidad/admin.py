"""
Admin del Módulo de Contabilidad
================================
Sistema de Diseño QKT v2.0
"""
from decimal import Decimal, InvalidOperation

from django.contrib import admin, messages
from django.contrib.admin.widgets import AutocompleteSelect
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.forms.models import BaseInlineFormSet
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core_erp import admin_ui as ui
from core_erp.admin_filtros import con_titulo, filtro_periodo
from core_erp.admin_utils import confirmar_accion_destructiva

from .models import (
    ConciliacionBancaria,
    ConfiguracionContable,
    CuentaBancaria,
    CuentaContable,
    EstadoCuentaBancario,
    MovimientoContable,
    MovimientoEstadoCuenta,
    Poliza,
    ReglaConciliacion,
    SaldoApertura,
    UnidadNegocio,
)
from .services import (
    aplicar_saldo_apertura,
    aprobar_regularizacion_arrastre,
    proponer_regularizacion_arrastre,
)
from .services_estados_cuenta import (
    aplicar_sugerencias_compras,
    emparejar_y_asentar,
    generar_conciliacion_preliminar,
    procesar_estado_cuenta,
)


class MovimientoContableInline(admin.TabularInline):
    model = MovimientoContable
    extra = 2
    fields = ['cuenta', 'concepto', 'debe', 'haber', 'referencia']
    autocomplete_fields = ['cuenta']


class SubcuentaInline(admin.TabularInline):
    model = CuentaContable
    fk_name = 'padre'
    extra = 0
    fields = ['codigo_sat', 'nombre', 'naturaleza', 'permite_movimientos', 'activa']
    readonly_fields = ['codigo_sat', 'nombre']
    show_change_link = True
    verbose_name = "Subcuenta"
    verbose_name_plural = "Subcuentas"

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(CuentaContable)
class CuentaContableAdmin(admin.ModelAdmin):
    list_display = ['codigo_sat', 'nombre', 'tipo_display', 'naturaleza_display', 'nivel', 'permite_movimientos', 'activa']
    list_filter = ['tipo', 'naturaleza', ('nivel', con_titulo('Nivel')), 'activa', ('permite_movimientos', con_titulo('Permite movimientos'))]
    search_fields = ['codigo_sat', 'nombre']
    ordering = ['codigo_sat']
    list_per_page = 50
    autocomplete_fields = ['padre']
    inlines = [SubcuentaInline]

    @admin.display(description="Tipo", ordering="tipo")
    def tipo_display(self, obj):
        return ui.badge(obj.get_tipo_display(), ui.NEUTRO, categoria=True)

    @admin.display(description="Naturaleza", ordering="naturaleza")
    def naturaleza_display(self, obj):
        return 'Deudora' if obj.naturaleza == 'D' else 'Acreedora'


@admin.register(UnidadNegocio)
class UnidadNegocioAdmin(admin.ModelAdmin):
    list_display = ['clave', 'nombre', 'regimen_display', 'activa']
    list_filter = [('regimen_fiscal', con_titulo('Régimen fiscal')), 'activa']
    search_fields = ['clave', 'nombre']

    @admin.display(description="Régimen Fiscal")
    def regimen_display(self, obj):
        texto = obj.get_regimen_fiscal_display()
        if len(texto) > 50:
            texto = texto[:50] + '...'
        return texto


@admin.register(CuentaBancaria)
class CuentaBancariaAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'banco', 'clabe_display', 'cuenta_contable', 'saldo_display', 'activa']
    list_filter = ['banco', 'activa']
    search_fields = ['nombre', 'banco', 'clabe']

    @admin.display(description="CLABE")
    def clabe_display(self, obj):
        if obj.clabe:
            return format_html('<span class="qkt-codigo">•••• {}</span>', obj.clabe[-4:])
        return ui.vacio()

    @admin.display(description="Saldo")
    def saldo_display(self, obj):
        saldo = obj.saldo_actual
        return ui.monto(saldo, tono=ui.ERROR if saldo < 0 else None)


@admin.register(Poliza)
class PolizaAdmin(admin.ModelAdmin):
    list_display = ['folio_display', 'fecha_display', 'tipo_display', 'concepto_display', 'origen_display',
                    'total_display', 'estado_display']
    list_filter = ['estado', 'origen', ('tipo', con_titulo('Tipo')), 'unidad_negocio']
    filtros_visibles = 3
    search_fields = ['folio', 'concepto']
    search_help_text = 'Folio o concepto'
    columnas_texto = ('concepto_display',)
    date_hierarchy = 'fecha'
    ordering = ['-fecha', '-folio']
    readonly_fields = ['created_by', 'created_at', 'cancelada_por', 'fecha_cancelacion',
                       'aplicada_por', 'fecha_aplicacion']
    inlines = [MovimientoContableInline]

    fieldsets = (
        ('Datos de la Póliza', {
            'fields': ('tipo', 'fecha', 'concepto', 'unidad_negocio')
        }),
        ('Estado', {
            'fields': ('estado', 'origen')
        }),
        ('Cancelación', {
            'fields': ('motivo_cancelacion', 'cancelada_por', 'fecha_cancelacion'),
            'classes': ('collapse',)
        }),
        ('Auditoría', {
            'fields': ('created_by', 'created_at', 'aplicada_por', 'fecha_aplicacion'),
            'classes': ('collapse',)
        }),
    )

    TONOS_ESTADO = {'BORRADOR': ui.NEUTRO, 'APLICADA': ui.EXITO, 'CANCELADA': ui.ERROR}

    def get_list_filter(self, request):
        # Con una sola unidad de negocio activa (hoy, QUINTA) el filtro no
        # separa nada: se oculta hasta que exista otra.
        filtros = list(super().get_list_filter(request))
        if UnidadNegocio.objects.filter(activa=True).count() <= 1:
            filtros.remove('unidad_negocio')
        return filtros

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('unidad_negocio')

    @admin.display(description="Folio", ordering="folio")
    def folio_display(self, obj):
        return f"{obj.tipo}-{str(obj.folio).zfill(4)}"

    @admin.display(description="Fecha", ordering="fecha")
    def fecha_display(self, obj):
        return date_format(obj.fecha, 'd M Y') if obj.fecha else ui.vacio()

    @admin.display(description="Tipo", ordering="tipo")
    def tipo_display(self, obj):
        return ui.badge(obj.get_tipo_display(), ui.INFO, categoria=True)

    @admin.display(description="Concepto")
    def concepto_display(self, obj):
        concepto = obj.concepto
        if len(concepto) > 60:
            concepto = concepto[:60] + "…"
        return concepto

    @admin.display(description="Origen", ordering="origen")
    def origen_display(self, obj):
        return obj.get_origen_display()

    @admin.display(description="Total")
    def total_display(self, obj):
        if obj.esta_cuadrada:
            return ui.monto(obj.total_debe)
        return format_html('<span title="La póliza no cuadra: debe ≠ haber">{}</span>',
                           ui.monto(obj.total_debe, tono=ui.ERROR, sufijo=' !'))

    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            obj.folio = Poliza.siguiente_folio(obj.tipo, obj.fecha)
        super().save_model(request, obj, form, change)

    actions = ['aplicar_polizas', 'cancelar_polizas', 'generar_compra_retroactiva_action', 'completar_polizas_compra_action']

    @admin.action(description="Completar con la cuenta de pago de la Compra (pólizas BORRADOR incompletas)")
    def completar_polizas_compra_action(self, request, queryset):
        """
        Para pólizas de Compra que quedaron en BORRADOR sin el movimiento de
        banco (porque a la Compra le faltaba unidad_negocio/cuenta_pago al
        crearse): edita primero la Compra para agregar esos datos, luego
        selecciona aquí su póliza y corre esta acción — agrega el movimiento
        que falta y la deja lista para "Aplicar pólizas seleccionadas".
        """
        from .services import completar_poliza_compra

        completadas = 0
        omitidas = []
        for poliza in queryset:
            try:
                completar_poliza_compra(poliza)
                completadas += 1
            except ValueError as e:
                omitidas.append(str(e))

        if completadas:
            self.message_user(
                request,
                f"{completadas} póliza(s) completada(s) — ya puedes aplicarlas.",
            )
        if omitidas:
            self.message_user(request, "Omitidas: " + '; '.join(omitidas), level=messages.WARNING)

    @admin.action(description="Generar Compra retroactiva (sin factura) para pólizas manuales")
    def generar_compra_retroactiva_action(self, request, queryset):
        from .services import generar_compra_retroactiva

        generadas = 0
        omitidas = []
        for poliza in queryset:
            try:
                generar_compra_retroactiva(poliza)
                generadas += 1
            except ValueError as e:
                omitidas.append(str(e))

        if generadas:
            self.message_user(
                request,
                f"{generadas} Compra(s) retroactiva(s) generada(s) (marcadas como no deducibles) "
                "y vinculada(s) a su póliza existente."
            )
        if omitidas:
            self.message_user(request, "Omitidas: " + '; '.join(omitidas), level=messages.WARNING)

    @admin.action(description="Aplicar pólizas seleccionadas")
    @confirmar_accion_destructiva(
        "¿Aplicar (postear) las pólizas seleccionadas? Una póliza aplicada "
        "entra a los saldos y reportes del ERP."
    )
    def aplicar_polizas(self, request, queryset):
        aplicadas = 0
        errores = []
        sin_autorizacion = []
        for poliza in queryset.filter(estado='BORRADOR'):
            # El mismo candado que la acción de la conciliación: sin esto, la
            # autorización de Dirección se saltaría aplicando el borrador desde
            # esta pantalla.
            if poliza.requiere_autorizacion_direccion and not request.user.is_superuser:
                sin_autorizacion.append("{}-{}".format(poliza.tipo, poliza.folio))
            elif poliza.esta_cuadrada:
                poliza.aplicar(request.user)
                aplicadas += 1
            else:
                errores.append("{}-{}".format(poliza.tipo, poliza.folio))

        if aplicadas:
            self.message_user(request, "{} póliza(s) aplicada(s)".format(aplicadas))
        if errores:
            self.message_user(request, "No cuadran: {}".format(', '.join(errores)), level='ERROR')
        if sin_autorizacion:
            self.message_user(
                request,
                "Solo Dirección puede aplicar regularizaciones de saldos: {}".format(
                    ', '.join(sin_autorizacion)
                ),
                level=messages.ERROR,
            )

    @admin.action(description="Cancelar pólizas seleccionadas")
    @confirmar_accion_destructiva(
        "¿Cancelar las pólizas seleccionadas? Sus movimientos se conservan, "
        "pero dejarán de sumar en cualquier saldo o reporte."
    )
    def cancelar_polizas(self, request, queryset):
        canceladas = queryset.exclude(estado='CANCELADA').update(
            estado='CANCELADA',
            cancelada_por=request.user,
            fecha_cancelacion=timezone.now(),
            motivo_cancelacion='Cancelación masiva desde admin'
        )
        self.message_user(request, "{} póliza(s) cancelada(s)".format(canceladas))


@admin.register(ConciliacionBancaria)
class ConciliacionBancariaAdmin(admin.ModelAdmin):
    list_display = [
        'cuenta_bancaria', 'periodo_display', 'banco_display', 'libros_display',
        'arrastrada_display', 'diferencia_display', 'estado_display',
    ]
    list_filter = ['estado', 'cuenta_bancaria', 'anio']
    ordering = ['-anio', '-mes']

    # Todas las cifras las calcula `generar_conciliacion_preliminar()` a partir
    # del estado de cuenta: capturarlas a mano solo produciría un cuadre falso
    # que la siguiente regeneración borra. Editable queda lo que sí es criterio
    # de quien concilia: el estado y las notas.
    readonly_fields = [
        'veredicto_display', 'cuadre_display', 'cuenta_bancaria', 'mes', 'anio',
        'fecha_inicio_periodo', 'fecha_corte',
        'saldo_segun_banco', 'saldo_segun_libros', 'diferencia_arrastrada',
        'cargos_banco_no_registrados', 'abonos_banco_no_registrados',
        'cargos_empresa_no_cobrados', 'abonos_empresa_no_abonados', 'diferencia',
        'conciliada_por', 'fecha_conciliacion',
    ]
    fieldsets = [
        (None, {'fields': ['veredicto_display', 'cuadre_display']}),
        ("Periodo conciliado", {
            'fields': ['cuenta_bancaria', ('mes', 'anio'),
                       ('fecha_inicio_periodo', 'fecha_corte')],
        }),
        ("Cifras (calculadas desde el estado de cuenta)", {
            'classes': ['collapse'],
            'fields': [
                ('saldo_segun_banco', 'saldo_segun_libros'),
                'diferencia_arrastrada',
                ('cargos_banco_no_registrados', 'abonos_banco_no_registrados'),
                ('abonos_empresa_no_abonados', 'cargos_empresa_no_cobrados'),
                'diferencia',
            ],
        }),
        ("Cierre", {'fields': ['estado', 'notas', 'conciliada_por', 'fecha_conciliacion']}),
    ]

    actions = ['proponer_regularizacion', 'aprobar_regularizacion']

    class Media:
        css = {'all': ('contabilidad/conciliacion.css',)}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cuenta_bancaria')

    def has_add_permission(self, request):
        # Una conciliación nace de un estado de cuenta procesado, nunca a mano.
        return False

    # ------------------------------------------------------------------
    # Regularización de la diferencia arrastrada
    # ------------------------------------------------------------------

    def _propuesta(self, obj, estado='BORRADOR'):
        """Póliza de regularización ligada a esta conciliación, si existe."""
        return Poliza.objects.filter(
            content_type=ContentType.objects.get_for_model(ConciliacionBancaria),
            object_id=obj.pk,
            origen='APERTURA',
            estado=estado,
        ).first()

    @admin.action(description="Proponer regularización de la diferencia arrastrada")
    def proponer_regularizacion(self, request, queryset):
        creadas, omitidas = [], []
        for conciliacion in queryset:
            try:
                poliza = proponer_regularizacion_arrastre(conciliacion, usuario=request.user)
            except ValueError as e:
                omitidas.append(str(e))
            else:
                creadas.append(f"{poliza.tipo}-{str(poliza.folio).zfill(4)} ({conciliacion})")
        if creadas:
            self.message_user(
                request,
                "Propuesta(s) creada(s) EN BORRADOR: " + '; '.join(creadas) +
                ". No afectan los saldos todavía: para que surtan efecto, Dirección "
                "debe autorizarlas con la acción «Autorizar y aplicar la regularización».",
                level=messages.SUCCESS,
            )
        for texto in omitidas:
            self.message_user(request, texto, level=messages.WARNING)

    @admin.action(description="Autorizar y aplicar la regularización (solo Dirección)")
    @confirmar_accion_destructiva(
        "¿Autorizar y aplicar la regularización de la diferencia arrastrada? "
        "Mueve el histórico contra una cuenta de ajuste — no hay una operación "
        "real detrás."
    )
    def aprobar_regularizacion(self, request, queryset):
        if not request.user.is_superuser:
            self.message_user(
                request,
                "Solo Dirección puede autorizar una regularización de saldos: mueve el "
                "histórico contra una cuenta de ajuste, sin una operación real detrás.",
                level=messages.ERROR,
            )
            return

        aplicadas, omitidas = [], []
        for conciliacion in queryset:
            poliza = self._propuesta(conciliacion)
            if not poliza:
                omitidas.append(
                    f"{conciliacion}: no tiene ninguna propuesta en borrador. "
                    "Genérala primero con «Proponer regularización»."
                )
                continue
            try:
                aprobar_regularizacion_arrastre(poliza, usuario=request.user)
            except (ValueError, ValidationError) as e:
                omitidas.append(f"{conciliacion}: {e}")
            else:
                aplicadas.append(f"{poliza.tipo}-{str(poliza.folio).zfill(4)}")

        if aplicadas:
            self.message_user(
                request,
                "Regularización aplicada: " + ', '.join(aplicadas) +
                ". Vuelve a generar la conciliación desde su estado de cuenta para "
                "que la diferencia arrastrada quede en cero.",
                level=messages.SUCCESS,
            )
        for texto in omitidas:
            self.message_user(request, texto, level=messages.WARNING)

    def save_model(self, request, obj, form, change):
        if obj.estado == 'CONCILIADA' and not obj.fecha_conciliacion:
            obj.conciliada_por = request.user
            obj.fecha_conciliacion = timezone.now()
        super().save_model(request, obj, form, change)

    # ------------------------------------------------------------------
    # Lectura para quien concilia
    # ------------------------------------------------------------------

    @admin.display(description="Resultado")
    def veredicto_display(self, obj):
        if obj.cuadra:
            return format_html(
                '<div class="qkt-veredicto qkt-veredicto-ok">'
                '<b>La conciliación cuadra.</b> Lo que dice el banco y lo que dicen las '
                'pólizas coincide en este periodo.{}</div>',
                mark_safe(  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
                    '<br><span class="qkt-muted">Ojo: además hay una diferencia arrastrada '
                    'de <b>${:,.2f}</b> de periodos anteriores, que no se origina aquí. '
                    'Ver el desglose de abajo.</span>'.format(float(obj.diferencia_arrastrada))
                ) if abs(obj.diferencia_arrastrada) >= Decimal('0.01') else '',
            )
        return format_html(
            '<div class="qkt-veredicto qkt-veredicto-mal">'
            '<b>No cuadra por ${}.</b> Revisa el desglose de abajo: la causa está '
            'en alguna de las cuatro partidas.</div>',
            '{:,.2f}'.format(abs(obj.diferencia)),
        )

    def _texto_regularizacion(self, obj):
        """En qué punto va la regularización de esta diferencia arrastrada.

        Se lee dentro del propio desglose porque es ahí donde alguien ve el
        número y se pregunta qué hacer con él.
        """
        aplicada = self._propuesta(obj, estado='APLICADA')
        if aplicada:
            return (
                'Ya se regularizó con la póliza <b>{}-{}</b> del {:%d/%m/%Y}, autorizada '
                'por {}. Vuelve a generar esta conciliación desde su estado de cuenta '
                'para que el número de arriba quede en cero.'
            ).format(
                aplicada.tipo, str(aplicada.folio).zfill(4), aplicada.fecha,
                aplicada.aplicada_por or 'Dirección',
            )

        borrador = self._propuesta(obj, estado='BORRADOR')
        if borrador:
            return (
                '<b class="qkt-naranja">Hay una regularización propuesta en borrador</b>: '
                'la póliza <a href="/admin/contabilidad/poliza/{}/change/">{}-{}</a>, '
                'fechada el {:%d/%m/%Y}. Todavía no afecta ningún saldo. Para que surta '
                'efecto, Dirección debe autorizarla con la acción «Autorizar y aplicar '
                'la regularización» desde el listado de conciliaciones.'
            ).format(
                borrador.pk, borrador.tipo, str(borrador.folio).zfill(4), borrador.fecha,
            )

        return (
            'Para cancelarla, aplica la acción <b>«Proponer regularización de la '
            'diferencia arrastrada»</b> desde el listado de conciliaciones: prepara la '
            'póliza en borrador contra la cuenta de ajuste de apertura, fechada el día '
            'anterior al periodo, y queda esperando la autorización de Dirección.'
        )

    @admin.display(description="De dónde sale la diferencia")
    def cuadre_display(self, obj):
        """El cálculo, renglón por renglón y en español llano. Es la respuesta
        a «¿de dónde sale este número?» sin tener que abrir el código."""
        estado_cuenta = getattr(obj, 'estado_cuenta_origen', None)

        def fila(etiqueta, valor, ayuda='', clase=''):
            return (
                '<tr class="{}"><th>{}<div class="qkt-ayuda-fila">{}</div></th>'
                '<td class="qkt-num">${:,.2f}</td></tr>'
            ).format(clase, etiqueta, ayuda, float(valor))

        filas = []
        if abs(obj.diferencia_arrastrada) >= Decimal('0.01'):
            filas.append(fila(
                'Diferencia que ya venía de antes',
                obj.diferencia_arrastrada,
                'Los libros y el banco ya no coincidían el primer día de este periodo. '
                'No se origina en este estado de cuenta: viene de pólizas anteriores al '
                'primer estado de cuenta cargado, o de asientos a la cuenta de bancos sin '
                'respaldo bancario. ' + self._texto_regularizacion(obj),
                'qkt-fila-alerta',
            ))

        filas.append(fila(
            'Cargos del banco que no están en pólizas',
            obj.cargos_banco_no_registrados,
            'Comisiones, intereses cobrados y demás salidas que el banco aplicó y la '
            'contabilidad todavía no registra. Falta capturarles su póliza.',
        ))
        filas.append(fila(
            'Abonos del banco que no están en pólizas',
            obj.abonos_banco_no_registrados,
            'Intereses ganados, depósitos no identificados y demás entradas que el banco '
            'acreditó y la contabilidad todavía no registra.',
        ))
        filas.append(fila(
            'Depósitos en tránsito',
            obj.abonos_empresa_no_abonados,
            'Cobros ya asentados en pólizas que el banco todavía no acredita. Es normal '
            'a fin de mes: deben aparecer en el estado de cuenta siguiente.',
        ))
        filas.append(fila(
            'Salidas que el banco no ha cobrado',
            obj.cargos_empresa_no_cobrados,
            'Pagos ya asentados en pólizas (cheques, transferencias) que el banco todavía '
            'no descuenta. También es normal a fin de mes.',
        ))

        enlace = ''
        if estado_cuenta:
            enlace = (
                '<p class="qkt-muted">Los movimientos que originan estas partidas están en '
                '<a href="/admin/contabilidad/estadocuentabancario/{}/change/">el estado de '
                'cuenta de este periodo</a>.</p>'
            ).format(estado_cuenta.pk)

        return format_html(
            '<div class="qkt-cuadre">'
            '<table class="qkt-cuadre-tabla">{}'
            '<tr class="qkt-fila-total"><th>Diferencia del periodo</th>'
            '<td class="qkt-num {}">${}</td></tr>'
            '</table>{}</div>',
            mark_safe(''.join(filas)),  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
            'qkt-ok' if obj.cuadra else 'qkt-mal',
            '{:,.2f}'.format(float(obj.diferencia)),
            mark_safe(enlace),  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
        )

    MESES = ['', 'Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    TONOS_ESTADO = {'PENDIENTE': ui.ALERTA, 'EN_PROCESO': ui.INFO, 'CONCILIADA': ui.EXITO}

    @admin.display(description="Banco", ordering="saldo_segun_banco")
    def banco_display(self, obj):
        return ui.monto(obj.saldo_segun_banco)

    @admin.display(description="Libros", ordering="saldo_segun_libros")
    def libros_display(self, obj):
        return ui.monto(obj.saldo_segun_libros)

    @admin.display(description="Arrastrada")
    def arrastrada_display(self, obj):
        if abs(obj.diferencia_arrastrada) < Decimal('0.01'):
            return ui.vacio()
        return format_html(
            '<span title="Descuadre heredado de periodos anteriores, ajeno a este estado de cuenta.">{}</span>',
            ui.monto(obj.diferencia_arrastrada, tono=ui.ALERTA),
        )

    @admin.display(description="Período", ordering="anio")
    def periodo_display(self, obj):
        return f"{self.MESES[obj.mes]} {obj.anio}"

    @admin.display(description="Diferencia")
    def diferencia_display(self, obj):
        if abs(obj.diferencia) < Decimal('0.01'):
            return ui.monto(Decimal('0'), tono=ui.EXITO)
        return ui.monto(obj.diferencia, tono=ui.ERROR)

    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        return ui.badge_por_valor(obj.estado, self.TONOS_ESTADO, obj.get_estado_display())


@admin.register(ConfiguracionContable)
class ConfiguracionContableAdmin(admin.ModelAdmin):
    list_display = ['operacion_display', 'cuenta_display', 'descripcion', 'activa']
    columnas_texto = ('operacion_display', 'cuenta_display', 'descripcion')
    list_filter = ['activa']
    search_fields = ['operacion', 'cuenta__codigo_sat', 'descripcion']
    autocomplete_fields = ['cuenta']

    @admin.display(description="Operación", ordering="operacion")
    def operacion_display(self, obj):
        return obj.get_operacion_display()

    @admin.display(description="Cuenta")
    def cuenta_display(self, obj):
        return format_html('<span class="qkt-codigo">{}</span> {}', obj.cuenta.codigo_sat, obj.cuenta.nombre)


@admin.register(MovimientoContable)
class MovimientoContableAdmin(admin.ModelAdmin):
    list_display = ['poliza_display', 'cuenta', 'concepto', 'debe_display', 'haber_display', 'referencia']
    list_filter = [('poliza__estado', con_titulo('Estado de la póliza')), ('poliza__tipo', con_titulo('Tipo de póliza')),
                   ('cuenta__tipo', con_titulo('Tipo de cuenta'))]
    search_fields = ['cuenta__codigo_sat', 'cuenta__nombre', 'concepto', 'poliza__concepto']
    autocomplete_fields = ['cuenta']

    def get_search_results(self, request, queryset, search_term):
        """Además de los `search_fields`, permite buscar por folio de póliza y
        por importe exacto — que es como se identifica un asiento cuando se
        concilia contra un estado de cuenta."""
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        termino = (search_term or '').strip()
        if not termino:
            return queryset, use_distinct

        extra = Q()
        if termino.isdigit():
            extra |= Q(poliza__folio=int(termino))
        try:
            importe = Decimal(termino.replace(',', '').replace('$', ''))
        except (InvalidOperation, ValueError):
            pass
        else:
            extra |= Q(debe=importe) | Q(haber=importe)

        if extra:
            base = self.model.objects.filter(extra)
            queryset = queryset | self.get_queryset(request).filter(pk__in=base.values('pk'))
        return queryset.select_related('poliza', 'cuenta'), use_distinct

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('poliza', 'cuenta')

    @admin.display(description="Póliza", ordering="poliza__folio")
    def poliza_display(self, obj):
        return format_html('<a href="{}" class="qkt-codigo">{}-{}</a>',
                           reverse('admin:contabilidad_poliza_change', args=[obj.poliza.pk]),
                           obj.poliza.tipo, str(obj.poliza.folio).zfill(4))

    @admin.display(description="Debe", ordering="debe")
    def debe_display(self, obj):
        return ui.monto(obj.debe) if obj.debe > 0 else ui.vacio(numerico=True)

    @admin.display(description="Haber", ordering="haber")
    def haber_display(self, obj):
        return ui.monto(obj.haber) if obj.haber > 0 else ui.vacio(numerico=True)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        """Un renglón de póliza no se borra: se cancela la póliza completa.

        Borrarlo suelto deja el asiento descuadrado, que es justo lo que la
        partida doble no permite. Además esto es lo que quita la X roja de
        "eliminar" del buscador de asientos del estado de cuenta: ahí no
        deselecciona nada, borra el movimiento contable de la base de datos.
        """
        return False


@admin.register(SaldoApertura)
class SaldoAperturaAdmin(admin.ModelAdmin):
    list_display = ['cuenta_bancaria', 'fecha_corte', 'saldo_display', 'aplicado', 'certificado_por']
    list_filter = ['aplicado', filtro_periodo('fecha_corte', 'Fecha de corte')]

    @admin.display(description='Saldo certificado', ordering='saldo_certificado')
    def saldo_display(self, obj):
        return ui.monto(obj.saldo_certificado)
    readonly_fields = ['aplicado', 'poliza']
    actions = ['aplicar_saldo']

    @confirmar_accion_destructiva(
        "¿Generar la póliza de apertura para los saldos certificados "
        "seleccionados? Fija el saldo inicial de la cuenta en el ERP."
    )
    def aplicar_saldo(self, request, queryset):
        aplicados, errores = 0, []
        for saldo in queryset.filter(aplicado=False):
            try:
                aplicar_saldo_apertura(saldo, usuario=request.user)
                aplicados += 1
            except Exception as e:
                errores.append(f"{saldo}: {e}")
        if aplicados:
            self.message_user(request, f"{aplicados} saldo(s) de apertura aplicado(s).", level=messages.SUCCESS)
        for err in errores:
            self.message_user(request, err, level=messages.ERROR)
    aplicar_saldo.short_description = "Generar póliza de apertura"

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.certificado_por = request.user
        super().save_model(request, obj, form, change)


class AutocompleteAsientoBancario(AutocompleteSelect):
    """
    Widget del buscador de asientos del inline de movimientos.

    Igual que el del admin salvo por la URL: apunta a la vista acotada de
    `contabilidad.views`, que solo ofrece asientos de la cuenta de bancos del
    propio estado de cuenta, en el periodo y sin emparejar. El id del estado de
    cuenta viaja en la URL porque el filtro depende del padre y
    `limit_choices_to` no tiene forma de conocerlo.
    """

    def __init__(self, field, admin_site, estado_cuenta_id=None, **kwargs):
        self.estado_cuenta_id = estado_cuenta_id
        super().__init__(field, admin_site, **kwargs)

    def get_url(self):
        url = reverse('contabilidad:autocomplete_asiento_bancario')
        if self.estado_cuenta_id:
            url = f"{url}?estado_cuenta={self.estado_cuenta_id}"
        return url


class MovimientoEstadoCuentaFormSet(BaseInlineFormSet):
    """
    Impide que dos movimientos del banco queden apuntando al mismo asiento.

    El buscador ya excluye los asientos tomados, pero solo conoce el estado de
    la base de datos al abrir la pantalla: dentro de un mismo guardado nada
    impedía elegir el mismo asiento en dos renglones. Un asiento contado dos
    veces desaparece de las partidas de la conciliación y el descuadre que
    provoca no se ve por ningún lado.
    """

    def clean(self):
        super().clean()
        vistos = {}
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or form.cleaned_data.get('DELETE'):
                continue
            asiento = form.cleaned_data.get('movimiento_contable')
            if not asiento:
                continue
            if asiento.pk in vistos:
                form.add_error(
                    'movimiento_contable',
                    f"Este asiento ya está asignado al movimiento del "
                    f"{vistos[asiento.pk]}. Un asiento contable solo puede "
                    f"corresponder a un movimiento del banco: quita la asignación "
                    f"de uno de los dos antes de guardar."
                )
            else:
                vistos[asiento.pk] = form.instance.fecha.strftime('%d/%m/%Y')

        if not vistos or not self.instance.pk:
            return

        # Tomados por un estado de cuenta distinto de la misma cuenta bancaria.
        ajenos = dict(
            MovimientoEstadoCuenta.objects
            .filter(
                estado_cuenta__cuenta_bancaria=self.instance.cuenta_bancaria,
                movimiento_contable_id__in=vistos.keys(),
            )
            .exclude(estado_cuenta=self.instance)
            .values_list('movimiento_contable_id', 'estado_cuenta__periodo_mes')
        )
        if not ajenos:
            return
        for form in self.forms:
            if not hasattr(form, 'cleaned_data'):
                continue
            asiento = form.cleaned_data.get('movimiento_contable')
            if asiento and asiento.pk in ajenos:
                form.add_error(
                    'movimiento_contable',
                    f"Este asiento ya está asignado a un movimiento del estado de "
                    f"cuenta del mes {ajenos[asiento.pk]:02d}. Quítalo de ahí primero."
                )


class MovimientoEstadoCuentaInline(admin.TabularInline):
    """
    Revisión de los movimientos que el parser extrajo del PDF.

    Todo lo que produce el parser (fecha, concepto, importes) es de solo
    lectura: es la copia fiel del documento del banco y editarlo a mano
    rompería la conciliación contra el documento fuente. Lo único accionable
    aquí es el emparejamiento con su asiento contable, su confirmación y —para
    las comisiones que el banco cobra a mes vencido— el periodo de devengo.
    """
    model = MovimientoEstadoCuenta
    formset = MovimientoEstadoCuentaFormSet
    extra = 0
    can_delete = False
    verbose_name_plural = "Movimientos del estado de cuenta"
    fields = [
        'fecha_display', 'concepto_display', 'importe_display',
        'movimiento_contable', 'situacion_display', 'periodo_devengo', 'confirmado',
    ]
    readonly_fields = ['fecha_display', 'concepto_display', 'importe_display', 'situacion_display']

    class Media:
        css = {'all': ('contabilidad/conciliacion.css',)}

    def has_add_permission(self, request, obj=None):
        # Los movimientos los genera el parser del PDF, no se capturan a mano.
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'movimiento_contable__poliza', 'movimiento_contable__cuenta'
        )

    def get_formset(self, request, obj=None, **kwargs):
        # Una consulta para todas las filas: qué movimientos sin asiento ya
        # tienen su póliza de regla esperando en borrador.
        self._con_poliza_borrador = set()
        if obj:
            self._con_poliza_borrador = set(Poliza.objects.filter(
                content_type=ContentType.objects.get_for_model(MovimientoEstadoCuenta),
                object_id__in=obj.movimientos.values('pk'),
                origen='BANCO', estado='BORRADOR',
            ).values_list('object_id', flat=True))
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'movimiento_contable':
            estado_cuenta = getattr(request, '_estado_cuenta_en_edicion', None)
            kwargs['widget'] = AutocompleteAsientoBancario(
                db_field, self.admin_site,
                estado_cuenta_id=estado_cuenta.pk if estado_cuenta else None,
            )
            kwargs['required'] = False
            if estado_cuenta:
                # El queryset del campo solo tiene que poder validar lo que el
                # buscador ofrece, más lo ya emparejado (que el buscador excluye
                # justamente por estar tomado) para no invalidar filas intactas.
                from .views import _candidatos_asiento_bancario
                candidatos = _candidatos_asiento_bancario(estado_cuenta)
                ya_usados = MovimientoEstadoCuenta.objects.filter(
                    estado_cuenta=estado_cuenta, movimiento_contable__isnull=False,
                ).values_list('movimiento_contable_id', flat=True)
                kwargs['queryset'] = MovimientoContable.objects.filter(
                    Q(pk__in=candidatos.values('pk')) | Q(pk__in=ya_usados)
                ).select_related('poliza', 'cuenta')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description="Fecha")
    def fecha_display(self, obj):
        return format_html('<span class="qkt-fecha">{}</span>', obj.fecha.strftime('%d/%m/%Y'))

    @admin.display(description="Concepto del banco")
    def concepto_display(self, obj):
        texto = obj.descripcion or '—'
        corto = texto if len(texto) <= 38 else texto[:35] + '…'
        return format_html('<span class="qkt-concepto" title="{}">{}</span>', texto, corto)

    @admin.display(description="Importe")
    def importe_display(self, obj):
        if obj.cargo > 0:
            return format_html(
                '<span class="qkt-importe qkt-salida">− ${}</span>',
                '{:,.2f}'.format(obj.cargo),
            )
        return format_html(
            '<span class="qkt-importe qkt-entrada">+ ${}</span>',
            '{:,.2f}'.format(obj.abono),
        )

    @admin.display(description="Situación")
    def situacion_display(self, obj):
        if not obj.movimiento_contable_id:
            if obj.pk in getattr(self, '_con_poliza_borrador', set()):
                return format_html(
                    '<span title="{}">{}</span>',
                    'Una regla ya creó su póliza, pero quedó en borrador. Aplícala en Pólizas '
                    'y usa «Volver a emparejar y aplicar reglas».',
                    ui.badge('Póliza en borrador', ui.ALERTA),
                )
            url = reverse('contabilidad:clasificar_movimiento', args=[obj.pk])
            return format_html(
                '<span title="{}">{}</span> {}',
                'Ninguna regla reconoció este movimiento. Búscale el asiento en la columna '
                'anterior o clasifícalo: el ERP lo recordará para los próximos meses.',
                ui.badge('Sin asiento', ui.ERROR),
                ui.boton('Clasificar', url),
            )
        elif obj.confirmado:
            texto, tono, ayuda = 'Confirmado', ui.EXITO, 'Ya revisaste este emparejamiento.'
        elif obj.match_automatico:
            texto, tono, ayuda = ('Por revisar', ui.ALERTA, 'El sistema lo emparejó por importe y fecha. '
                                  'Revísalo y marca «Confirmado» si es correcto.')
        else:
            texto, tono, ayuda = 'Por confirmar', ui.ALERTA, 'Emparejado a mano, falta confirmarlo.'
        return format_html('<span title="{}">{}</span>', ayuda, ui.badge(texto, tono))


@admin.register(EstadoCuentaBancario)
class EstadoCuentaBancarioAdmin(admin.ModelAdmin):
    list_display = [
        'cuenta_bancaria', 'periodo_display', 'estado_display',
        'saldo_final_display', 'avance_display', 'origen',
    ]
    list_filter = ['estado', 'cuenta_bancaria', 'periodo_anio']
    inlines = [MovimientoEstadoCuentaInline]
    actions = ['procesar', 'reemparejar', 'generar_conciliacion', 'sugerir_y_aplicar_compras']
    readonly_fields = ['resumen_display', 'saldo_inicial_estado', 'saldo_final_estado',
                       'fecha_corte_real', 'estado', 'error_detalle', 'conciliacion']
    fieldsets = [
        (None, {'fields': ['resumen_display']}),
        ("Documento del banco", {
            'fields': ['cuenta_bancaria', 'banco', ('periodo_mes', 'periodo_anio'),
                       'archivo', 'formato', 'origen'],
        }),
        ("Lo que leyó el sistema del PDF", {
            'fields': ['estado', ('saldo_inicial_estado', 'saldo_final_estado'),
                       'fecha_corte_real', 'conciliacion', 'error_detalle'],
            'description': "Estos datos los extrae el sistema del propio PDF; no se capturan a mano.",
        }),
    ]

    class Media:
        css = {'all': ('contabilidad/conciliacion.css',)}

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('cuenta_bancaria')

    def get_formsets_with_inlines(self, request, obj=None):
        # El buscador de asientos del inline necesita saber a qué estado de
        # cuenta pertenece la fila para acotar los candidatos. Se cuelga del
        # request (que es por petición) y no del ModelAdmin, que es compartido
        # entre peticiones concurrentes.
        request._estado_cuenta_en_edicion = obj
        yield from super().get_formsets_with_inlines(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.cargado_por = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Período", ordering="periodo_anio")
    def periodo_display(self, obj):
        return f"{obj.periodo_mes:02d}/{obj.periodo_anio}"

    @admin.display(description="Estado", ordering="estado")
    def estado_display(self, obj):
        tonos = {'CARGADO': ui.ALERTA, 'PROCESADO': ui.EXITO, 'ERROR': ui.ERROR}
        return ui.badge_por_valor(obj.estado, tonos, obj.get_estado_display())

    @admin.display(description="Saldo final", ordering="saldo_final_estado")
    def saldo_final_display(self, obj):
        return ui.monto(obj.saldo_final_estado)

    @admin.display(description="Emparejados")
    def avance_display(self, obj):
        total = obj.movimientos.count()
        if not total:
            return ui.badge('Sin procesar', ui.NEUTRO)
        con_asiento = obj.movimientos.filter(movimiento_contable__isnull=False).count()
        return format_html('{}<div class="qkt-sub">{} de {}</div>', ui.avance(con_asiento / total * 100),
                           con_asiento, total)

    @admin.display(description="Cómo se usa esta pantalla")
    def resumen_display(self, obj):
        """Guía corta en la propia pantalla: quien concilia no tiene por qué
        conocer el flujo interno del ERP."""
        if not obj or not obj.pk:
            return mark_safe(
                '<div class="qkt-ayuda">'
                '<b>Paso 1.</b> Elige la cuenta bancaria y el período, sube el PDF que '
                'te da el banco y guarda.<br>'
                '<b>Paso 2.</b> Desde el listado, aplica la acción '
                '«Procesar»: el sistema lee el PDF, extrae los movimientos y propone '
                'a qué asiento contable corresponde cada uno.<br>'
                '<b>Paso 3.</b> Revisa aquí abajo los movimientos y aplica la acción '
                '«Generar conciliación preliminar».'
                '</div>')
        if obj.estado == 'ERROR':
            return format_html(
                '<div class="qkt-ayuda qkt-ayuda-error">'
                'No se pudo leer el PDF: <b>{}</b><br>'
                'Corrige lo que indica el mensaje y vuelve a aplicar la acción «Procesar».'
                '</div>', obj.error_detalle or 'sin detalle',
            )
        if obj.estado != 'PROCESADO':
            return mark_safe(
                '<div class="qkt-ayuda">Todavía no se ha leído el PDF. Vuelve al listado '
                'y aplica la acción <b>«Procesar»</b> sobre este estado de cuenta.</div>')

        total = obj.movimientos.count()
        sin_asiento = obj.movimientos.filter(movimiento_contable__isnull=True).count()
        sin_confirmar = obj.movimientos.filter(
            movimiento_contable__isnull=False, confirmado=False
        ).count()

        pendientes = []
        if sin_asiento:
            pendientes.append(f'<li><b>{sin_asiento}</b> movimiento(s) <b>sin asiento contable</b>: '
                              'búscale el asiento en la columna «Movimiento contable», o registra '
                              'la póliza que falta.</li>')
        if sin_confirmar:
            pendientes.append(f'<li><b>{sin_confirmar}</b> emparejamiento(s) propuestos por el sistema '
                              '<b>sin confirmar</b>: revisa que el asiento sea el correcto y marca '
                              'la casilla «Confirmado».</li>')
        if not pendientes:
            pendientes.append('<li>Todos los movimientos tienen asiento y están confirmados. '
                              'Puedes generar la conciliación desde el listado.</li>')

        return format_html(
            '<div class="qkt-ayuda">'
            'El banco reporta <b>{}</b> movimientos entre el saldo inicial de '
            '<b>${}</b> y el saldo final de <b>${}</b> al corte del <b>{}</b>.'
            '<ul>{}</ul>'
            '<p><b>Para cambiar un asiento mal asignado:</b> haz clic en la '
            '<b class="qkt-naranja">×</b> que está <b>dentro</b> de la caja del '
            'asiento (a la izquierda de la flecha) para quitar la asignación, y '
            'vuelve a hacer clic en la caja para buscar el correcto. Puedes buscar '
            'por concepto, por folio de póliza o escribiendo el importe exacto. Si '
            'el asiento que necesitas ya está asignado a otro movimiento, quítaselo '
            'a ese primero y guarda.</p>'
            'Cuando termines, vuelve al listado y aplica la acción '
            '<b>«Generar conciliación preliminar»</b>.'
            '</div>',
            total,
            '{:,.2f}'.format(obj.saldo_inicial_estado or 0),
            '{:,.2f}'.format(obj.saldo_final_estado or 0),
            obj.fecha_corte_real.strftime('%d/%m/%Y') if obj.fecha_corte_real else '—',
            mark_safe(''.join(pendientes)),  # noqa: S308 -- revisado: solo interpola choices/numeros/HTML fijo, sin texto libre de usuario
        )

    def procesar(self, request, queryset):
        ok, errores = 0, []
        for ec in queryset:
            try:
                procesar_estado_cuenta(ec)
                ok += 1
            except Exception as e:
                errores.append(f"{ec}: {e}")
        if ok:
            self.message_user(request, f"{ok} estado(s) de cuenta procesado(s).", level=messages.SUCCESS)
        for err in errores:
            self.message_user(request, err, level=messages.ERROR)
    procesar.short_description = "Procesar (extraer movimientos y emparejar)"

    @admin.action(description="Volver a emparejar y aplicar reglas (sin releer el PDF)")
    def reemparejar(self, request, queryset):
        for ec in queryset.filter(estado='PROCESADO').select_related('cuenta_bancaria'):
            antes = ec.movimientos.filter(movimiento_contable__isnull=False).count()
            resumen = emparejar_y_asentar(ec, usuario=request.user)
            despues = ec.movimientos.filter(movimiento_contable__isnull=False).count()
            total = ec.movimientos.count()
            self.message_user(
                request,
                f"{ec}: {despues - antes} movimiento(s) nuevos con asiento ({despues} de {total}). "
                f"Reglas: {len(resumen['aplicadas'])} aplicadas, {len(resumen['borrador'])} en borrador, "
                f"{len(resumen['sin_regla'])} sin regla.",
                level=messages.SUCCESS,
            )
            if resumen['sin_cuenta']:
                self.message_user(
                    request,
                    f"{ec}: {len(resumen['sin_cuenta'])} movimiento(s) calzan con una regla cuya operación "
                    "no tiene cuenta en Configuración contable. Asígnala y vuelve a correr esta acción.",
                    level=messages.WARNING,
                )

    def generar_conciliacion(self, request, queryset):
        ok, errores = 0, []
        for ec in queryset.filter(estado='PROCESADO'):
            try:
                generar_conciliacion_preliminar(ec, usuario=request.user)
                ok += 1
            except Exception as e:
                errores.append(f"{ec}: {e}")
        if ok:
            self.message_user(request, f"{ok} conciliación(es) preliminar(es) generada(s). Revísalas en Conciliaciones bancarias.", level=messages.SUCCESS)
        for err in errores:
            self.message_user(request, err, level=messages.ERROR)
    generar_conciliacion.short_description = "Generar conciliación preliminar"

    @confirmar_accion_destructiva(
        "¿Sugerir y aplicar las Compras pendientes que calcen con los cargos sin "
        "emparejar de este estado de cuenta? Para cada cargo con una única Compra "
        "candidata (mismo importe y fecha cercana, de la misma unidad de negocio), "
        "se le asignará la cuenta de pago y se aplicará su póliza. Los cargos con "
        "más de una candidata o sin ninguna no se tocan — quedan para revisión manual."
    )
    def sugerir_y_aplicar_compras(self, request, queryset):
        for ec in queryset:
            aplicadas, ambiguas, sin_candidata = aplicar_sugerencias_compras(ec, request.user)
            if aplicadas:
                self.message_user(
                    request,
                    f"{ec}: {len(aplicadas)} Compra(s) emparejada(s) y aplicada(s). "
                    "Quedan «Por revisar» en el inline de abajo hasta que las confirmes.",
                    level=messages.SUCCESS,
                )
            if ambiguas:
                self.message_user(
                    request,
                    f"{ec}: {len(ambiguas)} cargo(s) con más de una Compra candidata del "
                    "mismo importe — no se asignaron solos, revísalos a mano.",
                    level=messages.WARNING,
                )
            if sin_candidata:
                self.message_user(
                    request,
                    f"{ec}: {len(sin_candidata)} cargo(s) sin ninguna Compra pendiente que calce.",
                    level=messages.WARNING,
                )
            if not (aplicadas or ambiguas or sin_candidata):
                self.message_user(request, f"{ec}: no hay cargos sin emparejar.", level=messages.INFO)
    sugerir_y_aplicar_compras.short_description = "Sugerir y aplicar Compras pendientes"


@admin.register(ReglaConciliacion)
class ReglaConciliacionAdmin(admin.ModelAdmin):
    """Reglas que asientan solos los movimientos del banco sin documento
    (traspasos del dueño, comisiones, gastos con tarjeta). Nunca se borran:
    se desactivan, para no perder de qué regla salió cada póliza."""
    list_display = ['nombre', 'tipo_display', 'clave_display', 'contrapartida_display',
                    'aplica_display', 'origen_display', 'activa']
    columnas_texto = ('nombre', 'clave_display', 'contrapartida_display')
    list_filter = ['activa', 'origen', 'tipo_movimiento', 'aplicar_automaticamente']
    search_fields = ['nombre', 'patrones', 'cuenta_tercero', 'cuenta__codigo_sat', 'cuenta__nombre']
    autocomplete_fields = ['cuenta']
    readonly_fields = ['origen', 'created_by', 'updated_by', 'created_at', 'updated_at']
    fieldsets = [
        ("Cuándo aplica", {
            'fields': ['nombre', 'tipo_movimiento', 'patrones', 'cuenta_tercero', 'prioridad', 'activa'],
        }),
        ("Qué asiento crea", {
            'fields': ['operacion', 'cuenta', 'aplicar_automaticamente'],
            'description': "La otra mitad del asiento; la de bancos sale de la cuenta bancaria del estado de cuenta.",
        }),
        ("Auditoría", {'fields': ['origen', 'created_by', 'updated_by', 'created_at', 'updated_at']}),
    ]

    TONOS_ORIGEN = {'SISTEMA': ui.INFO, 'APRENDIDA': ui.EXITO, 'MANUAL': ui.NEUTRO}

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Aplica a", ordering="tipo_movimiento")
    def tipo_display(self, obj):
        return obj.get_tipo_movimiento_display()

    @admin.display(description="Reconoce")
    def clave_display(self, obj):
        partes = []
        if obj.patrones:
            partes.append(format_html('<span class="qkt-codigo">{}</span>', obj.patrones))
        if obj.cuenta_tercero:
            partes.append(format_html('cuenta <span class="qkt-codigo">{}</span>', obj.cuenta_tercero))
        return mark_safe(' + '.join(partes)) if partes else ui.vacio()  # noqa: S308 -- partes ya escapadas con format_html

    @admin.display(description="Contrapartida")
    def contrapartida_display(self, obj):
        cuenta = obj.cuenta_contrapartida()
        if not cuenta:
            return ui.badge('Sin cuenta configurada', ui.ERROR)
        return format_html('<span class="qkt-codigo">{}</span> {}', cuenta.codigo_sat, cuenta.nombre)

    @admin.display(description="Póliza", ordering="aplicar_automaticamente")
    def aplica_display(self, obj):
        if obj.aplicar_automaticamente:
            return ui.badge('Se aplica sola', ui.EXITO)
        return ui.badge('Queda en borrador', ui.ALERTA)

    @admin.display(description="Origen", ordering="origen")
    def origen_display(self, obj):
        return ui.badge_por_valor(obj.origen, self.TONOS_ORIGEN, obj.get_origen_display())
