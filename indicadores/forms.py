"""Formulario de «Tus números» del plan óptimo (Issue #410): año, costos fijos del año y precio/costo por servicio."""
from decimal import Decimal

from django import forms

from core_erp.impuestos import sin_iva

from .models import ParametrosNegocio
from .services.base import ETIQUETAS, TIPOS, q2


class PlanForm(forms.Form):
    anio = forms.IntegerField(min_value=2000, max_value=2100, label='Año')
    cf_anual = forms.DecimalField(min_value=Decimal('0'), max_digits=14, decimal_places=2, label='Costos fijos del año')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for tipo in TIPOS:
            minimo = Decimal('0.01')
            self.fields[f'precio_{tipo}'] = forms.DecimalField(
                required=False, min_value=minimo, max_digits=12, decimal_places=2,
                label=f'Precio de {ETIQUETAS[tipo].lower()} con IVA')
            self.fields[f'costo_{tipo}'] = forms.DecimalField(
                required=False, min_value=Decimal('0'), max_digits=12, decimal_places=2,
                label=f'Costo variable de {ETIQUETAS[tipo].lower()} sin IVA')

    def servicios(self, p: ParametrosNegocio) -> dict:
        """`{tipo: {'precio', 'neto', 'costo'}}` de los servicios con precio; sin costo, el % de respaldo."""
        datos, resultado = self.cleaned_data, {}
        for tipo in TIPOS:
            precio = datos.get(f'precio_{tipo}')
            if precio is None:
                continue
            neto = q2(sin_iva(precio))
            costo = datos.get(f'costo_{tipo}')
            resultado[tipo] = {
                'precio': precio, 'neto': neto,
                'costo': q2(neto * p.pct_costo_de(tipo) / 100) if costo is None else costo}
        return resultado

    @classmethod
    def iniciales(cls, anio: int, valores: dict) -> dict:
        """Los valores con los que abre la página, a partir de `PlanAnualService.valores_iniciales`."""
        inicial = {'anio': anio, 'cf_anual': valores['cf_anual']}
        for tipo, s in valores['servicios'].items():
            inicial[f'precio_{tipo}'] = s['precio']
            inicial[f'costo_{tipo}'] = s['costo']
        return inicial
