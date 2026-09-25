"""Filtros de lista reutilizables entre apps (Issue #322)."""
from calendar import monthrange
from datetime import timedelta

from django.contrib import admin
from django.db import models
from django.utils import timezone


def _mes(fecha):
    return fecha.replace(day=1), fecha.replace(day=monthrange(fecha.year, fecha.month)[1])


def filtro_periodo(campo, titulo):
    """SimpleListFilter "Este mes / Mes pasado / Últimos 7 días" sobre un
    DateField o DateTimeField. Complementa a `date_hierarchy` (navegar por año/mes) con los
    atajos del día a día, que el filtro de fecha de Django parte en dos
    parámetros y Jazzmin no sabe enviar completos."""

    class FiltroPeriodo(admin.SimpleListFilter):
        title = titulo
        parameter_name = f'{campo}_periodo'

        def lookups(self, request, model_admin):
            return (('mes', 'Este mes'), ('anterior', 'Mes pasado'), ('7d', 'Últimos 7 días'))

        def queryset(self, request, queryset):
            hoy = timezone.localdate()
            rangos = {
                'mes': _mes(hoy),
                'anterior': _mes(hoy.replace(day=1) - timedelta(days=1)),
                '7d': (hoy - timedelta(days=7), hoy),
            }
            rango = rangos.get(self.value())
            if rango is None:
                return queryset
            # En un DateTimeField se compara la fecha local, no el instante
            es_fecha_hora = isinstance(queryset.model._meta.get_field(campo), models.DateTimeField)
            lookup = f'{campo}__date__range' if es_fecha_hora else f'{campo}__range'
            return queryset.filter(**{lookup: rango})

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
