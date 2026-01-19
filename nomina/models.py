from django.db import models

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
    puesto = models.CharField(max_length=20, choices=PUESTOS, default='MESERO') # NUEVO
    telefono = models.CharField(max_length=20, blank=True) # NUEVO
    tarifa_base = models.DecimalField(max_digits=10, decimal_places=2, default=50.00)
    activo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nombre} ({self.get_puesto_display()})"

class ReciboNomina(models.Model):
    empleado = models.ForeignKey(Empleado, on_delete=models.CASCADE)
    fecha_generacion = models.DateTimeField(auto_now_add=True)
    
    # Datos leídos del Excel
    periodo = models.CharField(max_length=100)
    horas_trabajadas = models.DecimalField(max_digits=10, decimal_places=2)
    tarifa_aplicada = models.DecimalField(max_digits=10, decimal_places=2)
    total_pagado = models.DecimalField(max_digits=10, decimal_places=2)
    
    # El archivo PDF generado
    archivo_pdf = models.FileField(upload_to='nominas_pdf/', blank=True, null=True)

    def __str__(self):
        return f"Pago {self.empleado} - ${self.total_pagado}"