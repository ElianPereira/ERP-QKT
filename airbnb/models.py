"""
Modelos del módulo Airbnb
=========================
Gestión de anuncios, reservaciones y pagos de Airbnb.
Separado contablemente del resto del ERP (régimen fiscal diferente).
"""
from decimal import Decimal
from django.db import models
from django.db.models import Sum
from django.utils.timezone import now
from django.contrib.auth.models import User


class AnuncioAirbnb(models.Model):
    """
    Representa un listing/anuncio en Airbnb.
    Puede ser una casa completa o una habitación dentro de la quinta.
    """
    TIPO_CHOICES = [
        ('CASA', 'Casa Completa'),
        ('HABITACION', 'Habitación en Quinta'),
    ]
    
    nombre = models.CharField(
        max_length=200, 
        verbose_name="Nombre del Anuncio",
        help_text="Ej: Casa Jardín, Habitación Orquídea"
    )
    tipo = models.CharField(
        max_length=20, 
        choices=TIPO_CHOICES, 
        default='HABITACION'
    )
    url_ical = models.URLField(
        max_length=500,
        verbose_name="URL de iCal",
        help_text="Obtener en Airbnb > Calendario > Exportar calendario"
    )
    airbnb_listing_id = models.CharField(
        max_length=50, 
        blank=True,
        verbose_name="ID de Airbnb",
        help_text="Se extrae automáticamente de la URL de iCal"
    )
    
    # Configuración de conflictos
    afecta_eventos_quinta = models.BooleanField(
        default=True,
        verbose_name="¿Afecta eventos de la Quinta?",
        help_text="Si está activo, las reservas de este anuncio pueden generar conflictos con eventos"
    )
    
    # Metadatos
    activo = models.BooleanField(default=True)
    ultima_sincronizacion = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def save(self, *args, **kwargs):
        # Extraer listing ID de la URL de iCal
        if self.url_ical and not self.airbnb_listing_id:
            # URL formato: https://www.airbnb.mx/calendar/ical/XXXXXX.ics?...
            try:
                import re
                match = re.search(r'/ical/(\d+)\.ics', self.url_ical)
                if match:
                    self.airbnb_listing_id = match.group(1)
            except:
                pass
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_display()})"
    
    class Meta:
        verbose_name = "Anuncio Airbnb"
        verbose_name_plural = "Anuncios Airbnb"
        ordering = ['nombre']


class ReservaAirbnb(models.Model):
    """
    Reservación sincronizada desde Airbnb vía iCal.
    Se usa para detectar conflictos con eventos de la quinta.
    """
    ESTADO_CHOICES = [
        ('CONFIRMADA', 'Confirmada'),
        ('PENDIENTE', 'Pendiente de Aceptar'),
        ('CANCELADA', 'Cancelada'),
        ('BLOQUEADA', 'Bloqueado por Host'),
    ]
    ORIGEN_CHOICES = [
        ('AIRBNB', 'Airbnb'),
        ('MANUAL', 'Registro Manual'),
        ('EVENTO', 'Bloqueo por Evento QKT'),
    ]
    
    anuncio = models.ForeignKey(
        AnuncioAirbnb, 
        on_delete=models.CASCADE, 
        related_name='reservas'
    )
    
    # Datos de la reserva
    uid_ical = models.CharField(
        max_length=255, 
        unique=True,
        verbose_name="UID de iCal",
        help_text="Identificador único del evento en el calendario"
    )
    titulo = models.CharField(
        max_length=200, 
        blank=True,
        verbose_name="Título/Huésped"
    )
    fecha_inicio = models.DateField(verbose_name="Check-in")
    fecha_fin = models.DateField(verbose_name="Check-out")
    
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_CHOICES, 
        default='CONFIRMADA'
    )
    origen = models.CharField(
        max_length=20, 
        choices=ORIGEN_CHOICES, 
        default='AIRBNB'
    )
    
    # Notas
    notas = models.TextField(blank=True)
    
    # Metadatos
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def noches(self):
        """Calcula el número de noches de la reserva"""
        return (self.fecha_fin - self.fecha_inicio).days
    
    def __str__(self):
        return f"{self.anuncio.nombre}: {self.fecha_inicio} → {self.fecha_fin}"
    
    class Meta:
        verbose_name = "Reserva Airbnb"
        verbose_name_plural = "Reservas Airbnb"
        ordering = ['-fecha_inicio']
        indexes = [
            models.Index(fields=['fecha_inicio', 'fecha_fin']),
            models.Index(fields=['anuncio', 'fecha_inicio']),
        ]


