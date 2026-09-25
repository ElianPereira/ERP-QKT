"""Filtros de lista reutilizables entre apps (Issue #322)."""
from calendar import monthrange
from datetime import timedelta

from django.contrib import admin
from django.utils import timezone


def _mes(fecha):
    return fecha.replace(day=1), fecha.replace(day=monthrange(fecha.year, fecha.month)[1])


def filtro_periodo(campo, titulo):
    """SimpleListFilter "Este mes / Mes pasado / Últimos 7 días" sobre un
    DateField. Complementa a `date_hierarchy` (navegar por año/mes) con los
    atajos del día a día, que el filtro de fecha de Django parte en dos
    parámetros y Jazzmin no sabe enviar completos."""

    class FiltroPeriodo(admin.SimpleListFilter):
        title = titulo
        parameter_name = f'{campo}_periodo'

        def lookups(self, request, model_admin):
            return (('mes', 'Este mes'), ('anterior', 'Mes pasado'), ('7d', 'Últimos 7 días'))

        def queryset(self, request, queryset):
            hoy = timezone.localdate()
            if self.value() == 'mes':
                return queryset.filter(**{f'{campo}__range': _mes(hoy)})
            if self.value() == 'anterior':
                return queryset.filter(**{f'{campo}__range': _mes(hoy.replace(day=1) - timedelta(days=1))})
            if self.value() == '7d':
                return queryset.filter(**{f'{campo}__range': (hoy - timedelta(days=7), hoy)})
            return queryset

    FiltroPeriodo.__name__ = f'FiltroPeriodo_{campo}'
    return FiltroPeriodo


def con_titulo(titulo):
    """Filtro de un campo con otro título. El de Django usa el verbose_name
    del campo ("Mostrar en cotizador web", "Deducible (con CFDI)"), pensado
    para el formulario y no para la barra.

    Uso: `list_filter = [('visible_cotizador', con_titulo('Visible en cotizador'))]`.
    Delega en `FieldListFilter.create`, así que el tipo de filtro (choices,
    booleano, relación) sigue eligiéndose solo.
    """

    class FiltroTitulado(admin.FieldListFilter):
        def __new__(cls, field, request, params, model, model_admin, field_path):
            spec = admin.FieldListFilter.create(field, request, params, model, model_admin, field_path)
            spec.title = titulo
            return spec

    return FiltroTitulado
