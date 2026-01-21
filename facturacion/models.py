from django.db import models
from django.utils.timezone import now
from comercial.models import Cliente, Cotizacion 

# --- IMPORTACIÓN DE CATÁLOGOS SAT (NUEVO) ---
from facturacion.choices import FormaPago, MetodoPago

class SolicitudFactura(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.CASCADE, verbose_name="Cliente")
    
    cotizacion = models.ForeignKey(
        Cotizacion, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        verbose_name="Cotización Origen",
        related_name="solicitudes_factura"
    )
    
    fecha_solicitud = models.DateTimeField(default=now)
    monto = models.DecimalField(max_digits=12, decimal_places=2)
    concepto = models.TextField()
    
    # CAMBIO: Usamos FormaPago del archivo choices.py
    forma_pago = models.CharField(
        max_length=2, 
        choices=FormaPago.choices,
        default=FormaPago.TRANSFERENCIA,
        verbose_name="Forma de Pago"
    )
    
    # CAMBIO: Usamos MetodoPago del archivo choices.py
    metodo_pago = models.CharField(
        max_length=3, 
        choices=MetodoPago.choices,
        default=MetodoPago.PUE,
        verbose_name="Método de Pago"
    )
    
    archivo_pdf = models.FileField(upload_to='solicitudes_pdf/', blank=True, null=True)

    def __str__(self):
        # Usamos try/except o condicional para evitar errores si ID es None al crear
        id_str = f"{int(self.id):03d}" if self.id else "Nueva"
        return f"Solicitud SOL-{id_str} - ${self.monto}"