class PagoAirbnb(models.Model):
    """
    Pagos recibidos de Airbnb.
    Régimen fiscal: Actividad Empresarial - Plataformas Tecnológicas
    Retenciones: ISR 4%, IVA 8%
    
    Se importan desde CSV de Airbnb o se registran manualmente.
    """
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente de Pago'),
        ('PAGADO', 'Pagado por Airbnb'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    anuncio = models.ForeignKey(
        AnuncioAirbnb, 
        on_delete=models.CASCADE, 
        related_name='pagos',
        null=True, 
        blank=True
    )
    reserva = models.ForeignKey(
        ReservaAirbnb, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='pagos'
    )
    
    # Datos del pago
    codigo_confirmacion = models.CharField(
        max_length=20, 
        blank=True,
        verbose_name="Código de Confirmación",
        help_text="Código de reserva de Airbnb (ej: HMXXXXXXXX)"
    )
    huesped = models.CharField(
        max_length=200, 
        verbose_name="Nombre del Huésped"
    )
    fecha_checkin = models.DateField(verbose_name="Check-in")
    fecha_checkout = models.DateField(verbose_name="Check-out")
    
    # Montos (todos en MXN)
    monto_bruto = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        verbose_name="Monto Bruto",
        help_text="Total cobrado al huésped (antes de comisiones)"
    )
    comision_airbnb = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        verbose_name="Comisión Airbnb",
        help_text="Comisión cobrada por Airbnb (normalmente 3%)"
    )
    
    # Retenciones de plataforma (régimen fiscal)
    retencion_isr = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        verbose_name="Retención ISR (4%)",
        help_text="ISR retenido por plataforma tecnológica"
    )
    retencion_iva = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal('0.00'),
        verbose_name="Retención IVA (8%)",
        help_text="IVA retenido por plataforma tecnológica"
    )
    
    # Pago neto
    monto_neto = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        verbose_name="Monto Neto Recibido",
        help_text="Lo que realmente deposita Airbnb"
    )
    
    # Fechas
    fecha_pago = models.DateField(
        null=True, 
        blank=True,
        verbose_name="Fecha de Pago",
        help_text="Fecha en que Airbnb depositó el pago"
    )
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_CHOICES, 
        default='PENDIENTE'
    )
    
    # Auditoría
    notas = models.TextField(blank=True)
    espacio_csv = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Espacio (CSV)",
        help_text="Nombre del listing tal como llegó en el CSV de Airbnb"
    )
    archivo_csv_origen = models.CharField(
        max_length=255, 
        blank=True,
        verbose_name="Archivo CSV Origen",
        help_text="Nombre del archivo CSV de donde se importó"
    )
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='pagos_airbnb_creados'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    @property
    def noches(self):
        return (self.fecha_checkout - self.fecha_checkin).days
    
    @property
    def tarifa_por_noche(self):
        if self.noches > 0:
            return self.monto_bruto / self.noches
        return Decimal('0.00')
    
    def calcular_retenciones(self):
        """
        Calcula las retenciones según régimen de plataformas tecnológicas.
        ISR: 4% sobre monto bruto
        IVA: 8% sobre monto bruto
        """
        self.retencion_isr = (self.monto_bruto * Decimal('0.04')).quantize(Decimal('0.01'))
        self.retencion_iva = (self.monto_bruto * Decimal('0.08')).quantize(Decimal('0.01'))
        self.monto_neto = (
            self.monto_bruto 
            - self.comision_airbnb 
            - self.retencion_isr 
            - self.retencion_iva
        ).quantize(Decimal('0.01'))
    
    def save(self, *args, **kwargs):
        # Auto-calcular retenciones si no están definidas
        if self.monto_bruto and (self.retencion_isr == 0 or self.retencion_iva == 0):
            self.calcular_retenciones()
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.codigo_confirmacion or 'Sin código'} - {self.huesped} (${self.monto_neto})"
    
    class Meta:
        verbose_name = "Pago Airbnb"
        verbose_name_plural = "Pagos Airbnb"
        ordering = ['-fecha_checkin']


class ConflictoCalendario(models.Model):
    """
    Registro de conflictos detectados entre reservas de Airbnb 
    y eventos de la quinta.
    """
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente de Resolver'),
        ('RESUELTO', 'Resuelto'),
        ('IGNORADO', 'Ignorado'),
    ]
    
    reserva_airbnb = models.ForeignKey(
        ReservaAirbnb, 
        on_delete=models.CASCADE,
        related_name='conflictos'
    )
    cotizacion = models.ForeignKey(
        'comercial.Cotizacion', 
        on_delete=models.CASCADE,
        related_name='conflictos_airbnb'
    )
    
    fecha_conflicto = models.DateField(verbose_name="Fecha del Conflicto")
    descripcion = models.TextField(blank=True)
    estado = models.CharField(
        max_length=20, 
        choices=ESTADO_CHOICES, 
        default='PENDIENTE'
    )
    
    resuelto_por = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    fecha_resolucion = models.DateTimeField(null=True, blank=True)
    notas_resolucion = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Conflicto {self.fecha_conflicto}: {self.reserva_airbnb.anuncio.nombre} vs {self.cotizacion.nombre_evento}"
    
    class Meta:
        verbose_name = "Conflicto de Calendario"
        verbose_name_plural = "Conflictos de Calendario"
        ordering = ['-fecha_conflicto']
        unique_together = ['reserva_airbnb', 'cotizacion', 'fecha_conflicto']
