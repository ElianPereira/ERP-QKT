"""
Módulo legal: versionado de documentos, evidencia de consentimiento y ARCO.

Un aviso publicado no prueba nada; el registro sí. Estos modelos existen para
poder acreditar ante una autoridad o ante un contracargo *qué* versión de qué
documento aceptó *quién*, *cuándo* y *para qué finalidades*.

Marco normativo:
- LFPDPPP (arts. 15, 16, 36; derechos ARCO con plazo de 20 días hábiles).
- Ley Federal de Protección al Consumidor y NOM-174-SCFI-2007.
"""

import hashlib
import re
from datetime import timedelta

import markdown

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# Un documento con marcadores sin resolver no puede publicarse: saldrían a la
# vista del cliente cadenas como "[CONFIRMAR: +52 999 XXX XXXX]".
MARCADORES_SIN_RESOLVER = re.compile(r'\[(CONFIRMAR|PENDIENTE)\s*:', re.IGNORECASE)


class TipoDocumento(models.TextChoices):
    AVISO_PRIVACIDAD = 'AVISO_PRIVACIDAD', 'Aviso de Privacidad'
    AVISO_SIMPLIFICADO = 'AVISO_SIMPLIFICADO', 'Aviso de Privacidad Simplificado'
    TERMINOS = 'TERMINOS', 'Términos y Condiciones'
    POLITICA_CANCELACION = 'POLITICA_CANCELACION', 'Política de Cancelación y Reembolso'
    REGLAMENTO = 'REGLAMENTO', 'Reglamento Interno'


class DocumentoLegal(models.Model):
    """
    Versión inmutable de un documento legal publicado.

    Solo puede existir UNA versión vigente por tipo, garantizado con una
    restricción condicional a nivel de base de datos.
    """

    tipo = models.CharField(max_length=32, choices=TipoDocumento.choices, db_index=True)
    version = models.CharField(max_length=16, help_text="Ej. '2.0'")
    titulo = models.CharField(max_length=200)
    contenido_md = models.TextField(
        help_text="Contenido en Markdown. Inmutable una vez guardado.")
    hash_contenido = models.CharField(max_length=64, editable=False, db_index=True)
    vigente_desde = models.DateField()
    vigente = models.BooleanField(default=False, db_index=True)

    creado_en = models.DateTimeField(auto_now_add=True)
    creado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='documentos_legales_creados',
    )

    class Meta:
        verbose_name = 'Documento legal'
        verbose_name_plural = 'Documentos legales'
        ordering = ['tipo', '-vigente_desde']
        constraints = [
            models.UniqueConstraint(fields=['tipo', 'version'],
                                    name='uniq_doc_tipo_version'),
            models.UniqueConstraint(
                fields=['tipo'], condition=models.Q(vigente=True),
                name='uniq_doc_vigente_por_tipo',
            ),
        ]
        indexes = [models.Index(fields=['tipo', 'vigente'])]

    def __str__(self):
        return f"{self.get_tipo_display()} v{self.version}"

    @staticmethod
    def calcular_hash(contenido: str) -> str:
        """SHA-256 hexadecimal del contenido normalizado a UTF-8."""
        return hashlib.sha256(contenido.encode('utf-8')).hexdigest()

    @property
    def hash_corto(self) -> str:
        return self.hash_contenido[:12]

    # El encabezado del archivo (título H1, línea de versión y el separador que
    # los sigue) se muestra en la portada de la página, no dentro del cuerpo.
    _ENCABEZADO_DUPLICADO = re.compile(
        r'\A\s*#\s+[^\n]+\n'          # título H1
        r'(?:\s*\*\*[^\n]*\*\*\n)*'    # líneas en negritas (versión, razón social)
        r'\s*(?:-{3,}\s*\n)?',          # separador horizontal
    )

    def cuerpo_markdown(self) -> str:
        """Markdown sin el encabezado que ya presenta la portada de la página."""
        return self._ENCABEZADO_DUPLICADO.sub('', self.contenido_md or '', count=1)

    def render_html(self) -> str:
        """
        Contenido en HTML con tipografía real.

        El documento se redacta en Markdown, pero al cliente hay que
        entregárselo formateado: encabezados, tablas y listas, no los
        asteriscos y pipes del código fuente.
        """
        return markdown.markdown(
            self.cuerpo_markdown(),
            extensions=['tables', 'sane_lists', 'attr_list'],
            output_format='html',
        )

    def marcadores_pendientes(self) -> list:
        """Marcadores [CONFIRMAR:] / [PENDIENTE:] que quedan en el contenido."""
        return MARCADORES_SIN_RESOLVER.findall(self.contenido_md or '')

    def clean(self):
        if self.pk:
            original = DocumentoLegal.objects.filter(pk=self.pk).first()
            if original and original.contenido_md != self.contenido_md:
                raise ValidationError(
                    "El contenido de un documento publicado no puede modificarse. "
                    "Cree una versión nueva."
                )
        if self.vigente and self.marcadores_pendientes():
            raise ValidationError(
                "No se puede marcar como vigente un documento con marcadores sin "
                "resolver ([CONFIRMAR:] / [PENDIENTE:]). Se publicarían tal cual "
                "a la vista del cliente."
            )

    def save(self, *args, **kwargs):
        self.hash_contenido = self.calcular_hash(self.contenido_md)
        if self.vigente and self.marcadores_pendientes():
            raise ValidationError(
                "No se puede publicar un documento con marcadores sin resolver."
            )
        if self.vigente:
            DocumentoLegal.objects.filter(tipo=self.tipo, vigente=True) \
                                  .exclude(pk=self.pk).update(vigente=False)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Los documentos legales no se eliminan.")


