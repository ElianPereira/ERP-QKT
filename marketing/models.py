"""Prospectos (leads) y su bitácora (Issue #468, fase 1).

Marketing nunca copia montos: un prospecto guarda llaves a la conversación,
el cliente y la cotización, y todo importe se lee de ahí. La etapa la deriva
`marketing.services.pipeline` de los datos que ya existen; nadie la captura.
"""
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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
        ('GOOGLE_ANUNCIO', 'Anuncio de Google'),
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
    gclid = models.CharField(max_length=300, blank=True, verbose_name='Clic en Google Ads (gclid)')
    gbraid = models.CharField(max_length=300, blank=True, verbose_name='Clic en Google Ads iOS (gbraid)')
    wbraid = models.CharField(max_length=300, blank=True, verbose_name='Clic en Google Ads web a app (wbraid)')
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
    recomendado_por = models.ForeignKey('comercial.Cliente', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='prospectos_recomendados', verbose_name='Lo recomendó')
    recomendado_por_texto = models.CharField(max_length=120, blank=True, verbose_name='Recomendado por (lo que dijo)')
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
    GOOGLE_API = 'GOOGLE_API'
    WHATSAPP_TEMPLATES = 'WHATSAPP_TEMPLATES'
    MANUAL = 'MANUAL'
    FUENTE_CHOICES = [(META_API, 'API de Meta'), (GOOGLE_API, 'API de Google Ads'), (WHATSAPP_TEMPLATES, 'Plantillas de WhatsApp'),
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


# === Parámetros e indicadores (fase 3) ===
def prob_etapa_inicial() -> dict:
    """Probabilidad de ganar según la etapa del embudo (0 a 1, como texto). Supuesto del propietario:
    se calibra con el histórico cuando haya cohortes maduras."""
    return {'NUEVO': '0.02', 'CONTACTADO': '0.05', 'VISITA_AGENDADA': '0.20', 'COTIZADO': '0.30',
            'GANADO': '1', 'PERDIDO': '0'}


class ParametroMarketing(models.Model):
    """Supuestos del propietario para medir y decidir. Uno activo; al guardar uno nuevo activo, los demás
    se desactivan (queda el historial). Sin ninguno, `vigente()` devuelve los valores de fábrica."""
    PCT = [MinValueValidator(Decimal('0')), MaxValueValidator(Decimal('100'))]

    vigente_desde = models.DateField(default=timezone.localdate, verbose_name='Vigente desde')
    activo = models.BooleanField(default=True, verbose_name='Activo')
    pct_reinversion = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('30.00'), validators=PCT,
        verbose_name='Reinversión (%)',
        help_text='Qué parte del margen de contribución de una venta puedes gastar en conseguirla. '
                  'Con ella se calcula el tope de CAC de cada servicio.')
    cac_tope_manual = models.JSONField(
        null=True, blank=True, verbose_name='Tope de CAC manual',
        help_text='Opcional. Un objeto con el tope en pesos por servicio, por ejemplo '
                  '{"EVENTO": "2500", "PASADIA": "300"}. Un servicio sin tope manual usa margen × reinversión.')
    muestra_minima_leads = models.PositiveIntegerField(
        default=10, verbose_name='Muestra mínima (prospectos)',
        help_text='Con menos prospectos que esto, las razones (CPL, CAC, ROAS, cierre) se muestran como '
                  '«muestra insuficiente» en vez de una cifra engañosa.')
    ventana_dias = models.PositiveIntegerField(
        default=90, validators=[MinValueValidator(7), MaxValueValidator(365)], verbose_name='Ventana (días)',
        help_text='Días hacia atrás que cubre cada instantánea diaria y la lectura de la pantalla.')
    cpl_max = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, verbose_name='CPL máximo',
        help_text='Costo por prospecto a partir del cual una campaña merece revisión. Vacío = sin tope.')
    frecuencia_max = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('3.50'), verbose_name='Frecuencia máxima',
        help_text='Veces promedio que una persona ve el anuncio antes de considerarlo saturado.')
    umbral_ocupacion_pausa = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('85.00'), validators=PCT,
        verbose_name='Ocupación para pausar (%)',
        help_text='Con la agenda llena a este nivel o más, conviene pausar la pauta del servicio.')
    dias_sin_respuesta = models.PositiveIntegerField(
        default=15, validators=[MinValueValidator(1)], verbose_name='Días sin respuesta',
        help_text='Un prospecto abierto sin interacción este número de días pasa a Perdido.')
    pausa_gasto_minimo = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal('1500.00'), verbose_name='Gasto para pausar un anuncio',
        help_text='Un anuncio que gastó esto (sin IVA) en los días de la ventana de pausa y no produjo '
                  'ninguna cotización se propone pausarlo.')
    pausa_dias = models.PositiveIntegerField(
        default=14, validators=[MinValueValidator(3), MaxValueValidator(60)], verbose_name='Días para pausar un anuncio',
        help_text='Ventana en la que se mide el gasto y las cotizaciones del anuncio.')
    reasignar_diferencia_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('30.00'), validators=PCT,
        verbose_name='Diferencia de CAC para reasignar (%)',
        help_text='Se propone mover presupuesto cuando el CAC de una campaña supera al de otra por este porcentaje.')
    escalar_ocupacion_max = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('60.00'), validators=PCT,
        verbose_name='Ocupación máxima para escalar (%)',
        help_text='Solo se propone subir presupuesto si la agenda de los próximos 60 días está por debajo de esto.')
    fines_libres_min = models.PositiveIntegerField(
        default=4, validators=[MinValueValidator(1)], verbose_name='Días de fin de semana libres para subir pauta',
        help_text='Sábados y domingos libres en los próximos 45 días a partir de los cuales se propone subir pauta.')
    concentracion_max_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('60.00'), validators=PCT,
        verbose_name='Concentración máxima en un canal (%)',
        help_text='Se avisa cuando un solo canal aporta más que esto de los prospectos del periodo.')
    sin_respuesta_minutos = models.PositiveIntegerField(
        default=5, validators=[MinValueValidator(1)], verbose_name='Minutos sin respuesta a un prospecto',
        help_text='Dentro de 8:00 a 20:00 (Mérida), un prospecto que escribió y no recibe respuesta en este '
                  'tiempo se avisa al encargado.')
    anomalia_factor = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('2.00'), validators=[MinValueValidator(Decimal('1.1'))],
        verbose_name='Factor de gasto anómalo',
        help_text='El gasto de un día que supera este múltiplo del promedio de los 7 días previos se señala.')
    ciclo_dias_evento = models.PositiveIntegerField(
        default=120, verbose_name='Ciclo de venta · evento (días)',
        help_text='Tiempo típico entre el primer contacto y el cobro de un evento. Una cohorte es madura '
                  'cuando ya pasó este tiempo desde que terminó su mes.')
    ciclo_dias_pasadia = models.PositiveIntegerField(default=30, verbose_name='Ciclo de venta · pasadía (días)')
    ciclo_dias_hospedaje = models.PositiveIntegerField(default=30, verbose_name='Ciclo de venta · hospedaje (días)')
    prob_etapa = models.JSONField(
        default=prob_etapa_inicial, verbose_name='Probabilidad por etapa',
        help_text='Probabilidad (0 a 1) de cerrar según la etapa; con ella se pronostican los anticipos. '
                  'Una etapa por clave: NUEVO, CONTACTADO, VISITA_AGENDADA, COTIZADO.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Parámetro de marketing'
        verbose_name_plural = 'Supuestos'
        ordering = ['-vigente_desde', '-id']

    def __str__(self):
        return f'Supuestos de marketing desde {self.vigente_desde:%d/%m/%Y}'

    def clean(self):
        errores = {}
        topes = self.cac_tope_manual
        if topes not in (None, {}):
            if not isinstance(topes, dict) or any(k not in dict(SERVICIO_CHOICES) for k in topes):
                errores['cac_tope_manual'] = 'Usa un objeto con claves EVENTO, PASADIA u HOSPEDAJE.'
            else:
                try:
                    if any(Decimal(str(v)) <= 0 for v in topes.values()):
                        errores['cac_tope_manual'] = 'Cada tope debe ser mayor que cero.'
                except ArithmeticError:
                    errores['cac_tope_manual'] = 'Cada tope debe ser un número.'
        probs = self.prob_etapa
        if not isinstance(probs, dict):
            errores['prob_etapa'] = 'Usa un objeto etapa → probabilidad.'
        else:
            try:
                invalidas = [k for k, v in probs.items()
                             if k not in Lead.RANGO and k != Lead.PERDIDO or not Decimal('0') <= Decimal(str(v)) <= 1]
            except ArithmeticError:
                invalidas = ['valor']
            if invalidas:
                errores['prob_etapa'] = 'Cada etapa debe ser una del embudo con probabilidad entre 0 y 1.'
        if errores:
            raise ValidationError(errores)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.activo:
            type(self).objects.filter(activo=True).exclude(pk=self.pk).update(activo=False)

    @classmethod
    def vigente(cls) -> 'ParametroMarketing':
        """El juego activo, o uno con los valores de fábrica (sin guardar) si nadie ha capturado nada."""
        return cls.objects.filter(activo=True).order_by('-vigente_desde', '-id').first() or cls()

    def ciclo_de(self, tipo: str) -> int:
        return {'EVENTO': self.ciclo_dias_evento, 'PASADIA': self.ciclo_dias_pasadia,
                'HOSPEDAJE': self.ciclo_dias_hospedaje}[tipo]

    def prob_de(self, etapa: str) -> Decimal:
        valor = (self.prob_etapa or {}).get(etapa, prob_etapa_inicial().get(etapa, '0'))
        return Decimal(str(valor))


class SnapshotKPI(models.Model):
    """Cifras de un día para ver la tendencia sin recalcular el histórico. Una fila por (fecha, servicio,
    campaña): `tipo_servicio` vacío = total y `campana` vacía = todas. Cada fila resume la ventana de
    `ventana_dias` que termina en `fecha`. Una razón sin base queda en NULL (el motivo no se guarda)."""
    fecha = models.DateField(db_index=True, verbose_name='Fecha')
    tipo_servicio = models.CharField(max_length=15, blank=True, verbose_name='Servicio')
    campana = models.ForeignKey(Campana, on_delete=models.PROTECT, null=True, blank=True, related_name='+',
                                verbose_name='Campaña')
    ventana_dias = models.PositiveIntegerField(verbose_name='Ventana (días)')
    gasto = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), verbose_name='Gasto')
    prospectos = models.PositiveIntegerField(default=0, verbose_name='Prospectos')
    cotizaciones = models.PositiveIntegerField(default=0, verbose_name='Cotizaciones')
    ganados = models.PositiveIntegerField(default=0, verbose_name='Ganados')
    contratado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'),
                                     verbose_name='Contratado (sin IVA)')
    cobrado = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'),
                                  verbose_name='Cobrado (sin IVA)')
    cpl = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name='CPL')
    costo_cotizacion = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                           verbose_name='Costo por cotización')
    tasa_cierre = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True,
                                      verbose_name='Tasa de cierre (%)')
    cac = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name='CAC')
    cac_tope = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name='Tope de CAC')
    roas_cobrado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                       verbose_name='ROAS cobrado')
    roas_contratado = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                          verbose_name='ROAS contratado')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Instantánea de indicadores'
        verbose_name_plural = 'Instantáneas'
        ordering = ['-fecha', 'tipo_servicio', 'campana_id']
        constraints = [
            models.UniqueConstraint(fields=['fecha', 'tipo_servicio'], name='mkt_snapshot_sin_campana',
                                    condition=Q(campana__isnull=True)),
            models.UniqueConstraint(fields=['fecha', 'tipo_servicio', 'campana'], name='mkt_snapshot_con_campana',
                                    condition=Q(campana__isnull=False)),
        ]

    def __str__(self):
        return f'{self.fecha:%d/%m/%Y} {self.tipo_servicio or "Total"}'


