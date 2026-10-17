"""
Módulo de Cumplimiento (Issue #403): permisos, licencias, constancias y
contratos de servicio con vigencia, costo y expediente.

No genera pólizas ni toca contabilidad: el costo es un dato y, si existe la
`Compra` (CFDI) del pago, se liga con una FK opcional. Todos los archivos van
al storage privado y se descargan por `core_erp/descargas.py`.
"""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core_erp.storages_qkt import storage_privado


class Tramite(models.Model):
    """Catálogo: qué necesita el negocio, cada cuánto y quién lo emite."""

    TIPO_CHOICES = [
        ('PERMISO', 'Permiso'),
        ('LICENCIA', 'Licencia'),
        ('CONSTANCIA', 'Constancia'),
        ('CONTRATO', 'Contrato de servicio'),
        ('SEGURO', 'Seguro'),
        ('OTRO', 'Otro'),
    ]

    nombre = models.CharField(max_length=150, unique=True, verbose_name='Nombre')
    tipo = models.CharField(max_length=12, choices=TIPO_CHOICES, default='PERMISO', verbose_name='Tipo')
    emisor = models.CharField(max_length=150, blank=True, verbose_name='Emisor',
                              help_text='Quién lo emite: Ayuntamiento de Umán, Protección Civil Yucatán…')
    proveedor = models.ForeignKey('comercial.Proveedor', null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='tramites_cumplimiento', verbose_name='Proveedor',
                                  help_text='Solo para contratos de servicio.')
    periodicidad_meses = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name='Periodicidad (meses)',
        help_text='12 para anuales. Vacío = única o sin vencimiento.')
    dias_aviso = models.PositiveSmallIntegerField(
        default=60, verbose_name='Días de aviso', help_text='Desde cuántos días antes se marca «Por vencer».')
    critico = models.BooleanField(default=False, verbose_name='Crítico',
                                  help_text='Sin esto no se puede operar.')
    orden = models.PositiveSmallIntegerField(
        default=100, verbose_name='Orden', help_text='Posición en las listas y el tablero (menor = primero).')
    requiere = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='dependientes',
        verbose_name='Requiere (trámite previo)',
        help_text='Sin este trámite vigente no se puede marcar como obtenido el actual.')
    renovacion_automatica = models.BooleanField(default=False, verbose_name='Renovación automática')
    dias_preaviso_cancelacion = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name='Días de preaviso para cancelar',
        help_text='Contratos: días antes del vencimiento para cancelar sin renovar.')
    responsable = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='tramites_responsable', verbose_name='Responsable')
    notas = models.TextField(blank=True, verbose_name='Notas')
    activo = models.BooleanField(default=True, verbose_name='Activo')

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name='+', verbose_name='Modificado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Modificado')

    class Meta:
        verbose_name = 'Trámite'
        verbose_name_plural = 'Trámites'
        ordering = ['orden', '-critico', 'nombre']

    def __str__(self):
        return self.nombre

    def clean(self):
        super().clean()
        if self.dias_preaviso_cancelacion and self.tipo != 'CONTRATO':
            raise ValidationError({'dias_preaviso_cancelacion': 'El preaviso de cancelación aplica solo a contratos.'})
        previo, vistos = self.requiere, {self.pk}
        while previo is not None:
            if previo.pk in vistos:
                raise ValidationError({'requiere': 'La cadena de trámites previos no puede ser circular.'})
            vistos.add(previo.pk)
            previo = previo.requiere

    @property
    def previo_pendiente(self):
        """Trámite previo que impide obtener este (None si no hay o ya está vigente)."""
        from .services import ESTADOS_QUE_HABILITAN, estado_tramite

        if self.requiere_id and estado_tramite(self.requiere).estado not in ESTADOS_QUE_HABILITAN:
            return self.requiere
        return None


class RequisitoTramite(models.Model):
    """Documento que el trámite pide para obtenerse o renovarse."""

    tramite = models.ForeignKey(Tramite, on_delete=models.CASCADE, related_name='requisitos', verbose_name='Trámite')
    nombre = models.CharField(max_length=150, verbose_name='Requisito')
    obligatorio = models.BooleanField(default=True, verbose_name='Obligatorio')
    orden = models.PositiveSmallIntegerField(default=0, verbose_name='Orden')
    notas = models.CharField(max_length=255, blank=True, verbose_name='Notas')
    activo = models.BooleanField(default=True, verbose_name='Activo')

    class Meta:
        verbose_name = 'Requisito'
        verbose_name_plural = 'Requisitos'
        ordering = ['orden', 'id']

    def __str__(self):
        return self.nombre


