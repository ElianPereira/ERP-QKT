"""
Admin de Indicadores (Issue #402). Solo Dirección (superusuario) ve y edita
parámetros y metas: son supuestos de negocio, no captura operativa.
"""
from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from core_erp import admin_ui as ui

from .models import DIAS_ISO, MetaMensual, ParametrosNegocio
from .services.base import ETIQUETAS, TIPOS
from .services.mezcla import referencia

CAMPOS_DIAS = ('dias_evento', 'dias_pasadia', 'dias_hospedaje')


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
    list_display = ('vigencia', 'estado', 'precios', 'costo_fijo', 'utilidad', 'tope_eventos', 'actualizado')
    list_filter = ('activo',)
    readonly_fields = ('resumen_servicios', 'created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        ('Precios', {
            'description': 'La mezcla óptima se arma solo con esto: lo que cobras y lo que cuesta atender cada '
                           'servicio. Si dejas un precio vacío, se usa el promedio de tus ventas reales '
                           '(poco confiable con pocas ventas).',
            'fields': ('precio_venta_evento', 'costo_directo_evento',
                       'precio_venta_pasadia', 'costo_directo_pasadia',
                       'precio_venta_hospedaje', 'costo_directo_hospedaje', 'resumen_servicios')}),
        ('Calendario', {
            'description': 'En qué días aceptas cada servicio y cuánto puede atender el equipo.',
            'fields': ('dias_evento', 'dias_pasadia', 'dias_hospedaje', 'max_eventos_semana',
                       'dias_preparacion_evento')}),
        ('Demanda', {
            'description': 'Cuánto crees poder vender al mes. Sin tope, la mezcla supone que se vende todo '
                           'lo que cabe: úsalo para ver el techo y luego pon tu demanda realista.',
            'fields': ('demanda_max_evento_mes', 'demanda_max_pasadia_mes', 'demanda_max_hospedaje_mes')}),
        ('Costos fijos', {
            'description': 'Lo que pagas cada mes sin importar cuánto vendas, y cuánto quieres ganar.',
            'fields': ('costo_fijo_mensual_override', 'utilidad_mensual_objetivo')}),
        ('Respaldos', {
            'description': 'Datos que se usan solo cuando falta información: el periodo de la contabilidad y el '
                           '% de costo cuando no capturas el costo directo en pesos.',
            'fields': ('inicio_datos', 'meses_promedio', 'pct_costo_variable_evento',
                       'pct_costo_variable_pasadia', 'pct_costo_variable_hospedaje')}),
        ('Vigencia', {
            'description': 'Al guardar un juego de parámetros activo, el anterior queda como histórico.',
            'fields': ('vigente_desde', 'activo')}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at')}),
    )

    @admin.display(description='Qué sale de esto')
    def resumen_servicios(self, obj):
        """Margen y prioridad de cada servicio con lo ya guardado: lo que la mezcla optimizará."""
        filas = []
        for tipo in TIPOS:
            ref = obj and referencia(obj, tipo)
            if ref:
                filas.append((tipo, ref))
        if not filas:
            return format_html(
                '<p class="qkt-aviso qkt-aviso--info">{}</p>',
                'Captura el precio de venta y el costo directo de cada servicio y guarda: aquí verás cuánto '
                'deja cada uno y en qué orden conviene venderlos.')
        # La unidad de la mezcla es el día-quinta: todas las referencias ocupan 1 día.
        filas.sort(key=lambda f: -f[1]['mc'])
        cuerpo = format_html_join(
            '', '<tr><th>{}</th><td class="qkt-der qkt-num">{}</td><td class="qkt-der qkt-num">{}</td>'
                '<td class="qkt-der qkt-num">{} ({}%)</td></tr>',
            ((f'{orden} · {ETIQUETAS[tipo]}', ui.monto(r['neto']), ui.monto(r['costo']), ui.monto(r['mc']),
              round(r['mc'] / r['neto'] * 100) if r['neto'] else 0)
             for orden, (tipo, r) in enumerate(filas, 1)))
        return format_html(
            '<table class="qkt-tabla"><thead><tr><th>Prioridad</th><th class="qkt-der">Precio sin IVA</th>'
            '<th class="qkt-der">Costo directo</th><th class="qkt-der">Margen por día</th></tr></thead>'
            '<tbody>{}</tbody></table><p>La Quinta atiende un servicio por día: conviene vender primero el que '
            'más deja por día. Se actualiza al guardar.</p>', cuerpo)

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
    list_display = ('periodo', 'estado', 'venta', 'ocupacion', 'unidades', 'marketing')
    list_filter = ('activo', 'anio')
    readonly_fields = ('created_by', 'updated_by', 'created_at', 'updated_at')
    fieldsets = (
        ('Mes', {
            'description': 'Una meta activa por mes. Para empezar, selecciona el mes en la lista y usa la acción '
                           '«Proponer desde equilibrio»: calcula la venta, ocupación y unidades necesarias para '
                           'tu utilidad objetivo y tú las ajustas.',
            'fields': ('anio', 'mes', 'activo')}),
        ('Venta y ocupación', {
            'description': 'Lo que quieres lograr en el mes. Con 0 el tablero usa el punto de equilibrio.',
            'fields': ('meta_venta_neta', 'meta_ocupacion_pct')}),
        ('Por servicio', {
            'description': 'Cuántas unidades de cada servicio necesitas vender para llegar a la meta.',
            'fields': ('meta_eventos', 'meta_pasadias', 'meta_noches')}),
        ('Marketing y notas', {'fields': ('presupuesto_marketing', 'notas')}),
        ('Auditoría', {'fields': ('created_by', 'created_at', 'updated_by', 'updated_at')}),
    )

    def get_changeform_initial_data(self, request):
        hoy = timezone.localdate()
        return {**super().get_changeform_initial_data(request), 'anio': hoy.year, 'mes': hoy.month}

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

    @admin.action(description='Proponer desde equilibrio')
    def proponer_desde_equilibrio(self, request, queryset):
        from .services.metas import proponer_meta

        meses = sorted({(m.anio, m.mes) for m in queryset})
        for anio, mes in meses:
            proponer_meta(anio, mes, request.user)
        self.message_user(request, f'Metas propuestas desde el equilibrio: {len(meses)} mes(es).',
                          messages.SUCCESS)
