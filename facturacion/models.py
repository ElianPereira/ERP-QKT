"""
Modelos del Módulo de Facturación
=================================
Gestión de solicitudes de factura y comunicación con contador.
"""
from django.db import models
from django.contrib.auth.models import User
from django.utils.timezone import now
from cloudinary_storage.storage import RawMediaCloudinaryStorage

from comercial.models import Cliente, Cotizacion, Pago
from facturacion.choices import FormaPago, MetodoPago, RegimenFiscal, UsoCFDI


class ConfiguracionContador(models.Model):
    """
    Datos del contador para envío de solicitudes de factura.
    Solo debe existir un registro activo.
    """
    nombre = models.CharField(max_length=200, verbose_name="Nombre del Contador")
    email = models.EmailField(verbose_name="Email")
    telefono_whatsapp = models.CharField(
        max_length=15,
        verbose_name="WhatsApp",
        help_text="Con código de país, ej: 529991234567"
    )
    notas = models.TextField(blank=True, verbose_name="Notas / Instrucciones")
    activo = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración del Contador"
        verbose_name_plural = "Configuración del Contador"

    def __str__(self):
        return f"{self.nombre} - {self.email}"

    @classmethod
    def get_activo(cls):
        """Retorna el contador activo o None."""
        try:
            return cls.objects.filter(activo=True).first()
        except Exception:
            return None