class Vigencia(models.Model):
    """Cada obtención o renovación de un trámite."""

    ESTADO_CHOICES = [
        ('EN_TRAMITE', 'En trámite'),
        ('OBTENIDA', 'Obtenida'),
        ('CANCELADA', 'Cancelada'),
    ]

    tramite = models.ForeignKey(Tramite, on_delete=models.PROTECT, related_name='vigencias', verbose_name='Trámite')
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default='EN_TRAMITE', verbose_name='Estado')
    folio = models.CharField(max_length=80, blank=True, verbose_name='Folio')
    fecha_emision = models.DateField(null=True, blank=True, verbose_name='Fecha de emisión')
    fecha_inicio = models.DateField(null=True, blank=True, verbose_name='Inicio de vigencia')
    fecha_vencimiento = models.DateField(null=True, blank=True, verbose_name='Vencimiento')
    costo = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'),
                                validators=[MinValueValidator(Decimal('0.00'))], verbose_name='Costo',
                                help_text='Total pagado, con IVA si aplica.')
    compra = models.ForeignKey('comercial.Compra', null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='vigencias_cumplimiento', verbose_name='Compra (CFDI)')
    documento = models.FileField(upload_to='cumplimiento/vigencias/', storage=storage_privado, blank=True,
                                 verbose_name='Documento oficial',
                                 help_text='Obligatorio para marcarla como obtenida.')
    notas = models.TextField(blank=True, verbose_name='Notas')

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name='+', verbose_name='Creado por')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name='+', verbose_name='Modificado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Modificado')

    class Meta:
        verbose_name = 'Vigencia'
        verbose_name_plural = 'Vigencias'
        ordering = ['-fecha_vencimiento', '-id']

    def __str__(self):
        base = f'{self.tramite} · {self.folio}' if self.folio else str(self.tramite)
        return f'{base} ({self.fecha_vencimiento:%d/%m/%Y})' if self.fecha_vencimiento else base

    @property
    def dias_restantes(self):
        """Días al vencimiento (negativo = vencida); None si no vence."""
        if not self.fecha_vencimiento:
            return None
        return (self.fecha_vencimiento - timezone.localdate()).days

    def clean(self):
        super().clean()
        errores = {}
        if self.estado == 'OBTENIDA':
            if not self.documento:
                errores['documento'] = 'Sube el documento oficial para marcarla como obtenida.'
            if not self.fecha_vencimiento and self.tramite_id and self.tramite.periodicidad_meses:
                errores['fecha_vencimiento'] = 'Este trámite vence: captura la fecha de vencimiento.'
            previo = self.tramite.previo_pendiente if self.tramite_id else None
            if previo:
                errores['estado'] = f'Antes necesitas «{previo.nombre}» vigente.'
        if self.fecha_inicio and self.fecha_vencimiento and self.fecha_vencimiento < self.fecha_inicio:
            errores['fecha_vencimiento'] = 'El vencimiento no puede ser anterior al inicio de vigencia.'
        if errores:
            raise ValidationError(errores)


class DocumentoExpediente(models.Model):
    """Documento entregado para obtener una vigencia."""

    vigencia = models.ForeignKey(Vigencia, on_delete=models.CASCADE, related_name='documentos',
                                 verbose_name='Vigencia')
    requisito = models.ForeignKey(RequisitoTramite, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='documentos', verbose_name='Requisito')
    nombre = models.CharField(max_length=150, verbose_name='Nombre')
    archivo = models.FileField(upload_to='cumplimiento/expediente/', storage=storage_privado, verbose_name='Archivo')
    fecha_documento = models.DateField(null=True, blank=True, verbose_name='Fecha del documento')
    subido_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, editable=False,
                                   on_delete=models.SET_NULL, related_name='+', verbose_name='Subido por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Subido')
    activo = models.BooleanField(default=True, verbose_name='Activo')

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documentos'
        ordering = ['id']

    def __str__(self):
        return self.nombre


class AvisoVencimiento(models.Model):
    """Bitácora e idempotencia de los avisos: un registro por vigencia,
    umbral y canal. El INSERT va antes del envío (ver services_avisos)."""

    CANAL_CHOICES = [('WHATSAPP', 'WhatsApp'), ('EMAIL', 'Correo')]
    ESTADO_CHOICES = [('PENDIENTE', 'Pendiente'), ('ENVIADO', 'Enviado'), ('FALLIDO', 'Fallido')]

    vigencia = models.ForeignKey(Vigencia, on_delete=models.CASCADE, related_name='avisos', verbose_name='Vigencia')
    umbral = models.SmallIntegerField(
        verbose_name='Umbral (días)',
        help_text='Días al vencimiento que disparó el aviso; -1 = vencida, -2 = último día para cancelar.')
    canal = models.CharField(max_length=10, choices=CANAL_CHOICES, verbose_name='Canal')
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default='PENDIENTE', verbose_name='Estado')
    detalle = models.TextField(blank=True, verbose_name='Detalle')
    enviado_en = models.DateTimeField(null=True, blank=True, verbose_name='Enviado')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Creado')
    actualizado_en = models.DateTimeField(default=timezone.now, verbose_name='Último intento')

    class Meta:
        verbose_name = 'Aviso'
        verbose_name_plural = 'Avisos'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['vigencia', 'umbral', 'canal'], name='aviso_vencimiento_unico'),
        ]

    def __str__(self):
        return f'{self.vigencia} · {self.umbral} · {self.get_canal_display()}'