class Decision(models.Model):
    """Recomendación de marketing que espera la decisión de Dirección (Issue #468, fase 4).

    Nace de una regla diaria (`services/decisiones.py`) junto con su `comunicacion.Hallazgo`, que es lo
    que se ve en el resumen del lunes. `accion_propuesta` describe la acción en JSON para que la fase 5 la
    ejecute; en esta fase **nada se ejecuta**: aprobar solo deja constancia de quién y cuándo. Una decisión
    abierta por regla y objeto (restricción única); caduca a los 14 días. No se borra.
    """
    PAUSAR, REANUDAR, AJUSTAR, REASIGNAR, INFORMATIVA = (
        'PAUSAR', 'REANUDAR', 'AJUSTAR_PRESUPUESTO', 'REASIGNAR', 'INFORMATIVA')
    ACCION_CHOICES = [(PAUSAR, 'Pausar'), (REANUDAR, 'Reanudar'), (AJUSTAR, 'Ajustar presupuesto'),
                      (REASIGNAR, 'Reasignar presupuesto'), (INFORMATIVA, 'Informativa')]
    PENDIENTE, APROBADA, RECHAZADA, POSPUESTA, EJECUTADA, FALLIDA, EXPIRADA = (
        'PENDIENTE', 'APROBADA', 'RECHAZADA', 'POSPUESTA', 'EJECUTADA', 'FALLIDA', 'EXPIRADA')
    ESTADO_CHOICES = [(PENDIENTE, 'Pendiente'), (APROBADA, 'Aprobada'), (RECHAZADA, 'Rechazada'),
                      (POSPUESTA, 'Pospuesta'), (EJECUTADA, 'Ejecutada'), (FALLIDA, 'Fallida'),
                      (EXPIRADA, 'Expirada')]
    ABIERTAS = (PENDIENTE, POSPUESTA)

    regla = models.CharField(max_length=24, db_index=True, verbose_name='Regla')
    objeto_clave = models.CharField(max_length=120, verbose_name='Objeto',
                                    help_text='Anuncio, campaña, canal o mes sobre el que aplica la regla.')
    tipo_accion = models.CharField(max_length=20, choices=ACCION_CHOICES, verbose_name='Acción')
    hallazgo = models.OneToOneField('comunicacion.Hallazgo', null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='decision_marketing', verbose_name='Hallazgo')
    titulo = models.CharField(max_length=200, verbose_name='Recomendación')
    evidencia = models.JSONField(default=dict, blank=True, verbose_name='Evidencia')
    accion_propuesta = models.JSONField(default=dict, blank=True, verbose_name='Acción propuesta')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=PENDIENTE, db_index=True,
                              verbose_name='Estado')
    expira_en = models.DateTimeField(verbose_name='Expira')
    pospuesta_hasta = models.DateTimeField(null=True, blank=True, verbose_name='Pospuesta hasta')
    decidido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='+', verbose_name='Decidido por')
    decidido_en = models.DateTimeField(null=True, blank=True, verbose_name='Decidido el')
    ejecutado_en = models.DateTimeField(null=True, blank=True, verbose_name='Ejecutado el')
    resultado = models.TextField(blank=True, verbose_name='Resultado de la ejecución')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizado')

    class Meta:
        verbose_name = 'Decisión'
        verbose_name_plural = 'Decisiones'
        ordering = ['-created_at', '-id']
        constraints = [models.UniqueConstraint(
            fields=['regla', 'objeto_clave'], condition=models.Q(estado__in=['PENDIENTE', 'POSPUESTA']),
            name='mkt_decision_abierta_unica')]

    def __str__(self):
        return f'Decisión {self.pk}: {self.titulo}'[:120]

    @property
    def abierta(self) -> bool:
        return self.estado in self.ABIERTAS


