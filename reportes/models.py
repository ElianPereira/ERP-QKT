"""
Modelos del Módulo de Reportes
==============================
Historial de reportes generados para auditoría.
ERP Quinta Ko'ox Tanil
"""
from django.db import models
from django.contrib.auth.models import User


class ReporteGenerado(models.Model):
    """
    Registro de auditoría de cada reporte generado.
    No almacena el PDF, solo el registro de quién lo pidió y cuándo.
    """
    TIPO_CHOICES = [
        # Contabilidad
        ('BALANZA', 'Balanza de Comprobación'),
        ('EDO_RESULTADOS', 'Estado de Resultados'),
        ('BALANCE_GRAL', 'Balance General'),
        ('LIBRO_MAYOR', 'Libro Mayor'),
        ('AUXILIAR', 'Auxiliar de Cuentas'),
        # Comercial
        ('CXC_CARTERA', 'CxC / Antigüedad de Saldos'),
        ('COT_PERIODO', 'Cotizaciones por Período'),
        # Airbnb
        ('OCUPACION', 'Ocupación por Listing'),
        ('COMPARATIVO', 'Comparativo Mensual Airbnb'),
        # Facturación
        ('FACTURAS', 'Facturas Emitidas'),
    ]
    FORMATO_CHOICES = [
        ('PDF', 'PDF'),
        ('HTML', 'Vista en Pantalla'),
    ]

    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, verbose_name="Tipo de Reporte")
    formato = models.CharField(max_length=10, choices=FORMATO_CHOICES, default='PDF')
    fecha_inicio = models.DateField(verbose_name="Fecha Inicio del Período")
    fecha_fin = models.DateField(verbose_name="Fecha Fin del Período")
    parametros = models.JSONField(
        default=dict, blank=True,
        verbose_name="Parámetros",
        help_text="Filtros aplicados (unidad negocio, cuenta, listing, etc.)"
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        verbose_name="Generado por"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Fecha de Generación")

    class Meta:
        verbose_name = "Reporte Generado"
        verbose_name_plural = "Reportes Generados"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_tipo_display()} | {self.fecha_inicio} → {self.fecha_fin}"
