from django.contrib.auth.models import User
from django.db import models

from core_erp.storages_qkt import storage_privado


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
    telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono')
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
    fecha_generacion = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de generación')

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
    archivo_pdf = models.FileField(
        upload_to='nominas_pdf/', blank=True, null=True, storage=storage_privado,
        verbose_name='Archivo PDF',
    )

    class Meta:
        verbose_name = "Recibo"
        verbose_name_plural = "Recibos"

    def __str__(self):
        return f"Pago {self.empleado} - ${self.total_pagado} [{self.estado}]"


class AprobacionNomina(models.Model):
    """Visto bueno de Dirección al total de nómina de una semana (Issue #431).

    Solo deja registro de quién y cuándo: no genera póliza, no mueve dinero ni
    cambia el estado de los recibos. La transferencia sigue siendo en el banco.
    """
    semana_inicio = models.DateField(unique=True, verbose_name='Semana (lunes)')
    semana_fin = models.DateField(verbose_name='Semana (domingo)')
    total = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Total a pagar')
    recibos = models.PositiveIntegerField(default=0, verbose_name='Recibos')
    notificada_en = models.DateTimeField(null=True, blank=True, verbose_name='Avisada a Dirección')
    aprobada_en = models.DateTimeField(null=True, blank=True, verbose_name='Aprobada')
    aprobada_por_nombre = models.CharField(max_length=120, blank=True, verbose_name='Aprobada por')
    aprobada_por_telefono = models.CharField(max_length=20, blank=True, verbose_name='Teléfono que aprobó')
    aprobada_por = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
                                     verbose_name='Usuario que aprobó')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creada')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada')

    class Meta:
        verbose_name = 'Aprobación de nómina'
        verbose_name_plural = 'Aprobaciones'
        ordering = ['-semana_inicio']

    def __str__(self):
        return f'Nómina {self.semana_inicio:%d/%m/%Y} al {self.semana_fin:%d/%m/%Y}'

    @property
    def aprobada(self) -> bool:
        return self.aprobada_en is not None
