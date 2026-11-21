"""Admin de ventas (Issue #505). Movimientos, comisiones y bitácora son de solo lectura (inmutables);
los cortes se operan con acciones solo para Dirección."""
from django import forms
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from comercial.models import Pago
from contabilidad.models import CuentaBancaria
from core_erp import admin_ui as ui
from core_erp.admin_utils import confirmar_accion_destructiva
from core_erp.descargas import url_descarga

from . import services_cortes, services_efectivo
from .models import ComisionContratacion, CorteComision, EntregaEfectivo, EventoVenta, MovimientoComision


class SoloLectura(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ComisionContratacion)
class ComisionContratacionAdmin(SoloLectura):
    list_display = ('cotizacion', 'vendedor', 'numero_en_periodo', 'anio', 'total_display', 'devengado_display',
                    'confirmada_en')
    list_select_related = ('cotizacion__cliente', 'vendedor')
    list_filter = ('vendedor', 'anio')
    search_fields = ('cotizacion__id', 'vendedor__nombre')

    @admin.display(description='Total', ordering='total')
    def total_display(self, obj):
        return ui.monto(obj.total)

    @admin.display(description='Devengado', ordering='devengado')
    def devengado_display(self, obj):
        return ui.monto(obj.devengado)


@admin.register(MovimientoComision)
class MovimientoComisionAdmin(SoloLectura):
    list_display = ('fecha', 'vendedor', 'tipo', 'monto_display', 'comision', 'visita', 'corte', 'detalle')
    list_select_related = ('vendedor', 'comision__vendedor', 'visita', 'corte__vendedor')
    list_filter = ('vendedor', 'tipo', 'fecha')
    search_fields = ('detalle', 'clave_idempotencia')

    @admin.display(description='Monto', ordering='monto')
    def monto_display(self, obj):
        return ui.monto(obj.monto)


@admin.register(EventoVenta)
class EventoVentaAdmin(SoloLectura):
    list_display = ('created_at', 'cotizacion', 'tipo', 'vendedor', 'usuario', 'detalle')
    list_filter = ('tipo',)


