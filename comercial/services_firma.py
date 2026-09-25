"""Firma electrónica de contratos desde el portal del cliente (Issue #318, fase 2).

Flujo: el cliente abre su contrato en el portal, pide un código de
verificación (le llega por correo), traza su firma en pantalla y la envía con
el código. Se genera el PDF firmado: el contrato original, sin tocar, más una
hoja de constancia con la evidencia. Validez: firma electrónica simple,
Código de Comercio art. 89 y ss. y Código Civil Federal art. 1834 bis.

El código nunca se guarda en claro (ni en la bitácora de comunicaciones): un
miembro del equipo con acceso al portal no debe poder firmar por el cliente.
"""
import base64
import hashlib
import io
import logging
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.files.base import ContentFile
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

VIGENCIA_CODIGO = timedelta(minutes=10)
ESPERA_REENVIO = timedelta(seconds=60)
MAX_INTENTOS = 5
MAX_BYTES_FIRMA = 300_000
FIRMA_PNG_PREFIJO = 'data:image/png;base64,'
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


class FirmaError(ValueError):
    """Error con mensaje apto para mostrarse al cliente."""


def sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def contrato_vigente(cotizacion):
    """El contrato más reciente de la cotización: es el único que se firma."""
    return cotizacion.contratos.exclude(archivo='').order_by('-generado_en').first()


def _leer(campo) -> bytes:
    with campo.open('rb') as f:
        return f.read()


def _firma_de(contrato):
    from .models import FirmaContrato

    firma, _ = FirmaContrato.objects.get_or_create(contrato=contrato)
    return firma