class AccionAnuncio(models.Model):
    """Bitácora de lo que se hizo (o se habría hecho) en la plataforma de anuncios por una decisión aprobada
    (Issue #468, fase 5). Una fila por paso: una reasignación son dos ajustes (y una reversión si el segundo
    falla). Una vez OK o FALLIDA la fila no se edita ni se borra. `respuesta` es un resumen sin secretos."""
    PAUSAR, REANUDAR, AJUSTAR = 'PAUSAR', 'REANUDAR', 'AJUSTAR_PRESUPUESTO'
    TIPO_CHOICES = [(PAUSAR, 'Pausar'), (REANUDAR, 'Reanudar'), (AJUSTAR, 'Ajustar presupuesto')]
    CAMPANA, ANUNCIO = 'CAMPANA', 'ANUNCIO'
    OBJETO_CHOICES = [(CAMPANA, 'Campaña'), (ANUNCIO, 'Anuncio')]
    SIMULACION, REAL = 'SIMULACION', 'REAL'
    MODO_CHOICES = [(SIMULACION, 'Simulación'), (REAL, 'Real')]
    PENDIENTE, EJECUTANDO, OK, FALLIDA = 'PENDIENTE', 'EJECUTANDO', 'OK', 'FALLIDA'
    ESTADO_CHOICES = [(PENDIENTE, 'Pendiente'), (EJECUTANDO, 'Ejecutando'), (OK, 'Correcta'), (FALLIDA, 'Fallida')]
    TERMINALES = (OK, FALLIDA)

    decision = models.ForeignKey(Decision, on_delete=models.PROTECT, related_name='acciones', verbose_name='Decisión')
    paso = models.PositiveSmallIntegerField(default=1, verbose_name='Paso')
    reversion = models.BooleanField(default=False, verbose_name='Es reversión')
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, verbose_name='Acción')
    objeto_tipo = models.CharField(max_length=8, choices=OBJETO_CHOICES, verbose_name='Objeto')
    objeto_id_externo = models.CharField(max_length=100, verbose_name='Identificador en la plataforma')
    objeto_nombre = models.CharField(max_length=300, blank=True, verbose_name='Nombre')
    valor_anterior = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                         verbose_name='Valor anterior')
    valor_nuevo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True,
                                      verbose_name='Valor nuevo')
    modo = models.CharField(max_length=10, choices=MODO_CHOICES, verbose_name='Modo')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=PENDIENTE, db_index=True,
                              verbose_name='Estado')
    respuesta = models.TextField(blank=True, verbose_name='Respuesta')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Ejecutada por')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Creada')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Actualizada')

    class Meta:
        verbose_name = 'Acción en anuncios'
        verbose_name_plural = 'Acciones'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.get_tipo_display()} · {self.objeto_nombre or self.objeto_id_externo} · {self.get_estado_display()}'

    def save(self, *args, **kwargs):
        if self.pk and AccionAnuncio.objects.filter(pk=self.pk, estado__in=self.TERMINALES).exists():
            raise ValidationError('La bitácora de acciones no se edita una vez terminada.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('La bitácora de acciones no se borra.')


class EventoConversion(models.Model):
    """Bandeja de salida (outbox) de eventos hacia la Conversions API de Meta (fase 6).

    Un evento nace PENDIENTE cuando el prospecto avanza (en `on_commit`, para que
    un fallo de Meta nunca afecte la venta) y el programador lo envía. Nada de
    datos personales en claro: solo identificadores de anuncio y navegador; el
    teléfono y el correo se leen del prospecto al momento de enviar y salen con
    hash SHA-256. Nunca se borra: los estados terminales son la bitácora.
    """
    LEAD = 'Lead'
    COTIZACION_ENVIADA = 'CotizacionEnviada'
    SCHEDULE = 'Schedule'
    PURCHASE = 'Purchase'
    EVENTO_CHOICES = [
        (LEAD, 'Lead'),
        (COTIZACION_ENVIADA, 'Cotización enviada'),
        (SCHEDULE, 'Visita agendada'),
        (PURCHASE, 'Compra'),
    ]
    WHATSAPP = 'WHATSAPP'
    WEB = 'WEB'
    ORIGEN_CHOICES = [(WHATSAPP, 'WhatsApp'), (WEB, 'Sitio web')]
    PENDIENTE = 'PENDIENTE'
    ENVIADO = 'ENVIADO'
    FALLIDO = 'FALLIDO'
    OMITIDO = 'OMITIDO_SIN_CONSENTIMIENTO'
    CANCELADO = 'CANCELADO'
    ESTADO_CHOICES = [
        (PENDIENTE, 'Pendiente'),
        (ENVIADO, 'Enviado'),
        (FALLIDO, 'Fallido'),
        (OMITIDO, 'Omitido (sin consentimiento)'),
        (CANCELADO, 'Cancelado'),
    ]
    TERMINALES = (ENVIADO, FALLIDO, OMITIDO, CANCELADO)

    META = 'META'
    GOOGLE = 'GOOGLE'
    PLATAFORMA_CHOICES = [(META, 'Meta'), (GOOGLE, 'Google')]

    lead = models.ForeignKey(Lead, on_delete=models.PROTECT, related_name='conversiones', verbose_name='Prospecto')
    plataforma = models.CharField(max_length=6, choices=PLATAFORMA_CHOICES, default=META, db_index=True,
                                  verbose_name='Plataforma')
    nombre_evento = models.CharField(max_length=20, choices=EVENTO_CHOICES, verbose_name='Evento')
    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, verbose_name='ID del evento')
    valor = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name='Valor')
    moneda = models.CharField(max_length=3, default='MXN', verbose_name='Moneda')
    origen = models.CharField(max_length=8, choices=ORIGEN_CHOICES, verbose_name='Origen')
    ctwa_clid = models.CharField(max_length=300, blank=True, verbose_name='Clic en el anuncio (ctwa_clid)')
    fbp = models.CharField(max_length=100, blank=True, verbose_name='Navegador (fbp)')
    fbc = models.CharField(max_length=300, blank=True, verbose_name='Clic en el anuncio (fbc)')
    contexto_web = models.JSONField(default=dict, blank=True, verbose_name='Contexto web')
    event_time = models.DateTimeField(default=timezone.now, verbose_name='Ocurrió')
    estado = models.CharField(max_length=30, choices=ESTADO_CHOICES, default=PENDIENTE, db_index=True,
                              verbose_name='Estado')
    intentos = models.PositiveSmallIntegerField(default=0, verbose_name='Intentos')
    proximo_intento = models.DateTimeField(default=timezone.now, verbose_name='Próximo intento')
    ultimo_error = models.CharField(max_length=300, blank=True, verbose_name='Último error')
    enviado_en = models.DateTimeField(null=True, blank=True, verbose_name='Enviado el')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Conversión'
        verbose_name_plural = 'Conversiones'
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['lead', 'plataforma', 'nombre_evento'],
                                    name='mkt_conversion_unica_por_lead_plataforma'),
        ]

    def __str__(self):
        return f'{self.get_nombre_evento_display()} · {self.get_estado_display()}'

    def delete(self, *args, **kwargs):
        raise ValidationError('La bandeja de conversiones no se borra.')


