"""
Catálogos dinámicos del ERP.

Cada modelo aquí representa un catálogo que antes estaba hardcodeado
en choices. La app está pensada para crecer: cuando otro módulo necesite
un catálogo editable, se agrega un modelo aquí y se registra en admin.
"""
from django.db import models


class CategoriaGasto(models.Model):
    """
    Categorías para clasificar los conceptos de compra (Gastos).
    Reemplaza la lista hardcodeada Gasto.CATEGORIAS.
    """
    clave = models.CharField(
        max_length=30,
        unique=True,
        verbose_name="Clave",
        help_text="Identificador corto (ej: LIMPIEZA, BEBIDAS_CON_ALCOHOL)",
    )
    nombre = models.CharField(
        max_length=100,
        verbose_name="Nombre",
        help_text="Nombre visible en dropdowns y reportes",
    )
    activa = models.BooleanField(
        default=True,
        verbose_name="Activa",
        help_text="Las categorías inactivas no aparecen en el dropdown pero se conservan en gastos históricos",
    )
    orden = models.PositiveSmallIntegerField(
        default=0,
        verbose_name="Orden",
        help_text="Menor número = aparece primero en la lista",
    )

    class Meta:
        verbose_name = "Categoría de Gasto"
        verbose_name_plural = "Categorías de Gasto"
        ordering = ["orden", "nombre"]

    def __str__(self):
        return self.nombre
