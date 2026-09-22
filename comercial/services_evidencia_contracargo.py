"""
Armado y envío de evidencia de un Contracargo a Openpay (Issue #305).

Openpay no tiene API para disputar un contracargo — es un correo manual a
soporte@openpay.mx dentro del plazo de 3 días hábiles (confirmado con su
soporte, caso CS1234019). Este módulo automatiza la parte que sí se puede
automatizar: reunir la evidencia que el ERP ya tiene documentada en un PDF,
y mandarla — con un clic desde el admin, o, si nadie lo hizo a tiempo, con
un envío automático de última instancia antes de que venza el plazo (ver
`comercial.management.commands.enviar_evidencia_contracargos_pendientes`).

Ninguna función de aquí lanza fuera de su propio alcance de forma que rompa
al llamador (webhook, admin o cron): siempre hay algo razonable que devolver.
"""
import logging

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.utils import timezone
from weasyprint import HTML

logger = logging.getLogger(__name__)

OPENPAY_SOPORTE_EMAIL = 'soporte@openpay.mx'


def _contexto_evidencia(contracargo):
    cotizacion = contracargo.cotizacion
    pagos = list(cotizacion.pagos.order_by('fecha_pago')) if cotizacion else []
    comunicaciones = (
        list(cotizacion.comunicaciones.filter(
            estado__in=['ENVIADO', 'ENTREGADO', 'ABIERTO'],
        ).order_by('fecha_envio'))
        if cotizacion else []
    )
    return {
        'contracargo': contracargo,
        'transaccion': contracargo.transaccion_openpay,
        'cotizacion': cotizacion,
        'cliente': getattr(cotizacion, 'cliente', None),
        'pagos': pagos,
        'comunicaciones': comunicaciones,
        'generado_en': timezone.now(),
    }


def armar_evidencia(contracargo):
    """
    Genera el PDF de evidencia y lo guarda en `Contracargo.evidencia_pdf`.

    Idempotente: si ya existe un PDF armado, no lo regenera — se arma una
    sola vez al entrar en disputa. Nunca lanza: si algo falla, deja
    `evidencia_pdf` vacío y lo registra en el log; `enviar_evidencia_a_openpay`
    revisa ese caso antes de intentar mandar nada.
    """
    if contracargo.evidencia_pdf:
        return contracargo.evidencia_pdf
    try:
        html = render_to_string(
            'comercial/pdf/evidencia_contracargo.html', _contexto_evidencia(contracargo),
        )
        pdf_bytes = HTML(string=html).write_pdf()
    except Exception:
        logger.exception(
            "Contracargo %s: no se pudo armar el PDF de evidencia", contracargo.openpay_id,
        )
        return None
    nombre = f"evidencia_contracargo_{contracargo.openpay_id}.pdf"
    contracargo.evidencia_pdf.save(nombre, ContentFile(pdf_bytes), save=True)
    return contracargo.evidencia_pdf


def enviar_evidencia_a_openpay(contracargo, usuario=None):
    """
    Manda el PDF de evidencia (+ el contrato de la cotización, si existe) a
    soporte@openpay.mx. `usuario=None` es la marca de un envío automático
    (cron de última instancia); con `usuario` explícito queda registrado
    quién lo mandó a mano desde el admin.

    Nunca lanza — devuelve (ok: bool, mensaje: str).
    """
    if contracargo.evidencia_enviada:
        return False, "La evidencia de este contracargo ya se había enviado antes."

    if not contracargo.evidencia_pdf:
        armar_evidencia(contracargo)
    if not contracargo.evidencia_pdf:
        return False, "No se pudo armar el PDF de evidencia; revisar el log."

    cotizacion = contracargo.cotizacion
    folio = f"COT-{cotizacion.pk:03d}" if cotizacion else contracargo.openpay_id

    try:
        email = EmailMessage(
            subject=f"Evidencia contracargo {contracargo.openpay_id} — {folio}",
            body=(
                f"Adjunto evidencia del contracargo {contracargo.openpay_id} "
                f"(cotización {folio}) para disputarlo dentro del plazo del "
                f"{contracargo.fecha_limite_evidencia}."
            ),
            from_email=settings.EMAIL_FROM_NOTIFICACIONES,
            to=[OPENPAY_SOPORTE_EMAIL],
        )
        contracargo.evidencia_pdf.open('rb')
        try:
            email.attach(
                f"evidencia_contracargo_{contracargo.openpay_id}.pdf",
                contracargo.evidencia_pdf.read(), 'application/pdf',
            )
        finally:
            contracargo.evidencia_pdf.close()

        contrato = cotizacion.contratos.order_by('-generado_en').first() if cotizacion else None
        if contrato and contrato.archivo:
            contrato.archivo.open('rb')
            try:
                email.attach(
                    f"contrato_{contrato.numero}.pdf", contrato.archivo.read(), 'application/pdf',
                )
            finally:
                contrato.archivo.close()

        email.send()
    except Exception as e:
        logger.exception(
            "Contracargo %s: falló el envío de evidencia a Openpay", contracargo.openpay_id,
        )
        return False, str(e)

    contracargo.evidencia_enviada = True
    contracargo.evidencia_enviada_por = usuario
    contracargo.fecha_evidencia_enviada = timezone.now()
    contracargo.save(update_fields=[
        'evidencia_enviada', 'evidencia_enviada_por', 'fecha_evidencia_enviada',
    ])
    return True, ''