# === Referidos, permiso de medios y reseñas (fase 7) ===

class Referido(models.Model):
    """Una recomendación: lo gana QUIEN RECOMIENDA cuando la cotización del recomendado queda CONFIRMADA.

    El beneficio se aplica como un `comercial.Descuento` acotado por productos (ver
    `services.referidos`); aquí solo vive el vínculo, su vigencia (12 meses) y su único uso.
    """
    REGISTRADO = 'REGISTRADO'
    GANADO = 'GANADO'
    USADO = 'USADO'
    VENCIDO = 'VENCIDO'
    ANULADO = 'ANULADO'
    ESTADO_CHOICES = [
        (REGISTRADO, 'Registrado'),
        (GANADO, 'Ganado'),
        (USADO, 'Usado'),
        (VENCIDO, 'Vencido'),
        (ANULADO, 'Anulado'),
    ]

    codigo = models.CharField(max_length=12, unique=True, editable=False, verbose_name='Código')
    recomendador = models.ForeignKey('comercial.Cliente', on_delete=models.PROTECT, related_name='referidos_hechos',
                                     verbose_name='Quién recomienda')
    recomendado_lead = models.ForeignKey(Lead, on_delete=models.SET_NULL, null=True, blank=True,
                                         related_name='referidos', verbose_name='Prospecto recomendado')
    recomendado_cotizacion = models.ForeignKey('comercial.Cotizacion', on_delete=models.SET_NULL, null=True,
                                               blank=True, related_name='referidos_recomendado',
                                               verbose_name='Cotización del recomendado')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=REGISTRADO, db_index=True,
                              verbose_name='Estado')
    ganado_en = models.DateTimeField(null=True, blank=True, verbose_name='Ganado el')
    vence_en = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='Vence el')
    usado_en = models.DateTimeField(null=True, blank=True, verbose_name='Usado el')
    usado_en_cotizacion = models.ForeignKey('comercial.Cotizacion', on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='referidos_usados', verbose_name='Aplicado en la cotización')
    uso_confirmado_en = models.DateTimeField(null=True, blank=True, verbose_name='Uso confirmado el')
    monto_beneficio = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,
                                          verbose_name='Beneficio aplicado (sin IVA)')
    motivo_cierre = models.CharField(max_length=255, blank=True, verbose_name='Motivo')
    notificado_en = models.DateTimeField(null=True, blank=True, verbose_name='Avisado el')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Referido'
        verbose_name_plural = 'Referidos'
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['recomendado_lead'], name='mkt_referido_uno_por_prospecto',
                                    condition=Q(recomendado_lead__isnull=False)),
        ]

    def __str__(self):
        return f'{self.codigo} · {self.get_estado_display()}'

    def save(self, *args, **kwargs):
        if not self.codigo:
            self.codigo = 'REF-' + uuid.uuid4().hex[:8].upper()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Los referidos no se borran: se anulan.')

    @property
    def vigente(self) -> bool:
        return self.estado == self.GANADO and self.vence_en is not None and self.vence_en > timezone.now()


