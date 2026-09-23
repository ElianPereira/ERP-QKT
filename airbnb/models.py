"""
Modelos del módulo Airbnb — SOLO pendientes de borrado
======================================================
La línea de negocio Airbnb salió del portafolio (Issue #311). Toda la
funcionalidad ya se retiró; estos modelos quedan únicamente para que la
siguiente migración borre sus datos y tablas, y después se elimina la app.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models


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

    def __str__(self):
        return f"{self.nombre} ({self.get_tipo_display()})"

    class Meta:
        verbose_name = "Anuncio"
        verbose_name_plural = "Anuncios"
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

    def __str__(self):
        return f"{self.anuncio.nombre}: {self.fecha_inicio} → {self.fecha_fin}"

    class Meta:
        verbose_name = "Reserva"
        verbose_name_plural = "Reservas"
        ordering = ['-fecha_inicio']
        indexes = [
            models.Index(fields=['fecha_inicio', 'fecha_fin']),
            models.Index(fields=['anuncio', 'fecha_inicio']),
        ]


class PagoAirbnb(models.Model):
    """
    Pagos recibidos de Airbnb.
    Régimen fiscal: Actividad Empresarial - Plataformas Tecnológicas.

    Las retenciones se guardan TAL COMO VIENEN del CSV de Airbnb; las
    tasas de referencia viven en `core_erp.impuestos`. Ver
    `retenciones_esperadas()` y `cuadra` para el contraste entre ambas.
    """
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente de Pago'),
        ('PAGADO', 'Pagado por Airbnb'),
        ('CANCELADO', 'Cancelado'),
        ('REEMBOLSADO', 'Reembolsado al huésped'),
    ]

    ORIGEN_CHOICES = [
        ('CSV', 'Importado del CSV de Airbnb'),
        ('MANUAL', 'Capturado a mano'),
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
        null=True,
        db_index=True,
        verbose_name="Código de Confirmación",
        help_text=(
            "Código de reserva de Airbnb (ej: HMXXXXXXXX). Ya NO es único: una "
            "extensión de estancia genera un segundo payout con el mismo código "
            "(mismo huésped, cobro nuevo), y cada uno necesita su propio registro "
            "para cuadrar con el estado de cuenta y con las facturas del contador."
        )
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
        verbose_name="Retención ISR",
        help_text="ISR retenido por la plataforma, tal como viene en el CSV. La tasa depende de si Airbnb tiene el RFC del anfitrión (art. 113-A LISR)."
    )
    retencion_iva = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Retención IVA",
        help_text="IVA retenido por la plataforma, tal como viene en el CSV (art. 18-J LIVA)."
    )

    # IVA que Airbnb cobra al huésped y TRANSFIERE al anfitrión para que sea
    # él quien lo entere. En el CSV son las filas "Impuestos liquidados como
    # anfitrión" y suman al depósito, por eso no es un gasto.
    iva_trasladado = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="IVA trasladado",
        help_text=("IVA que Airbnb cobró al huésped y te transfiere. Lo enteras tú, "
                   "no la plataforma."),
    )

    # Impuesto al hospedaje. A diferencia del IVA, este lo retiene y entera
    # Airbnb: aparece en la columna "Impuesto liquidado por Airbnb" y NO llega
    # al depósito, así que es informativo para el anfitrión.
    impuesto_hospedaje = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Impuesto al hospedaje (ISH)",
        help_text=("Impuesto estatal que Airbnb retiene y entera por su cuenta. "
                   "Informativo: no pasa por tus manos."),
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
    origen = models.CharField(
        max_length=10, choices=ORIGEN_CHOICES, default='CSV',
        help_text="Un pago capturado a mano no se sobrescribe al reimportar el CSV.",
    )
    archivo_csv_origen = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Archivo CSV Origen",
        help_text="Nombre del archivo CSV de donde se importó"
    )
    payout_id = models.CharField(
        max_length=100, blank=True, db_index=True,
        verbose_name="Depósito de Airbnb",
        help_text="Agrupa los pagos que Airbnb depositó juntos, para conciliar contra el banco.",
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

    def __str__(self):
        return f"{self.codigo_confirmacion or 'Sin código'} - {self.huesped} (${self.monto_neto})"

    class Meta:
        verbose_name = "Pago"
        verbose_name_plural = "Pagos"
        ordering = ['-fecha_checkin']
        indexes = [
            # El reporte fiscal agrupa por fecha de pago, no por check-in.
            models.Index(fields=['fecha_pago']),
            models.Index(fields=['estado', '-fecha_pago']),
            models.Index(fields=['anuncio', '-fecha_checkin']),
        ]
        constraints = [
            # Un mismo código de reserva puede tener más de un payout (p. ej.
            # una extensión de estancia cobrada aparte), pero no dos payouts
            # el mismo día para el mismo código: eso sí sería el duplicado que
            # la reimportación del CSV debe actualizar, no crear de nuevo.
            models.UniqueConstraint(
                fields=['codigo_confirmacion', 'fecha_pago'],
                condition=models.Q(codigo_confirmacion__isnull=False)
                & models.Q(fecha_pago__isnull=False),
                name='airbnb_pago_codigo_fecha_unico',
            ),
        ]


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


class DepositoConciliado(models.Model):
    """
    Emparejamiento confirmado a mano entre un payout de Airbnb y el abono
    del banco.

    Existe porque el emparejamiento automático no siempre puede decidir: si el
    banco no conservó el id del payout y dos depósitos coinciden en importe y
    fecha, cualquier asignación sería una adivinanza. En ese caso el sistema
    no elige —los marca como ambiguos— y quien concilia dice cuál es cuál. Lo
    que se guarda aquí manda sobre el automático y no se vuelve a preguntar.
    """
    payout_id = models.CharField(
        max_length=100, unique=True,
        verbose_name="Depósito de Airbnb",
        help_text="El payout tal como lo trae el CSV.",
    )
    movimiento = models.OneToOneField(
        'contabilidad.MovimientoEstadoCuenta',
        on_delete=models.CASCADE,
        related_name='deposito_airbnb',
        verbose_name="Abono del estado de cuenta",
    )
    confirmado_por = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )
    confirmado_at = models.DateTimeField(auto_now_add=True)
    notas = models.TextField(blank=True)

    class Meta:
        verbose_name = "Depósito de Airbnb conciliado"
        verbose_name_plural = "Depósitos de Airbnb conciliados"
        ordering = ['-confirmado_at']

    def __str__(self):
        return f"{self.payout_id} → {self.movimiento_id}"
