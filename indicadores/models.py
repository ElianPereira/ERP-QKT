"""
Módulo Indicadores (Issue #402)
===============================
Parámetros que captura el propietario y metas mensuales. Todo lo demás
(capacidad, equilibrio, ocupación, mezcla) se calcula en `indicadores/services/`
sobre datos que el ERP ya tiene; aquí solo viven los supuestos.

Ambos modelos siguen la regla de soft-deactivation: se desactiva, nunca se borra.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

DIAS_ISO = {'1': 'lun', '2': 'mar', '3': 'mié', '4': 'jue', '5': 'vie', '6': 'sáb', '7': 'dom'}
PORCENTAJE = [MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))]


def _validar_dias(valor: str, campo: str):
    """Dígitos ISO 1-7 (lunes = 1), sin repetir. Vacío = no se vende ese servicio."""
    if any(d not in DIAS_ISO for d in valor) or len(set(valor)) != len(valor):
        raise ValidationError({campo: 'Usa dígitos del 1 (lunes) al 7 (domingo), sin repetir. Ej.: 567.'})


class ParametrosNegocio(models.Model):
    """Supuestos del propietario. Uno vigente; al guardar uno nuevo activo, los demás se desactivan."""

    vigente_desde = models.DateField(default=date.today, verbose_name='Vigente desde')
    activo = models.BooleanField(default=True, verbose_name='Activo')

    inicio_datos = models.DateField(
        default=date(2026, 7, 1), verbose_name='Inicio de datos',
        help_text='La contabilidad del ERP arranca aquí; no se promedia nada anterior.')
    meses_promedio = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(12)],
        verbose_name='Meses para promediar')

    dias_evento = models.CharField(
        max_length=7, default='567', blank=True, verbose_name='Días de evento',
        help_text='Días de la semana en que se venden eventos (1 = lunes … 7 = domingo).')
    dias_pasadia = models.CharField(
        max_length=7, default='1234567', blank=True, verbose_name='Días de pasadía')
    dias_hospedaje = models.CharField(
        max_length=7, default='1234567', blank=True, verbose_name='Días de hospedaje')
    max_eventos_semana = models.PositiveSmallIntegerField(
        default=2, verbose_name='Máximo de eventos por semana',
        help_text='Tope operativo del equipo.')
    dias_preparacion_evento = models.PositiveSmallIntegerField(
        default=0, verbose_name='Días de preparación por evento',
        help_text='Días extra que bloquea un evento (montaje y desmontaje). '
                  'Solo afecta el cálculo de capacidad, no la disponibilidad real.')

    pct_costo_variable_evento = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('35.00'), validators=PORCENTAJE,
        verbose_name='% costo variable de evento',
        help_text='Respaldo cuando no hay muestra suficiente de ventas con gasto ligado.')
    pct_costo_variable_pasadia = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('15.00'), validators=PORCENTAJE,
        verbose_name='% costo variable de pasadía')
    pct_costo_variable_hospedaje = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('10.00'), validators=PORCENTAJE,
        verbose_name='% costo variable de hospedaje')

    costo_fijo_mensual_override = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        verbose_name='Costo fijo mensual presupuestado',
        help_text='Vacío = promedio contable de los últimos meses cerrados.')
    utilidad_mensual_objetivo = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name='Utilidad mensual objetivo',
        help_text='0 = la meta es el punto de equilibrio.')

    demanda_max_evento_mes = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Demanda máxima de eventos al mes',
        help_text='Vacío = sin tope.')
    demanda_max_pasadia_mes = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Demanda máxima de pasadías al mes')
    demanda_max_hospedaje_mes = models.PositiveIntegerField(
        null=True, blank=True, verbose_name='Demanda máxima de hospedajes al mes')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado el')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado el')

    class Meta:
        verbose_name = 'Parámetro'
        verbose_name_plural = 'Parámetros'
        ordering = ['-activo', '-vigente_desde', '-id']

    def __str__(self):
        return f'Parámetros desde {self.vigente_desde:%d/%m/%Y}' + ('' if self.activo else ' (inactivos)')

    def clean(self):
        super().clean()
        _validar_dias(self.dias_evento, 'dias_evento')
        _validar_dias(self.dias_pasadia, 'dias_pasadia')
        _validar_dias(self.dias_hospedaje, 'dias_hospedaje')

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.activo:
            type(self).objects.filter(activo=True).exclude(pk=self.pk).update(activo=False)

    @classmethod
    def vigente(cls) -> 'ParametrosNegocio':
        """El juego activo, o uno con los defaults (sin guardar) si el propietario aún no captura nada."""
        return cls.objects.filter(activo=True).order_by('-vigente_desde', '-id').first() or cls()

    def dias_de(self, tipo: str) -> set:
        """Días ISO (1-7, como `int`) en que se vende el servicio `tipo`."""
        cadena = {'EVENTO': self.dias_evento, 'PASADIA': self.dias_pasadia,
                  'HOSPEDAJE': self.dias_hospedaje}[tipo]
        return {int(d) for d in cadena}

    def pct_costo_de(self, tipo: str) -> Decimal:
        return {'EVENTO': self.pct_costo_variable_evento, 'PASADIA': self.pct_costo_variable_pasadia,
                'HOSPEDAJE': self.pct_costo_variable_hospedaje}[tipo]

    def demanda_de(self, tipo: str):
        return {'EVENTO': self.demanda_max_evento_mes, 'PASADIA': self.demanda_max_pasadia_mes,
                'HOSPEDAJE': self.demanda_max_hospedaje_mes}[tipo]


class MetaMensual(models.Model):
    """Meta comercial de un mes. Una activa por mes."""

    anio = models.PositiveSmallIntegerField(verbose_name='Año')
    mes = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(12)], verbose_name='Mes')
    meta_venta_neta = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name='Meta de venta neta')
    meta_ocupacion_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('0.00'), validators=PORCENTAJE,
        verbose_name='Meta de ocupación (%)')
    meta_eventos = models.PositiveIntegerField(default=0, verbose_name='Meta de eventos')
    meta_pasadias = models.PositiveIntegerField(default=0, verbose_name='Meta de pasadías')
    meta_noches = models.PositiveIntegerField(default=0, verbose_name='Meta de noches')
    presupuesto_marketing = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name='Presupuesto de marketing')
    notas = models.TextField(blank=True, verbose_name='Notas')
    activo = models.BooleanField(default=True, verbose_name='Activa')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name='+', verbose_name='Creada por')
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
        on_delete=models.SET_NULL, related_name='+', verbose_name='Actualizada por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada el')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada el')

    class Meta:
        verbose_name = 'Meta'
        verbose_name_plural = 'Metas'
        ordering = ['-anio', '-mes']
        constraints = [
            models.UniqueConstraint(
                fields=['anio', 'mes'], condition=models.Q(activo=True), name='meta_unica_por_mes_activa'),
        ]

    def __str__(self):
        return f'Meta {self.mes:02d}/{self.anio}'

    @classmethod
    def del_mes(cls, anio: int, mes: int):
        return cls.objects.filter(anio=anio, mes=mes, activo=True).first()