ALCANCES_MEDIOS = [('FOTOS', 'Fotos del evento'), ('TESTIMONIO', 'Testimonio'), ('NOMBRE', 'Nombre de pila')]


class AutorizacionMedios(models.Model):
    """Permiso del cliente para mostrar fotos, su testimonio y su nombre en la página y las redes.

    La evidencia es el texto exacto que aceptó (con su SHA-256), cuándo y por qué canal. Se revoca
    sin borrarse. Una fila viva (solicitada o autorizada) por cotización.
    """
    SOLICITADA = 'SOLICITADA'
    AUTORIZADA = 'AUTORIZADA'
    RECHAZADA = 'RECHAZADA'
    REVOCADA = 'REVOCADA'
    ESTADO_CHOICES = [
        (SOLICITADA, 'Solicitada'),
        (AUTORIZADA, 'Autorizada'),
        (RECHAZADA, 'Rechazada'),
        (REVOCADA, 'Revocada'),
    ]
    VIVAS = (SOLICITADA, AUTORIZADA)
    ORIGEN_CHOICES = [('WHATSAPP', 'WhatsApp'), ('PORTAL', 'Portal del cliente'), ('ADMIN', 'Registro del equipo')]

    cliente = models.ForeignKey('comercial.Cliente', on_delete=models.PROTECT, related_name='autorizaciones_medios',
                                verbose_name='Cliente')
    cotizacion = models.ForeignKey('comercial.Cotizacion', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='autorizaciones_medios', verbose_name='Cotización')
    alcances = models.JSONField(default=list, verbose_name='Alcances',
                                help_text='FOTOS, TESTIMONIO y/o NOMBRE.')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=SOLICITADA, db_index=True,
                              verbose_name='Estado')
    origen = models.CharField(max_length=8, choices=ORIGEN_CHOICES, verbose_name='Origen')
    texto_aceptado = models.TextField(verbose_name='Texto que se le mostró')
    texto_sha256 = models.CharField(max_length=64, editable=False, verbose_name='SHA-256 del texto')
    referencia_externa = models.CharField(max_length=200, blank=True, verbose_name='Mensaje de WhatsApp (wamid)')
    solicitada_en = models.DateTimeField(default=timezone.now, verbose_name='Solicitada el')
    decidida_en = models.DateTimeField(null=True, blank=True, verbose_name='Decidida el')
    registrada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name='+', verbose_name='Registrada por')
    revocada_en = models.DateTimeField(null=True, blank=True, verbose_name='Revocada el')
    revocada_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='+', verbose_name='Revocada por')
    motivo_revocacion = models.CharField(max_length=255, blank=True, verbose_name='Motivo de la revocación')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+', verbose_name='Actualizado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Autorización de medios'
        verbose_name_plural = 'Autorizaciones'
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['cotizacion'], name='mkt_autorizacion_viva_por_cotizacion',
                                    condition=Q(estado__in=['SOLICITADA', 'AUTORIZADA'], cotizacion__isnull=False)),
        ]

    def __str__(self):
        return f'{self.cliente} · {self.get_estado_display()}'

    def clean(self):
        validos = {codigo for codigo, _ in ALCANCES_MEDIOS}
        if not isinstance(self.alcances, list) or not self.alcances or set(self.alcances) - validos:
            raise ValidationError({'alcances': 'Elige uno o más alcances: FOTOS, TESTIMONIO, NOMBRE.'})

    def save(self, *args, **kwargs):
        from hashlib import sha256
        self.texto_sha256 = sha256((self.texto_aceptado or '').encode('utf-8')).hexdigest()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Las autorizaciones no se borran: se revocan.')


class ResenaGoogle(models.Model):
    """Reseña de Google Business Profile ya vista por el ERP. Solo lo necesario: sin nombre ni texto."""
    id_externo = models.CharField(max_length=200, unique=True, verbose_name='ID en Google')
    estrellas = models.PositiveSmallIntegerField(verbose_name='Estrellas')
    publicada_en = models.DateTimeField(null=True, blank=True, verbose_name='Publicada el')
    actualizada_en = models.DateTimeField(null=True, blank=True, verbose_name='Actualizada el')
    respondida = models.BooleanField(default=False, verbose_name='Respondida')
    avisada = models.BooleanField(default=False, verbose_name='Avisada a Dirección')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Fecha de creación')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Última actualización')

    class Meta:
        verbose_name = 'Reseña de Google'
        verbose_name_plural = 'Reseñas'
        ordering = ['-publicada_en', '-id']

    def __str__(self):
        return f'{self.estrellas} estrellas · {self.publicada_en:%d/%m/%Y}' if self.publicada_en else f'{self.estrellas} estrellas'
