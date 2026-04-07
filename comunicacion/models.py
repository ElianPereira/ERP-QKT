"""
Modelo unificado de comunicaciones salientes con clientes.
Registra cada email/WhatsApp/SMS/notificación enviado.
"""
from django.db import models
from django.utils import timezone


class ComunicacionCliente(models.Model):
    CANAL_CHOICES = [
        ('EMAIL', 'Email'),
        ('WHATSAPP', 'WhatsApp'),
        ('SMS', 'SMS'),
        ('PORTAL', 'Notificación en portal'),
    ]
    TIPO_CHOICES = [
        ('COTIZACION', 'Cotización enviada'),
        ('CONFIRMACION_PAGO', 'Confirmación de pago'),
        ('REEMBOLSO', 'Notificación de reembolso'),
        ('RECORDATORIO_PAGO', 'Recordatorio de pago'),
        ('CONTRATO', 'Contrato'),
        ('EVENTO_PROXIMO', 'Evento próximo'),
        ('CANCELACION', 'Cancelación'),
        ('OTRO', 'Otro'),
    ]
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente'),
        ('ENVIADO', 'Enviado'),
        ('ENTREGADO', 'Entregado'),
        ('ABIERTO', 'Abierto / leído'),
        ('FALLIDO', 'Fallido'),
    ]
    TRIGGER_CHOICES = [
        ('MANUAL', 'Manual'),
        ('SIGNAL', 'Signal automático'),
        ('CRON', 'Tarea programada'),
    ]

    cotizacion = models.ForeignKey(
        'comercial.Cotizacion', on_delete=models.CASCADE,
        related_name='comunicaciones', null=True, blank=True
    )
    pago = models.ForeignKey(
        'comercial.Pago', on_delete=models.SET_NULL,
        related_name='comunicaciones', null=True, blank=True
    )
    canal = models.CharField(max_length=15, choices=CANAL_CHOICES)
    tipo = models.CharField(max_length=25, choices=TIPO_CHOICES)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default='PENDIENTE')
    trigger = models.CharField(max_length=10, choices=TRIGGER_CHOICES, default='MANUAL')

    destinatario = models.CharField(max_length=200, help_text="Email, teléfono o URL")
    asunto = models.CharField(max_length=255, blank=True)
    cuerpo = models.TextField(blank=True)
    error = models.TextField(blank=True)

    fecha_envio = models.DateTimeField(default=timezone.now)
    fecha_entrega = models.DateTimeField(null=True, blank=True)
    fecha_apertura = models.DateTimeField(null=True, blank=True)

    proveedor_id = models.CharField(max_length=100, blank=True,
                                     help_text="ID externo (Brevo, WhatsApp, etc.)")

    class Meta:
        verbose_name = "Comunicación con cliente"
        verbose_name_plural = "Comunicaciones con clientes"
        ordering = ['-fecha_envio']
        indexes = [
            models.Index(fields=['cotizacion', '-fecha_envio']),
            models.Index(fields=['estado', 'canal']),
        ]

    def __str__(self):
        return f"[{self.canal}/{self.tipo}] → {self.destinatario}"
