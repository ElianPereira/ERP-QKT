import xml.etree.ElementTree as ET
from decimal import Decimal
from django.db import models, transaction
from django.db.models import F, Sum
from django.utils.timezone import now
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from facturacion.choices import RegimenFiscal, UsoCFDI
from cloudinary_storage.storage import RawMediaCloudinaryStorage
import secrets

# ==========================================
# 0. CONFIGURACIÓN DEL SISTEMA
# ==========================================
class ConstanteSistema(models.Model):
    clave = models.CharField(max_length=50, unique=True, help_text="Ej: PRECIO_HIELO_20KG")
    valor = models.DecimalField(max_digits=10, decimal_places=2)
    descripcion = models.CharField(max_length=200, blank=True)

    def __str__(self): return f"{self.clave}: ${self.valor}"
    class Meta: verbose_name = "Constante del Sistema"


# ==========================================
# 0.5 PROVEEDORES
# ==========================================
class Proveedor(models.Model):
    nombre = models.CharField(max_length=200, unique=True, verbose_name="Nombre / Razón Social")
    contacto = models.CharField(max_length=200, blank=True, verbose_name="Persona de Contacto")
    telefono = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    notas = models.TextField(blank=True, verbose_name="Notas",
                             help_text="Horarios, condiciones de pago, dirección, etc.")
    activo = models.BooleanField(default=True)

    def __str__(self): return self.nombre
    class Meta:
        verbose_name = "Proveedor"
        verbose_name_plural = "Proveedores"
        ordering = ['nombre']


# PLACEHOLDER