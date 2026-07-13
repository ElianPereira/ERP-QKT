from django.db import models
from django.contrib.auth.models import User

class Empleado(models.Model):
    # PUESTOS DISPONIBLES
    PUESTOS = [
        ('MESERO', 'Mesero'),
        ('COCINA', 'Cocina/Chef'),
        ('STAFF', 'Staff General'),
        ('CHOFER', 'Chofer'),
        ('SEGURIDAD', 'Seguridad'),
        ('OTRO', 'Otro'),
    ]

    nombre = models.CharField(max_length=200, help_text="Debe coincidir con el nombre en el Excel si usas carga masiva")
    puesto = models.CharField(max_length=20, choices=PUESTOS, default='MESERO') 
    telefono = models.CharField(max_length=20, blank=True) 
    tarifa_base = models.DecimalField(max_digits=10, decimal_places=2, default=50.00)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} ({self.get_puesto_display()})"

class ReciboNomina(models.Model):
    ESTADO_CHOICES = [
        ('CALCULADO', 'Calculado (sin pagar)'),
        ('PAGADO', 'Pagado en efectivo'),
        ('CANCELADO', 'Cancelado'),
    ]

    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE)
    fecha_generacion = models.DateTimeField(auto_now_add=True)

    # Datos leídos del Excel
    periodo = models.CharField(max_length=100)
    horas_trabajadas = models.DecimalField(max_digits=10, decimal_places=2)
    tarifa_aplicada = models.DecimalField(max_digits=10, decimal_places=2)
    total_pagado = models.DecimalField(
        max_digits=10, decimal_places=2,
        verbose_name="Total a pagar"
    )

    # Solo control administrativo — sin ningún vínculo con contabilidad.
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='CALCULADO')
    fecha_pago = models.DateField(null=True, blank=True, verbose_name="Fecha de pago en efectivo")
    pagado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='nominas_pagadas_por', verbose_name="Pagado por"
    )

    # El archivo PDF generado
    archivo_pdf = models.FileField(upload_to='nominas_pdf/', blank=True, null=True)

    class Meta:
        verbose_name = "Recibo"
        verbose_name_plural = "Recibos"

    def __str__(self):
        return f"Pago {self.empleado} - ${self.total_pagado} [{self.estado}]"