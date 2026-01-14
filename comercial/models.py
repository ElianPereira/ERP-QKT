from django.db import models
from django.db.models import Sum

# 1. INSUMOS (Hielo, DJ, Mobiliario)
class Insumo(models.Model):
    nombre = models.CharField(max_length=200)
    unidad_medida = models.CharField(max_length=50) # Ej: kg, horas, pza
    costo_unitario = models.DecimalField(max_digits=10, decimal_places=2) 

    def __str__(self):
        return f"{self.nombre} (${self.costo_unitario})"

# 2. PRODUCTOS (Paquetes)
class Producto(models.Model):
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    margen_ganancia = models.DecimalField(max_digits=4, decimal_places=2, default=0.30, help_text="Ej: 0.30 para 30%")
    
    def calcular_costo(self):
        return sum(c.subtotal_costo() for c in self.componentes.all())

    def sugerencia_precio(self):
        costo = self.calcular_costo()
        return round(costo * (1 + self.margen_ganancia), 2)

    def __str__(self):
        return self.nombre

# Receta del Producto (Qué insumos lleva)
class ComponenteProducto(models.Model):
    producto = models.ForeignKey(Producto, related_name='componentes', on_delete=models.CASCADE)
    insumo = models.ForeignKey(Insumo, on_delete=models.PROTECT)
    cantidad = models.DecimalField(max_digits=10, decimal_places=2)

    def subtotal_costo(self):
        return self.insumo.costo_unitario * self.cantidad

# 3. CLIENTES
class Cliente(models.Model):
    nombre = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=20, blank=True)
    
    def __str__(self):
        return self.nombre

# 4. COTIZACIONES (Ventas)
class Cotizacion(models.Model):
    ESTADOS = [
        ('BORRADOR', 'Borrador'),
        ('CONFIRMADA', 'Venta Confirmada'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE)
    producto = models.ForeignKey(Producto, on_delete=models.PROTECT)
    fecha_evento = models.DateField()
    precio_final = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(max_length=20, choices=ESTADOS, default='BORRADOR')
    created_at = models.DateTimeField(auto_now_add=True)

    def total_pagado(self):
        resultado = self.pagos.aggregate(Sum('monto'))['monto__sum']
        return resultado if resultado else 0

    def saldo_pendiente(self):
        return self.precio_final - self.total_pagado()

    def __str__(self):
        return f"Evento {self.cliente} - {self.fecha_evento}"

# 5. PAGOS
class Pago(models.Model):
    METODOS = [('EFECTIVO', 'Efectivo'), ('TRANSFERENCIA', 'Transferencia')]
    
    cotizacion = models.ForeignKey(Cotizacion, related_name='pagos', on_delete=models.CASCADE)
    fecha_pago = models.DateField(auto_now_add=True)
    monto = models.DecimalField(max_digits=10, decimal_places=2)
    metodo = models.CharField(max_length=20, choices=METODOS)
    referencia = models.CharField(max_length=100, blank=True)
    
    def __str__(self):
        return f"${self.monto} - {self.cotizacion}"