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
        ('FACTURA', 'Factura emitida'),
        ('AGENTE_IA', 'Respuesta del agente de WhatsApp'),
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
        related_name='comunicaciones', null=True, blank=True,
        verbose_name='Cotización',
    )
    pago = models.ForeignKey(
        'comercial.Pago', on_delete=models.SET_NULL,
        related_name='comunicaciones', null=True, blank=True
    )
    canal = models.CharField(max_length=15, choices=CANAL_CHOICES)
    tipo = models.CharField(max_length=25, choices=TIPO_CHOICES)
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default='PENDIENTE')
    trigger = models.CharField(max_length=10, choices=TRIGGER_CHOICES, default='MANUAL', verbose_name='Disparador')

    destinatario = models.CharField(max_length=200, help_text="Email, teléfono o URL")
    asunto = models.CharField(max_length=255, blank=True)
    cuerpo = models.TextField(blank=True)
    error = models.TextField(blank=True)

    fecha_envio = models.DateTimeField(default=timezone.now, verbose_name='Fecha de envío')
    fecha_entrega = models.DateTimeField(null=True, blank=True)
    fecha_apertura = models.DateTimeField(null=True, blank=True)

    proveedor_id = models.CharField(max_length=100, blank=True,
                                     help_text="ID externo (Brevo, WhatsApp, etc.)", verbose_name='ID del proveedor')

    # Identifica de forma única el par (evento de negocio, canal). El índice
    # único es la reserva: se inserta la fila ANTES de enviar y un IntegrityError
    # significa "ya se envió". Es lo que impide duplicados cuando un signal corre
    # dos veces, el cron repite o Railway reinicia a media ejecución.
    # Formato: cotizacion:{id}:web:email · pago:{id}:whatsapp ·
    #          recordatorio:{parcialidad_id}:{YYYY-MM-DD}:email
    # Nulo en los envíos manuales, donde repetir es legítimo (NULL no colisiona
    # en un índice único ni en PostgreSQL ni en SQLite).
    clave_idempotencia = models.CharField(
        max_length=191, null=True, blank=True, unique=True,
        verbose_name="Clave de idempotencia",
        help_text="Evita envíos duplicados del mismo evento por el mismo canal.",
    )

    class Meta:
        verbose_name = "Comunicación con cliente"
        verbose_name_plural = "Bitácora"
        ordering = ['-fecha_envio']
        indexes = [
            models.Index(fields=['cotizacion', '-fecha_envio']),
            models.Index(fields=['estado', 'canal']),
        ]

    def __str__(self):
        return f"[{self.canal}/{self.tipo}] → {self.destinatario}"


class ConversacionWhatsApp(models.Model):
    """Conversación del agente de WhatsApp con un número (Issue #346).

    `historial` es exactamente lo que se le manda a la API de Claude y solo
    crece (append-only): el modelo invalida su razonamiento previo si una
    parte ya enviada cambia. Se reinicia entero, nunca a medias, cuando la
    conversación lleva más de 24 h sin mensajes.
    """
    telefono = models.CharField(max_length=20, unique=True, verbose_name='Teléfono')
    nombre = models.CharField(max_length=200, blank=True, verbose_name='Nombre en WhatsApp')
    cliente = models.ForeignKey(
        'comercial.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='conversaciones_whatsapp', verbose_name='Cliente',
    )
    historial = models.JSONField(default=list, blank=True, verbose_name='Historial para la IA')
    requiere_humano = models.BooleanField(
        default=False, verbose_name='Requiere humano',
        help_text='El agente pidió ayuda y dejó de contestar. Desmárcalo para reactivarlo.',
    )
    motivo_humano = models.CharField(max_length=300, blank=True, verbose_name='Motivo')
    pausado_hasta = models.DateTimeField(
        null=True, blank=True, verbose_name='Agente en pausa hasta',
        help_text='Se llena solo cuando alguien contesta desde la app de WhatsApp.',
    )
    ultimo_mensaje = models.DateTimeField(default=timezone.now, verbose_name='Último mensaje')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Conversación de WhatsApp'
        verbose_name_plural = 'Conversaciones'
        ordering = ['-ultimo_mensaje']

    def __str__(self):
        return f"{self.nombre or 'Sin nombre'} ({self.telefono})"

    def agente_en_pausa(self, ahora=None) -> bool:
        ahora = ahora or timezone.now()
        return self.requiere_humano or bool(self.pausado_hasta and self.pausado_hasta > ahora)


class MensajeWhatsApp(models.Model):
    """Bitácora legible de la conversación: lo que escribió el cliente, lo que
    contestó el agente y lo que contestó una persona desde la app."""
    DIRECCION_CHOICES = [
        ('ENTRADA', 'Cliente'),
        ('AGENTE', 'Agente IA'),
        ('HUMANO', 'Persona (app)'),
    ]
    conversacion = models.ForeignKey(
        ConversacionWhatsApp, on_delete=models.CASCADE, related_name='mensajes',
        verbose_name='Conversación',
    )
    direccion = models.CharField(max_length=10, choices=DIRECCION_CHOICES, verbose_name='Quién')
    texto = models.TextField(blank=True, verbose_name='Texto')
    # ID de Meta (wamid). Único: Meta reintenta entregas y un mensaje repetido
    # no debe contestarse dos veces.
    wamid = models.CharField(max_length=191, null=True, blank=True, unique=True, verbose_name='ID de Meta')
    procesado = models.BooleanField(default=False, verbose_name='Procesado')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Mensaje de WhatsApp'
        verbose_name_plural = 'Mensajes'
        ordering = ['created_at', 'id']
        indexes = [models.Index(fields=['conversacion', 'procesado'])]

    def __str__(self):
        return f"[{self.get_direccion_display()}] {self.texto[:60]}"
