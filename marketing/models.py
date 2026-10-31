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

    @property
    def anuncio(self):
        """Anuncio sincronizado de la pauta al que corresponde `anuncio_id` (o None)."""
        if not self.anuncio_id:
            return None
        return Anuncio.objects.filter(id_externo=self.anuncio_id).select_related('campana').first()


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


SERVICIO_CHOICES = [('EVENTO', 'Evento'), ('PASADIA', 'Pasadía'), ('HOSPEDAJE', 'Hospedaje')]


# === Pauta publicitaria (fase 2) ===
class Campana(models.Model):
    """Campaña de una plataforma de anuncios. Lo que viene de la plataforma
    (nombre, estado, presupuesto) lo sobrescribe la sincronización; `objetivo`
    y `tipo_servicio` los captura el propietario para atribuir la pauta."""
    META = 'META'
    GOOGLE = 'GOOGLE'
    PLATAFORMA_CHOICES = [(META, 'Meta'), (GOOGLE, 'Google')]
    OBJETIVO_CHOICES = [
        ('PREMIUM', 'Premium'), ('CORPORATIVOS', 'Corporativos'),
        ('CELEBRACIONES', 'Celebraciones'), ('PASADIA', 'Pasadía'),
    ]

    plataforma = models.CharField(max_length=6, choices=PLATAFORMA_CHOICES, default=META, verbose_name='Plataforma')
    id_externo = models.CharField(max_length=100, verbose_name='Identificador en la plataforma')
    nombre = models.CharField(max_length=300, verbose_name='Nombre')
    objetivo = models.CharField(max_length=14, choices=OBJETIVO_CHOICES, blank=True, verbose_name='Objetivo')
    tipo_servicio = models.CharField(max_length=15, choices=SERVICIO_CHOICES, blank=True,
                                     verbose_name='Servicio que promueve',
                                     help_text='Con él se atribuye la pauta a EVENTO, PASADIA o HOSPEDAJE.')
    objetivo_plataforma = models.CharField(max_length=60, blank=True, verbose_name='Objetivo en la plataforma')
    estado = models.CharField(max_length=40, blank=True, verbose_name='Estado')
    presupuesto_diario = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                             verbose_name='Presupuesto diario')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')

    class Meta:
        verbose_name = 'Campaña'
        verbose_name_plural = 'Campañas'
        ordering = ['-updated_at']
        constraints = [models.UniqueConstraint(fields=['plataforma', 'id_externo'], name='mkt_campana_unica')]

    def __str__(self):
        return self.nombre or self.id_externo


class Anuncio(models.Model):
    """Anuncio de una campaña. `id_externo` es el mismo que guarda
    `Lead.anuncio_id` cuando el prospecto llegó por ese anuncio."""
    campana = models.ForeignKey(Campana, on_delete=models.PROTECT, related_name='anuncios', verbose_name='Campaña')
    id_externo = models.CharField(max_length=100, verbose_name='Identificador en la plataforma')
    nombre = models.CharField(max_length=300, blank=True, verbose_name='Nombre')
    estado = models.CharField(max_length=40, blank=True, verbose_name='Estado')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')

    class Meta:
        verbose_name = 'Anuncio'
        verbose_name_plural = 'Anuncios'
        ordering = ['-updated_at']
        constraints = [models.UniqueConstraint(fields=['campana', 'id_externo'], name='mkt_anuncio_unico')]

    def __str__(self):
        return self.nombre or self.id_externo

    @property
    def prospectos(self):
        """Prospectos que llegaron por este anuncio (liga por `Lead.anuncio_id`)."""
        return Lead.objects.filter(anuncio_id=self.id_externo)


class GastoPublicitario(models.Model):
    """Gasto de un día, sin IVA y en MXN tal como lo reporta la plataforma.
    A nivel anuncio (`anuncio` lleno) o a nivel campaña (`anuncio` vacío)."""
    META_API = 'META_API'
    WHATSAPP_TEMPLATES = 'WHATSAPP_TEMPLATES'
    MANUAL = 'MANUAL'
    FUENTE_CHOICES = [(META_API, 'API de Meta'), (WHATSAPP_TEMPLATES, 'Plantillas de WhatsApp'),
                      (MANUAL, 'Captura manual')]
    PENDIENTE = 'PENDIENTE'
    CONCILIADO = 'CONCILIADO'
    DIFERENCIA = 'DIFERENCIA'
    CONCILIACION_CHOICES = [(PENDIENTE, 'Pendiente'), (CONCILIADO, 'Conciliado'), (DIFERENCIA, 'Con diferencia')]

    fecha = models.DateField(db_index=True, verbose_name='Fecha')
    campana = models.ForeignKey(Campana, on_delete=models.PROTECT, related_name='gastos', verbose_name='Campaña')
    anuncio = models.ForeignKey(Anuncio, on_delete=models.PROTECT, null=True, blank=True, related_name='gastos',
                                verbose_name='Anuncio')
    monto = models.DecimalField(max_digits=14, decimal_places=2, verbose_name='Monto (MXN, sin IVA)')
    impresiones = models.PositiveIntegerField(default=0, verbose_name='Impresiones')
    clics = models.PositiveIntegerField(default=0, verbose_name='Clics')
    frecuencia = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, verbose_name='Frecuencia')
    fuente = models.CharField(max_length=20, choices=FUENTE_CHOICES, default=META_API, verbose_name='Fuente')
    estado_conciliacion = models.CharField(max_length=10, choices=CONCILIACION_CHOICES, default=PENDIENTE,
                                           db_index=True, verbose_name='Conciliación')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')

    class Meta:
        verbose_name = 'Gasto publicitario'
        verbose_name_plural = 'Pauta'
        ordering = ['-fecha', 'campana_id']
        constraints = [
            models.UniqueConstraint(fields=['anuncio', 'fecha', 'fuente'], name='mkt_gasto_anuncio_dia',
                                    condition=Q(anuncio__isnull=False)),
            models.UniqueConstraint(fields=['campana', 'fecha', 'fuente'], name='mkt_gasto_campana_dia',
                                    condition=Q(anuncio__isnull=True)),
        ]

    def clean(self):
        if self.anuncio_id and self.anuncio.campana_id != self.campana_id:
            raise ValidationError({'anuncio': 'El anuncio no pertenece a esa campaña.'})

    def __str__(self):
        return f'{self.campana} {self.fecha:%d/%m/%Y} ${self.monto}'
