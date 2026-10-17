"""
Admin de Indicadores (Issue #402). Solo Dirección (superusuario) ve y edita
parámetros y metas: son supuestos de negocio, no captura operativa.
"""
from datetime import date

from django import forms
from django.contrib import admin, messages
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core_erp import admin_ui as ui

from .models import DIAS_ISO, MetaMensual, ParametrosNegocio
from .services.base import ETIQUETAS, TIPOS
from .services.metas import valores_desde_plan
from .services.plan_anual import PlanAnualService

CAMPOS_DIAS = ('dias_evento', 'dias_pasadia', 'dias_hospedaje')
# La tabla «un servicio por fila» de la plantilla; no están en `fieldsets` para no pintarse dos veces.
CAMPOS_SERVICIOS = (
    'precio_venta_evento', 'costo_directo_evento', 'dias_evento',
    'precio_venta_pasadia', 'costo_directo_pasadia', 'dias_pasadia',
    'precio_venta_hospedaje', 'costo_directo_hospedaje', 'dias_hospedaje',
)


class SoloDireccionMixin:
    """Visible y editable únicamente para el superusuario; nada se borra."""

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

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class ParametrosNegocioForm(forms.ModelForm):
    """Los días de venta se eligen con casillas (lun…dom) y se guardan como la cadena ISO de siempre."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in CAMPOS_DIAS:
            etiqueta = self.fields[campo].label
            ayuda = self.fields[campo].help_text
            self.fields[campo] = forms.MultipleChoiceField(
                choices=list(DIAS_ISO.items()), required=False, label=etiqueta, help_text=ayuda,
                widget=forms.CheckboxSelectMultiple(attrs={'class': 'qkt-dias'}))
            self.initial[campo] = list(getattr(self.instance, campo) or '')

    def _dias(self, campo):
        return ''.join(sorted(self.cleaned_data.get(campo) or []))

    def clean_dias_evento(self):
        return self._dias('dias_evento')

    def clean_dias_pasadia(self):
        return self._dias('dias_pasadia')

    def clean_dias_hospedaje(self):
        return self._dias('dias_hospedaje')

    class Meta:
        model = ParametrosNegocio
        fields = '__all__'


@admin.register(ParametrosNegocio)
class ParametrosNegocioAdmin(SoloDireccionMixin, admin.ModelAdmin):
    form = ParametrosNegocioForm
    change_form_template = 'admin/indicadores/parametrosnegocio/change_form.html'
    list_display = ('vigencia', 'estado', 'precios', 'costo_fijo', 'utilidad', 'tope_eventos', 'actualizado')
    list_filter = ('activo',)
    readonly_fields = ('resumen_servicios', 'created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        ('Costos fijos y utilidad', {
            'description': 'Lo que pagas cada mes sin importar cuánto vendas, y cuánto quieres ganar. De aquí sale la '
                           'utilidad neta de cada combinación.',
            'fields': ('costo_fijo_mensual_override', 'utilidad_mensual_objetivo')}),
        ('Qué sale de esto', {'fields': ('resumen_servicios',)}),
        ('Avanzado', {
            'classes': ('collapse',),
            'description': 'Calendario del equipo, respaldos cuando falta información y vigencia. Casi nunca se tocan.',
            'fields': ('max_eventos_semana', 'dias_preparacion_evento', 'pct_costo_variable_evento',
                       'pct_costo_variable_pasadia', 'pct_costo_variable_hospedaje', 'inicio_datos',
                       'meses_promedio', 'vigente_desde', 'activo')}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at')}),
    )

    def get_form(self, request, obj=None, change=False, **kwargs):
        # Los campos de la tabla viven en la plantilla, no en `fieldsets`: hay que pedirlos aparte.
        if kwargs.get('fields'):
            kwargs['fields'] = [*CAMPOS_SERVICIOS, *kwargs['fields']]
        return super().get_form(request, obj, change=change, **kwargs)

    @admin.display(description='Plan del año con lo guardado')
    def resumen_servicios(self, obj):
        """La respuesta del plan óptimo del año siguiente con estos parámetros: lo que se ve al guardar."""
        enlace = format_html('<a class="qkt-btn qkt-btn--secundario qkt-btn--sm" href="{}">Ver el plan completo</a>',
                             reverse('indicadores:plan'))
        if obj is None or not obj.pk or all(obj.precio_de(t) is None for t in TIPOS):
            return format_html(
                '<p class="qkt-aviso qkt-aviso--info">{}</p><p>{}</p>',
                'Captura el precio y el costo directo de cada servicio y guarda: aquí verás qué conviene vender '
                'el año que entra y cuánta utilidad deja.', enlace)
        hoy = timezone.localdate()
        plan = PlanAnualService.generar(hoy.year + 1, obj, hoy=hoy)
        tono = 'exito' if plan['utilidad'] >= 0 else 'error'
        return format_html(
            '<p><strong>{}.</strong></p><div class="qkt-kpis">'
            '<div class="qkt-kpi qkt-kpi--info"><div class="qkt-kpi__label">Venta neta</div>'
            '<div class="qkt-kpi__valor">{}</div></div>'
            '<div class="qkt-kpi qkt-kpi--{}"><div class="qkt-kpi__label">Utilidad</div>'
            '<div class="qkt-kpi__valor">{}</div></div></div><p>{} Se actualiza al guardar.</p>',
            plan['frase'], ui.monto(plan['venta']), tono, ui.monto(plan['utilidad']), enlace)

    @admin.display(description='Precios')
    def precios(self, obj):
        capturados = [ETIQUETAS[t] for t in TIPOS if obj.precio_de(t) is not None]
        return ', '.join(capturados) if capturados else ui.vacio()

    @admin.display(description='Vigencia', ordering='vigente_desde')
    def vigencia(self, obj):
        return f'Desde {obj.vigente_desde:%d/%m/%Y}'

    @admin.display(description='Estado', ordering='activo')
    def estado(self, obj):
        return ui.badge('Vigente' if obj.activo else 'Anterior', ui.EXITO if obj.activo else ui.NEUTRO)

    @admin.display(description='Costo fijo')
    def costo_fijo(self, obj):
        return ui.monto(obj.costo_fijo_mensual_override) if obj.costo_fijo_mensual_override is not None \
            else ui.vacio(numerico=True)

    @admin.display(description='Utilidad objetivo')
    def utilidad(self, obj):
        return ui.monto(obj.utilidad_mensual_objetivo)

    @admin.display(description='Eventos por semana')
    def tope_eventos(self, obj):
        return obj.max_eventos_semana

    @admin.display(description='Actualizado', ordering='updated_at')
    def actualizado(self, obj):
        return obj.updated_at.strftime('%d/%m/%Y') if obj.updated_at else ui.vacio()


@admin.register(MetaMensual)
class MetaMensualAdmin(SoloDireccionMixin, admin.ModelAdmin):
    change_form_template = 'admin/indicadores/metamensual/change_form.html'
    list_display = ('periodo', 'estado', 'venta', 'ocupacion', 'unidades', 'marketing')
    list_filter = ('activo', 'anio')
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        ('Mes', {
            'description': 'Una meta activa por mes. Para empezar usa «Llenar desde el plan»: trae la venta, la '
                           'ocupación y las unidades del plan de ese mes (sin guardar nada) y tú las ajustas. Sin '
                           'plan, la acción «Proponer desde el plan» de la lista usa el punto de equilibrio.',
            'fields': ('anio', 'mes', 'activo')}),
        ('Venta y ocupación', {
            'description': 'Lo que quieres lograr en el mes. El tablero compara lo real contra esto; con 0 usa el '
                           'punto de equilibrio.',
            'fields': ('meta_venta_neta', 'meta_ocupacion_pct')}),
        ('Por servicio', {
            'description': 'Cuántas unidades de cada servicio necesitas vender para llegar a la meta. El hospedaje '
                           'se cuenta en noches-habitación.',
            'fields': ('meta_eventos', 'meta_pasadias', 'meta_noches')}),
        ('Marketing y notas', {'fields': ('presupuesto_marketing', 'notas')}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at')}),
    )

    def _valores_del_plan(self, request, obj=None) -> dict:
        """Con `?desde_plan=1&anio=&mes=`: lo que el plan de ese mes propone. Nunca guarda nada."""
        if not request.GET.get('desde_plan'):
            return {}
        hoy = timezone.localdate()
        try:
            anio, mes = int(request.GET['anio']), int(request.GET['mes'])
            date(anio, mes, 1)
        except (KeyError, ValueError):
            anio, mes = (obj.anio, obj.mes) if obj else (hoy.year, hoy.month)
        valores = valores_desde_plan(anio, mes, hoy=hoy)
        if valores is None:
            self.message_user(
                request, f'No hay un plan para {mes:02d}/{anio}: captura el precio de algún servicio en '
                         'Parámetros. Los campos quedan como estaban.', messages.WARNING)
            return {}
        self.message_user(request, f'Valores del plan de {mes:02d}/{anio}. Revísalos y pulsa Guardar para conservarlos.',
                          messages.INFO)
        return {'anio': anio, 'mes': mes, **valores}

    def get_changeform_initial_data(self, request):
        hoy = timezone.localdate()
        return {'anio': hoy.year, 'mes': hoy.month, **super().get_changeform_initial_data(request),
                **self._valores_del_plan(request)}

    def get_form(self, request, obj=None, change=False, **kwargs):
        formulario = super().get_form(request, obj, change=change, **kwargs)
        if obj is None or not kwargs.get('fields') or request.method != 'GET' or not request.GET.get('desde_plan'):
            return formulario
        valores = self._valores_del_plan(request, obj)

        class ConPlan(formulario):
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self.initial.update(valores)

        return ConPlan

    @admin.display(description='Mes', ordering='mes')
    def periodo(self, obj):
        return f'{obj.mes:02d}/{obj.anio}'

    @admin.display(description='Estado', ordering='activo')
    def estado(self, obj):
        return ui.badge('Activa' if obj.activo else 'Inactiva', ui.EXITO if obj.activo else ui.NEUTRO)

    @admin.display(description='Venta neta', ordering='meta_venta_neta')
    def venta(self, obj):
        return ui.monto(obj.meta_venta_neta)

    @admin.display(description='Ocupación')
    def ocupacion(self, obj):
        return f'{obj.meta_ocupacion_pct}%'

    @admin.display(description='Eventos / pasadías / noches')
    def unidades(self, obj):
        return f'{obj.meta_eventos} / {obj.meta_pasadias} / {obj.meta_noches}'

    @admin.display(description='Marketing')
    def marketing(self, obj):
        return ui.monto(obj.presupuesto_marketing)

    actions = ['proponer_desde_equilibrio']

    @admin.action(description='Proponer desde el plan')
    def proponer_desde_equilibrio(self, request, queryset):
        from .services.metas import proponer_meta

        meses = sorted({(m.anio, m.mes) for m in queryset})
        for anio, mes in meses:
            proponer_meta(anio, mes, request.user)
        self.message_user(request, f'Metas propuestas: {len(meses)} mes(es).',
                          messages.SUCCESS)
