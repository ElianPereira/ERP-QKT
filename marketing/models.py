"""Prospectos (leads) y su bitácora (Issue #468, fase 1).

Marketing nunca copia montos: un prospecto guarda llaves a la conversación,
el cliente y la cotización, y todo importe se lee de ahí. La etapa la deriva
`marketing.services.pipeline` de los datos que ya existen; nadie la captura.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Lead(models.Model):
    """Una oportunidad de venta: alguien que escribió o cotizó.

    Un mismo teléfono puede tener varios prospectos en el tiempo (vuelve un
    año después por otro evento), pero solo uno abierto a la vez.
    """
    NUEVO = 'NUEVO'
    CONTACTADO = 'CONTACTADO'
    VISITA_AGENDADA = 'VISITA_AGENDADA'
    COTIZADO = 'COTIZADO'
    GANADO = 'GANADO'
    PERDIDO = 'PERDIDO'
    ETAPA_CHOICES = [
        (NUEVO, 'Nuevo'),
        (CONTACTADO, 'Contactado'),
        (VISITA_AGENDADA, 'Visita agendada'),
        (COTIZADO, 'Cotizado'),
        (GANADO, 'Ganado'),
        (PERDIDO, 'Perdido'),
    ]
    CERRADAS = (GANADO, PERDIDO)
    # Orden del embudo: una etapa solo avanza hacia una de rango mayor.
    RANGO = {NUEVO: 0, CONTACTADO: 1, VISITA_AGENDADA: 2, COTIZADO: 3, GANADO: 4}

    FUENTE_CHOICES = [
        ('META_ANUNCIO', 'Anuncio de Meta'),
        ('FACEBOOK', 'Facebook'),
        ('INSTAGRAM', 'Instagram'),
        ('TIKTOK', 'TikTok'),
        ('GOOGLE', 'Google'),
        ('RECOMENDACION', 'Recomendación'),
        ('RECURRENTE', 'Cliente recurrente'),
        ('VISITA_QUINTA', 'Visitó la Quinta'),
        ('WHATSAPP', 'WhatsApp directo'),
        ('OTRO', 'Otro'),
    ]
    # Fuentes que no dicen de dónde vino: se reemplazan si después llega un
    # dato mejor (p. ej. el «Cómo nos encontró» de una cotización).
    FUENTES_GENERICAS = ('WHATSAPP', 'OTRO')

    MOTIVO_CHOICES = [
        ('PRECIO', 'Precio'),
        ('FECHA', 'Fecha no disponible'),
        ('AFORO', 'Aforo o espacio'),
        ('COMPETENCIA', 'Eligió otro lugar'),
        ('SIN_RESPUESTA', 'Dejó de responder'),
        ('CANCELO', 'Canceló'),
        ('OTRO', 'Otro'),
    ]
    ORIGEN_CHOICES = [
        ('SISTEMA', 'Sistema'),
        ('KOOXI', 'Kooxi'),
        ('USUARIO', 'Usuario'),
    ]

    telefono = models.CharField(max_length=20, blank=True, db_index=True, verbose_name='Teléfono')
    nombre = models.CharField(max_length=200, blank=True, verbose_name='Nombre')
    fuente = models.CharField(max_length=15, choices=FUENTE_CHOICES, default='OTRO', db_index=True,
                              verbose_name='Fuente')
    anuncio_id = models.CharField(max_length=100, blank=True, db_index=True, verbose_name='Anuncio (id)')
    anuncio_titulo = models.CharField(max_length=300, blank=True, verbose_name='Anuncio')
    anuncio_clid = models.CharField(max_length=300, blank=True, verbose_name='Clic en el anuncio (ctwa_clid)')
    etapa = models.CharField(max_length=16, choices=ETAPA_CHOICES, default=NUEVO, verbose_name='Etapa')
    tipo_servicio = models.CharField(max_length=15, blank=True, verbose_name='Tipo de servicio')
    motivo_perdida = models.CharField(max_length=15, choices=MOTIVO_CHOICES, blank=True,
                                      verbose_name='Motivo de pérdida')
    motivo_perdida_origen = models.CharField(max_length=8, choices=ORIGEN_CHOICES, blank=True,
                                             verbose_name='Motivo según')
    conversacion = models.ForeignKey('comunicacion.ConversacionWhatsApp', on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='prospectos',
                                     verbose_name='Conversación')
    cliente = models.ForeignKey('comercial.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='prospectos', verbose_name='Cliente')
    cotizacion = models.ForeignKey('comercial.Cotizacion', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='prospectos', verbose_name='Cotización')
    fecha_primer_contacto = models.DateTimeField(default=timezone.now, db_index=True,
                                                 verbose_name='Primer contacto')
    fecha_ultima_interaccion = models.DateTimeField(default=timezone.now, verbose_name='Última interacción')
    etapa_cambiada_en = models.DateTimeField(default=timezone.now, verbose_name='Etapa desde')
    anonimizado_en = models.DateTimeField(null=True, blank=True, verbose_name='Anonimizado el')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Prospecto'
        verbose_name_plural = 'Prospectos'
        ordering = ['-fecha_primer_contacto']
        indexes = [models.Index(fields=['etapa', 'fecha_primer_contacto'], name='mkt_lead_etapa_fecha')]
        constraints = [
            models.UniqueConstraint(
                fields=['telefono'], name='mkt_lead_un_abierto_por_telefono',
                condition=~Q(etapa__in=['GANADO', 'PERDIDO']) & ~Q(telefono=''),
            ),
        ]

    def __str__(self):
        return f"{self.nombre or 'Prospecto'} ({self.get_etapa_display()})"

    @property
    def abierto(self) -> bool:
        return self.etapa not in self.CERRADAS


class LeadEvento(models.Model):
    """Bitácora inmutable del prospecto. Sin datos personales en `detalle`."""
    TIPO_CHOICES = [
        ('CREADO', 'Creado'),
        ('ETAPA', 'Cambio de etapa'),
        ('REAPERTURA', 'Reabierto'),
        ('FUENTE', 'Fuente corregida'),
        ('MOTIVO', 'Motivo de pérdida'),
        ('ANONIMIZADO', 'Anonimizado'),
    ]
    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, related_name='eventos', verbose_name='Prospecto')
    tipo = models.CharField(max_length=12, choices=TIPO_CHOICES, verbose_name='Tipo')
    etapa_anterior = models.CharField(max_length=16, blank=True, verbose_name='Etapa anterior')
    etapa_nueva = models.CharField(max_length=16, blank=True, verbose_name='Etapa nueva')
    origen = models.CharField(max_length=8, choices=Lead.ORIGEN_CHOICES, default='SISTEMA', verbose_name='Origen')
    detalle = models.JSONField(default=dict, blank=True, verbose_name='Detalle')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Registrado por')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Fecha')

    class Meta:
        verbose_name = 'Evento del prospecto'
        verbose_name_plural = 'Eventos'
        ordering = ['created_at', 'id']

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.etapa_nueva or ""}'.strip(' ·')

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('La bitácora del prospecto no se edita.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('La bitácora del prospecto no se borra.')