@admin.register(CorteComision)
class CorteComisionAdmin(SoloLectura):
    """Solo Dirección (superusuario): ningún grupo recibe permisos sobre cortes."""
    list_display = ('periodo', 'vendedor', 'tipo', 'estado_badge', 'total_display', 'compensado_display',
                    'pago_programado', 'estado_de_cuenta')
    list_filter = ('estado', 'tipo', 'vendedor')
    actions = ['aprobar', 'marcar_pagado', 'cancelar_pago', 'cancelar_corte']
    TONOS = {'BORRADOR': ui.ALERTA, 'APROBADO': ui.INFO, 'PAGADO': ui.EXITO, 'CANCELADO': ui.NEUTRO}

    @admin.display(description='Periodo', ordering='hasta')
    def periodo(self, obj):
        return f'{obj.desde:%d/%m/%Y} al {obj.hasta:%d/%m/%Y}'

    @admin.display(description='Estado', ordering='estado')
    def estado_badge(self, obj):
        return ui.badge(obj.get_estado_display(), self.TONOS.get(obj.estado, ui.NEUTRO))

    @admin.display(description='Total', ordering='total')
    def total_display(self, obj):
        return ui.monto(obj.total)

    @admin.display(description='Compensado', ordering='compensado')
    def compensado_display(self, obj):
        return ui.monto(obj.compensado) if obj.compensado else ui.vacio()

    @admin.display(description='Se paga el')
    def pago_programado(self, obj):
        return f'{services_cortes.fecha_pago_programada(obj):%d/%m/%Y}'

    @admin.display(description='Estado de cuenta')
    def estado_de_cuenta(self, obj):
        url = url_descarga(obj, 'archivo_pdf')
        return ui.boton_icono(url, 'file-pdf', 'Descargar estado de comisiones', nueva_pestana=True) if url \
            else ui.vacio()

    def _solo_direccion(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied

    @admin.action(description='Aprobar (no paga)')
    @confirmar_accion_destructiva('Aprobar no paga: solo deja el corte listo para pagarse. Revisa visitas manuales y totales.')
    def aprobar(self, request, queryset):
        self._solo_direccion(request)
        hechas = sum(1 for c in queryset if services_cortes.aprobar_corte(c, usuario=request.user))
        self.message_user(request, f'{hechas} corte(s) aprobado(s). Aprobar no paga.',
                          messages.SUCCESS if hechas else messages.WARNING)

    @admin.action(description='Marcar pagado')
    def marcar_pagado(self, request, queryset):
        self._solo_direccion(request)
        cuentas = CuentaBancaria.objects.filter(activa=True, cuenta_contable__isnull=False)
        if request.POST.get('confirmar') == 'si':
            metodo = request.POST.get('metodo', '')
            cuenta = cuentas.filter(pk=request.POST.get('cuenta') or None).first() if metodo == 'TRANSFERENCIA' else None
            hechas, errores = 0, []
            for corte in queryset:
                try:
                    services_cortes.marcar_pagado(corte, request.user, metodo, cuenta,
                                                  request.POST.get('referencia', ''))
                    hechas += 1
                except ValidationError as e:
                    errores.append(f'{corte}: {" ".join(e.messages)}')
            for texto in errores:
                self.message_user(request, texto, messages.ERROR)
            if hechas:
                self.message_user(request, f'{hechas} corte(s) marcado(s) como pagado(s) con su póliza.',
                                  messages.SUCCESS)
            return HttpResponseRedirect(reverse('admin:ventas_cortecomision_changelist'))
        return TemplateResponse(request, 'admin/ventas/cortecomision/marcar_pagado.html', {
            **self.admin_site.each_context(request),
            'title': 'Marcar pagado', 'queryset': queryset, 'cuentas': cuentas,
            'total': sum(c.total for c in queryset), 'opts': self.model._meta,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME, 'action_name': request.POST.get('action'),
        })

    @admin.action(description='Cancelar pago (cancela su póliza)')
    @confirmar_accion_destructiva('Se cancela la póliza del pago y el corte vuelve a «Aprobado». Ya no habrá dinero asentado.')
    def cancelar_pago(self, request, queryset):
        self._solo_direccion(request)
        for corte in queryset:
            try:
                services_cortes.cancelar_pago(corte, request.user)
            except ValidationError as e:
                self.message_user(request, f'{corte}: {" ".join(e.messages)}', messages.ERROR)

    @admin.action(description='Cancelar corte (libera sus movimientos)')
    @confirmar_accion_destructiva('El corte se cancela y sus movimientos quedan libres para el siguiente corte.')
    def cancelar_corte(self, request, queryset):
        self._solo_direccion(request)
        for corte in queryset:
            try:
                services_cortes.cancelar_corte(corte, request.user)
            except ValidationError as e:
                self.message_user(request, f'{corte}: {" ".join(e.messages)}', messages.ERROR)


class EntregaEfectivoForm(forms.ModelForm):
    pagos = forms.ModelMultipleChoiceField(
        queryset=Pago.objects.none(), label='Cobros que cubre',
        help_text='Solo efectivo registrado por esa persona y aún no entregado.')

    class Meta:
        model = EntregaEfectivo
        fields = ('vendedor', 'fecha', 'pagos', 'recibido_por', 'notas')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pagos'].queryset = Pago.objects.filter(
            metodo='EFECTIVO', tipo='INGRESO', entregas_efectivo__isnull=True, usuario__isnull=False,
            cotizacion__es_historica=False).select_related('cotizacion')
        self.fields['pagos'].label_from_instance = lambda p: (
            f'{p.fecha_pago:%d/%m/%Y} · COT-{p.cotizacion_id:03d} · ${p.monto:,.2f}')
        self.fields['fecha'].initial = timezone.localdate

    def clean(self):
        datos = super().clean()
        vendedor, pagos = datos.get('vendedor'), datos.get('pagos')
        if vendedor and pagos:
            try:
                self.instance.monto = services_efectivo.validar_entrega(vendedor, pagos)
            except ValidationError as e:
                self.add_error('pagos', e)
        return datos


@admin.register(EntregaEfectivo)
class EntregaEfectivoAdmin(admin.ModelAdmin):
    """Dirección registra el efectivo que recibió. Solo control: sin póliza y sin compensar comisiones."""
    form = EntregaEfectivoForm
    list_display = ('fecha', 'vendedor', 'monto_display', 'recibido_por')
    list_filter = ('vendedor',)

    def has_change_permission(self, request, obj=None):
        return obj is None and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Monto', ordering='monto')
    def monto_display(self, obj):
        return ui.monto(obj.monto)

    def get_changeform_initial_data(self, request):
        return {'recibido_por': request.user.pk}

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def changelist_view(self, request, extra_context=None):
        for vendedor, saldo in services_efectivo.saldos_pendientes():
            self.message_user(request, f'Pendiente de entregar de {vendedor.nombre}: ${saldo:,.2f}',
                              messages.WARNING)
        return super().changelist_view(request, extra_context)