class SolicitudFactura(models.Model):
    """
    Solicitud de factura enviada al contador.
    Se puede generar automáticamente desde un Pago o manualmente.
    """
    ESTADO_CHOICES = [
        ('PENDIENTE', 'Pendiente de Enviar'),
        ('ENVIADA', 'Enviada al Contador'),
        ('FACTURADA', 'Factura Recibida'),
        ('CANCELADA', 'Cancelada'),
    ]

    # ─── Relaciones ───────────────────────────────────────────
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.CASCADE,
        verbose_name="Cliente"
    )
    cotizacion = models.ForeignKey(
        Cotizacion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Cotización Origen",
        related_name="solicitudes_factura"
    )
    pago = models.ForeignKey(
        Pago,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Pago Origen",
        related_name="solicitudes_factura"
    )

    # ─── Datos para facturar ──────────────────────────────────
    fecha_solicitud = models.DateTimeField(default=now, verbose_name="Fecha de Solicitud")
    monto = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Monto a Facturar")
    concepto = models.TextField(verbose_name="Concepto / Descripción")

    # Datos fiscales (copiados del cliente al crear, editables)
    rfc = models.CharField(max_length=13, verbose_name="RFC")
    razon_social = models.CharField(max_length=300, verbose_name="Razón Social")
    codigo_postal = models.CharField(max_length=5, verbose_name="C.P. Fiscal")
    regimen_fiscal = models.CharField(
        max_length=3,
        choices=RegimenFiscal.choices,
        verbose_name="Régimen Fiscal"
    )
    uso_cfdi = models.CharField(
        max_length=4,
        choices=UsoCFDI.choices,
        default=UsoCFDI.GASTOS_EN_GENERAL,
        verbose_name="Uso CFDI"
    )

    # Datos del pago
    forma_pago = models.CharField(
        max_length=2,
        choices=FormaPago.choices,
        default=FormaPago.TRANSFERENCIA,
        verbose_name="Forma de Pago"
    )
    metodo_pago = models.CharField(
        max_length=3,
        choices=MetodoPago.choices,
        default=MetodoPago.PUE,
        verbose_name="Método de Pago"
    )
    fecha_pago = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha de Pago"
    )

    # ─── Estado y seguimiento ─────────────────────────────────
    estado = models.CharField(
        max_length=15,
        choices=ESTADO_CHOICES,
        default='PENDIENTE',
        verbose_name="Estado"
    )

    # Envío al contador
    enviada_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_enviadas',
        verbose_name="Enviada por"
    )
    fecha_envio = models.DateTimeField(null=True, blank=True, verbose_name="Fecha de Envío")
    metodo_envio = models.CharField(
        max_length=20,
        blank=True,
        choices=[('EMAIL', 'Email'), ('WHATSAPP', 'WhatsApp')],
        verbose_name="Método de Envío"
    )

    # ─── Archivos de factura ──────────────────────────────────
    archivo_zip = models.FileField(
        upload_to='facturas_zip/',
        blank=True,
        null=True,
        storage=RawMediaCloudinaryStorage(),
        verbose_name="ZIP con PDF y XML",
        help_text="Archivo ZIP con la factura (PDF + XML)"
    )
    archivo_pdf = models.FileField(
        upload_to='facturas_pdf/',
        blank=True,
        null=True,
        storage=RawMediaCloudinaryStorage(),
        verbose_name="Factura PDF"
    )
    archivo_xml = models.FileField(
        upload_to='facturas_xml/',
        blank=True,
        null=True,
        storage=RawMediaCloudinaryStorage(),
        verbose_name="Factura XML"
    )
    uuid_factura = models.CharField(
        max_length=36,
        blank=True,
        verbose_name="UUID/Folio Fiscal",
        help_text="Se extrae automáticamente del XML"
    )
    fecha_factura = models.DateField(
        null=True,
        blank=True,
        verbose_name="Fecha de Factura"
    )

    # ─── Notas ────────────────────────────────────────────────
    notas = models.TextField(blank=True, verbose_name="Notas / Observaciones")

    # ─── Auditoría ────────────────────────────────────────────
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='solicitudes_creadas',
        verbose_name="Creada por"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Solicitud de Factura"
        verbose_name_plural = "Solicitudes de Factura"
        ordering = ['-fecha_solicitud']
        indexes = [
            models.Index(fields=['estado', '-fecha_solicitud']),
            models.Index(fields=['cliente', '-fecha_solicitud']),
        ]

    def __str__(self):
        if self.id:
            folio = f"SOL-{int(self.id):04d}"
        else:
            folio = "Nueva"
        cliente_nombre = self.cliente.nombre if self.cliente else "Sin cliente"
        monto = float(self.monto) if self.monto else 0
        return f"{folio} | {cliente_nombre} | ${monto:,.2f}"

    @property
    def tiene_factura(self):
        """Verifica si ya se subió la factura (PDF o ZIP)."""
        return bool(self.archivo_zip or (self.archivo_pdf and self.archivo_xml))

    def marcar_enviada(self, usuario, metodo):
        """Marca la solicitud como enviada."""
        self.estado = 'ENVIADA'
        self.enviada_por = usuario
        self.fecha_envio = now()
        self.metodo_envio = metodo
        self.save(update_fields=['estado', 'enviada_por', 'fecha_envio', 'metodo_envio', 'updated_at'])

    def marcar_facturada(self):
        """Marca la solicitud como facturada (cuando se sube el archivo)."""
        if self.tiene_factura:
            self.estado = 'FACTURADA'
            self.save(update_fields=['estado', 'updated_at'])

    def save(self, *args, **kwargs):
        # Auto-cambiar estado a FACTURADA si se sube archivo
        if self.pk:
            if self.tiene_factura and self.estado != 'CANCELADA':
                self.estado = 'FACTURADA'
        super().save(*args, **kwargs)

    def get_datos_para_contador(self):
        """Genera el texto con datos para enviar al contador."""
        folio = f"SOL-{int(self.id):04d}" if self.id else "Nueva"
        monto = float(self.monto) if self.monto else 0
        
        lineas = [
            "═══════════════════════════════════",
            f"📋 SOLICITUD DE FACTURA {folio}",
            "═══════════════════════════════════",
            "",
            "👤 DATOS FISCALES:",
            f"   RFC: {self.rfc}",
            f"   Razón Social: {self.razon_social}",
            f"   C.P.: {self.codigo_postal}",
            f"   Régimen: {self.get_regimen_fiscal_display()}",
            f"   Uso CFDI: {self.get_uso_cfdi_display()}",
            "",
            "💰 DATOS DEL PAGO:",
            f"   Monto: ${monto:,.2f} MXN",
            f"   Concepto: {self.concepto}",
            f"   Forma de Pago: {self.get_forma_pago_display()}",
            f"   Método de Pago: {self.get_metodo_pago_display()}",
        ]

        if self.fecha_pago:
            lineas.append(f"   Fecha de Pago: {self.fecha_pago.strftime('%d/%m/%Y')}")

        if self.cotizacion:
            lineas.extend([
                "",
                f"📎 Evento: {self.cotizacion.nombre_evento}",
                f"   Fecha: {self.cotizacion.fecha_evento.strftime('%d/%m/%Y')}",
            ])

        if self.notas:
            lineas.extend([
                "",
                f"📝 Notas: {self.notas}",
            ])

        lineas.extend([
            "",
            "═══════════════════════════════════",
        ])

        return "\n".join(lineas)

    def get_whatsapp_url(self):
        """Genera URL de WhatsApp con los datos para el contador."""
        contador = ConfiguracionContador.get_activo()
        if not contador:
            return None

        import urllib.parse
        texto = self.get_datos_para_contador()
        texto_encoded = urllib.parse.quote(texto)
        telefono = contador.telefono_whatsapp.replace('+', '').replace(' ', '')

        return f"https://wa.me/{telefono}?text={texto_encoded}"