class Finalidad(models.Model):
    """
    Catálogo de finalidades del tratamiento (art. 15 LFPDPPP).
    Distingue las que requieren consentimiento de las que no.
    """

    clave = models.SlugField(max_length=40, unique=True)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    requiere_consentimiento = models.BooleanField(default=True)
    orden = models.PositiveSmallIntegerField(default=0)
    activa = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Finalidad del tratamiento'
        verbose_name_plural = 'Finalidades del tratamiento'
        ordering = ['orden', 'clave']

    def __str__(self):
        return self.nombre


class OrigenAceptacion(models.TextChoices):
    FORM_COTIZACION = 'FORM_COTIZACION', 'Formulario público de cotización'
    PORTAL_CLIENTE = 'PORTAL_CLIENTE', 'Portal de clientes'
    CHECKOUT = 'CHECKOUT', 'Checkout de pago'
    CONTRATO = 'CONTRATO', 'Firma de contrato'
    ADMIN = 'ADMIN', 'Captura administrativa'


class AceptacionLegal(models.Model):
    """
    Evidencia inmutable del consentimiento.

    Es el registro que se exhibe ante una verificación o ante un contracargo.
    No se edita ni se elimina bajo ninguna circunstancia.
    """

    cliente = models.ForeignKey(
        'comercial.Cliente', null=True, blank=True,
        on_delete=models.PROTECT, related_name='aceptaciones_legales',
    )
    correo = models.EmailField(help_text="Correo declarado al momento de aceptar.")
    documentos = models.ManyToManyField(DocumentoLegal, related_name='aceptaciones')
    snapshot_documentos = models.JSONField(
        default=list,
        help_text="[{'tipo':..., 'version':..., 'hash':...}] congelado al aceptar.",
    )
    finalidades_aceptadas = models.JSONField(
        default=list, help_text="Claves de Finalidad consentidas.")
    finalidades_rechazadas = models.JSONField(default=list)

    origen = models.CharField(max_length=24, choices=OrigenAceptacion.choices)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    aceptado_en = models.DateTimeField(default=timezone.now, editable=False,
                                       db_index=True)

    class Meta:
        verbose_name = 'Aceptación legal'
        verbose_name_plural = 'Aceptaciones legales'
        ordering = ['-aceptado_en']
        indexes = [
            models.Index(fields=['cliente', '-aceptado_en']),
            models.Index(fields=['correo', '-aceptado_en']),
        ]

    def __str__(self):
        return f"Aceptación {self.pk} — {self.correo} — {self.aceptado_en:%Y-%m-%d %H:%M}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Las aceptaciones legales son inmutables.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Las aceptaciones legales no se eliminan.")


class TipoARCO(models.TextChoices):
    ACCESO = 'ACCESO', 'Acceso'
    RECTIFICACION = 'RECTIFICACION', 'Rectificación'
    CANCELACION = 'CANCELACION', 'Cancelación'
    OPOSICION = 'OPOSICION', 'Oposición'
    REVOCACION = 'REVOCACION', 'Revocación del consentimiento'


class EstadoARCO(models.TextChoices):
    RECIBIDA = 'RECIBIDA', 'Recibida'
    EN_TRAMITE = 'EN_TRAMITE', 'En trámite'
    PREVENCION = 'PREVENCION', 'Prevención (información faltante)'
    PROCEDENTE = 'PROCEDENTE', 'Procedente'
    IMPROCEDENTE = 'IMPROCEDENTE', 'Improcedente'


class SolicitudARCO(models.Model):
    """
    Bitácora de solicitudes de derechos ARCO.
    Plazo legal de respuesta: 20 días hábiles desde la recepción.
    """

    folio = models.CharField(max_length=24, unique=True, editable=False)
    tipo = models.CharField(max_length=16, choices=TipoARCO.choices)
    titular_nombre = models.CharField(max_length=200)
    correo = models.EmailField()
    telefono = models.CharField(max_length=20, blank=True)
    descripcion = models.TextField()
    identificacion = models.FileField(upload_to='arco/identificaciones/', blank=True)

    estado = models.CharField(max_length=16, choices=EstadoARCO.choices,
                              default=EstadoARCO.RECIBIDA)
    recibida_en = models.DateTimeField(default=timezone.now, editable=False)
    fecha_limite = models.DateField(editable=False)
    respondida_en = models.DateTimeField(null=True, blank=True)
    respuesta = models.TextField(blank=True)
    atendida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name='solicitudes_arco',
    )

    class Meta:
        verbose_name = 'Solicitud ARCO'
        verbose_name_plural = 'Solicitudes ARCO'
        ordering = ['-recibida_en']
        indexes = [models.Index(fields=['estado', 'fecha_limite'])]

    def __str__(self):
        return f"{self.folio} — {self.get_tipo_display()}"

    @staticmethod
    def sumar_dias_habiles(fecha, dias: int):
        """Suma días hábiles (lunes a viernes). No contempla días festivos."""
        actual = fecha
        restantes = dias
        while restantes > 0:
            actual += timedelta(days=1)
            if actual.weekday() < 5:
                restantes -= 1
        return actual

    def save(self, *args, **kwargs):
        if not self.pk:
            base = timezone.localtime(self.recibida_en).date()
            self.fecha_limite = self.sumar_dias_habiles(base, 20)
            if not self.folio:
                sello = timezone.localtime(self.recibida_en).strftime('%Y%m%d%H%M%S%f')
                self.folio = f"ARCO-{sello[:20]}"
        super().save(*args, **kwargs)

    @property
    def dias_restantes(self) -> int:
        """Días naturales para vencer el plazo legal. Negativo = vencido."""
        return (self.fecha_limite - timezone.localdate()).days