def solicitar_codigo(contrato, ahora=None):
    """Genera y envía por correo un código de 6 dígitos. Devuelve el destino.

    Fija también la huella del PDF que el cliente tiene enfrente: si el
    contrato se regenera antes de firmar, la firma se rechaza.
    """
    from comunicacion.services import remitente_por_tipo, reservar_comunicacion

    ahora = ahora or timezone.now()
    cotizacion = contrato.cotizacion
    email = (cotizacion.cliente.email or '').strip()
    if not email:
        raise FirmaError(
            "No tenemos un correo registrado para enviarte el código. "
            "Escríbenos para agregarlo."
        )

    with transaction.atomic():
        firma = _firma_de(contrato)
        firma = type(firma).objects.select_for_update().get(pk=firma.pk)
        if firma.firmado:
            raise FirmaError("Este contrato ya está firmado.")
        if firma.codigo_enviado_en and ahora - firma.codigo_enviado_en < ESPERA_REENVIO:
            raise FirmaError("Ya te enviamos un código. Espera un minuto para pedir otro.")

        codigo = f"{secrets.randbelow(10**6):06d}"
        firma.codigo_hash = make_password(codigo)
        firma.codigo_enviado_en = ahora
        firma.codigo_canal = 'EMAIL'
        firma.codigo_destino = email
        firma.intentos = 0
        firma.hash_documento = sha256(_leer(contrato.archivo))
        firma.save()

    asunto = f"Código para firmar tu contrato {contrato.numero}"
    html = render_to_string('comunicacion/email/codigo_firma.html', {
        'cotizacion': cotizacion,
        'contrato': contrato,
        'codigo': codigo,
        'minutos': int(VIGENCIA_CODIGO.total_seconds() // 60),
    })
    # El registro de la comunicación va sin el código, a propósito.
    comm = reservar_comunicacion(
        cotizacion=cotizacion, canal='EMAIL', tipo='CONTRATO', trigger='MANUAL',
        destinatario=email, asunto=asunto, estado='PENDIENTE',
        cuerpo='Código de verificación para firmar el contrato (no se guarda).',
    )
    try:
        msg = EmailMultiAlternatives(
            subject=asunto, body=strip_tags(html),
            from_email=remitente_por_tipo('CONTRATO'), to=[email],
        )
        msg.attach_alternative(html, 'text/html')
        msg.send(fail_silently=False)
        comm.estado = 'ENVIADO'
        comm.fecha_envio = timezone.now()
        comm.save(update_fields=['estado', 'fecha_envio'])
    except Exception as e:
        logger.exception("No se pudo enviar el código de firma del contrato %s", contrato.pk)
        comm.estado = 'FALLIDO'
        comm.error = str(e)[:1000]
        comm.save(update_fields=['estado', 'error'])
        raise FirmaError("No pudimos enviar el código. Intenta de nuevo en unos minutos.") from e
    return email


def _decodificar_firma(data_url: str) -> bytes:
    """Valida el trazo que manda el navegador: un PNG real y de tamaño razonable."""
    from PIL import Image

    if not data_url or not data_url.startswith(FIRMA_PNG_PREFIJO):
        raise FirmaError("Traza tu firma en el recuadro.")
    try:
        contenido = base64.b64decode(data_url[len(FIRMA_PNG_PREFIJO):], validate=True)
    except ValueError:
        raise FirmaError("La firma no es válida. Vuelve a trazarla.") from None
    if len(contenido) > MAX_BYTES_FIRMA or not contenido.startswith(PNG_MAGIC):
        raise FirmaError("La firma no es válida. Vuelve a trazarla.")
    try:
        with Image.open(io.BytesIO(contenido)) as img:
            img.verify()
        with Image.open(io.BytesIO(contenido)) as img:
            # Un lienzo en blanco (o casi) no es una firma.
            alfa = img.convert('RGBA').getchannel('A')
            if alfa.getbbox() is None:
                raise FirmaError("Traza tu firma en el recuadro.")
    except FirmaError:
        raise
    except Exception:
        raise FirmaError("La firma no es válida. Vuelve a trazarla.") from None
    return contenido


def _pdf_firmado(contrato, firma, pdf_original: bytes, png: bytes) -> bytes:
    """Contrato original + hoja de constancia, unidos sin alterar el original."""
    import pypdfium2
    from weasyprint import HTML

    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    html = render_to_string('contratos/constancia_firma.html', {
        'contrato': contrato,
        'cotizacion': contrato.cotizacion,
        'firma': firma,
        'firma_data_url': FIRMA_PNG_PREFIJO + base64.b64encode(png).decode(),
        'logo_url': f"file://{ruta_logo}",
        'fuentes_dir': f"file://{os.path.join(settings.BASE_DIR, 'static', 'fonts')}",
        'numero': contrato.numero,
    })
    constancia = HTML(string=html).write_pdf()

    documento = pypdfium2.PdfDocument(pdf_original)
    documento.import_pages(pypdfium2.PdfDocument(constancia))
    salida = io.BytesIO()
    documento.save(salida)
    return salida.getvalue()


def firmar(contrato, *, codigo, firma_data_url, nombre, ip, user_agent,
           acepta_publicidad=False, acepta_transmision=False, ahora=None):
    """Valida el código y la firma, y genera el PDF firmado. Devuelve la FirmaContrato."""
    from .models import FirmaContrato

    ahora = ahora or timezone.now()
    nombre = (nombre or '').strip()
    if len(nombre) < 3:
        raise FirmaError("Escribe tu nombre completo.")
    png = _decodificar_firma(firma_data_url)

    with transaction.atomic():
        firma = FirmaContrato.objects.select_for_update().filter(contrato=contrato).first()
        if firma is not None and firma.firmado:
            raise FirmaError("Este contrato ya está firmado.")
        if firma is None or not firma.codigo_hash:
            raise FirmaError("Primero pide tu código de verificación.")
        if firma.intentos >= MAX_INTENTOS:
            raise FirmaError("Demasiados intentos. Pide un código nuevo.")
        if ahora - firma.codigo_enviado_en > VIGENCIA_CODIGO:
            raise FirmaError("El código venció. Pide uno nuevo.")
        codigo_verificado = firma.codigo_hash
        codigo_ok = check_password((codigo or '').strip(), codigo_verificado)
        if not codigo_ok:
            # Se guarda y se sale del atomic antes de lanzar: si se lanzara
            # aquí dentro, el rollback desharía el intento fallido.
            FirmaContrato.objects.filter(pk=firma.pk).update(intentos=firma.intentos + 1)

    if not codigo_ok:
        raise FirmaError("El código no es correcto.")

    with transaction.atomic():
        firma = FirmaContrato.objects.select_for_update().get(pk=firma.pk)
        if firma.firmado:
            raise FirmaError("Este contrato ya está firmado.")
        if firma.codigo_hash != codigo_verificado:
            raise FirmaError("Pediste un código nuevo. Usa el más reciente.")

        pdf_original = _leer(contrato.archivo)
        if sha256(pdf_original) != firma.hash_documento:
            raise FirmaError(
                "El contrato cambió después de que pediste el código. "
                "Revísalo de nuevo y pide otro código."
            )

        firma.nombre_firmante = nombre[:200]
        firma.firmado_en = ahora
        firma.ip = ip or None
        firma.user_agent = (user_agent or '')[:300]
        firma.acepta_publicidad = bool(acepta_publicidad)
        firma.acepta_transmision = bool(acepta_transmision)
        firma.codigo_hash = ''  # un solo uso
        firma.imagen_firma.save(f'firma_{contrato.numero}.png', ContentFile(png), save=False)

        pdf = _pdf_firmado(contrato, firma, pdf_original, png)
        firma.hash_firmado = sha256(pdf)
        firma.archivo_firmado.save(f'Contrato_{contrato.numero}_firmado.pdf', ContentFile(pdf), save=False)
        firma.save()

    transaction.on_commit(lambda: _enviar_copia_firmada(firma.pk))
    return firma


def _enviar_copia_firmada(firma_pk):
    """Manda al cliente su contrato firmado. Un fallo aquí no deshace la firma."""
    from comunicacion.services import enviar_email

    from .models import FirmaContrato

    try:
        firma = FirmaContrato.objects.select_related('contrato__cotizacion__cliente').get(pk=firma_pk)
        cotizacion = firma.contrato.cotizacion
        enviar_email(
            cotizacion=cotizacion, tipo='CONTRATO', trigger='SIGNAL',
            destinatario=cotizacion.cliente.email or '',
            asunto=f"Tu contrato {firma.contrato.numero} firmado",
            template='comunicacion/email/contrato_firmado.html',
            context={'cotizacion': cotizacion, 'firma': firma},
            adjuntos=[(f'Contrato_{firma.contrato.numero}_firmado.pdf',
                       _leer(firma.archivo_firmado), 'application/pdf')],
            clave_idempotencia=f'contrato:{firma.contrato_id}:firmado',
        )
    except Exception:
        logger.exception("No se pudo enviar la copia del contrato firmado (firma %s)", firma_pk)
