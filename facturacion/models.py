from django.db import models
from django.utils.timezone import now
from comercial.models import Cliente, Cotizacion 

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
    
    FORMA_PAGO_CHOICES = [
        ('03 - Transferencia electrónica de fondos', '03 - Transferencia'),
        ('01 - Efectivo', '01 - Efectivo'),
        ('04 - Tarjeta de crédito', '04 - Tarjeta de crédito'),
        ('28 - Tarjeta de débito', '28 - Tarjeta de débito'),
        ('99 - Por definir', '99 - Por definir'),
    ]
    forma_pago = models.CharField(max_length=100, choices=FORMA_PAGO_CHOICES)
    
    METODO_PAGO_CHOICES = [
        ('PUE - Pago en una sola exhibición', 'PUE - Una sola exhibición'),
        ('PPD - Pago en parcialidades o diferido', 'PPD - Parcialidades'),
    ]
    metodo_pago = models.CharField(max_length=100, choices=METODO_PAGO_CHOICES)
    
    archivo_pdf = models.FileField(upload_to='solicitudes_pdf/', blank=True, null=True)

    def __str__(self):
        # Usamos try/except o condicional para evitar errores si ID es None al crear
        id_str = f"{int(self.id):03d}" if self.id else "Nueva"
        return f"Solicitud SOL-{id_str} - ${self.monto}"