"""
Bajas de mensajes de promoción por WhatsApp.

Quien escribe «BAJA» (o se lo pide a Kooxi con sus palabras) deja de recibir
los seguimientos de cotización y cualquier mensaje de marketing que filtre
con `dado_de_baja`. Los avisos de su propia reservación (pagos, guía,
contrato) no son promoción y siguen llegando.
"""
import re
import unicodedata

from django.utils import timezone

from .models import BajaWhatsApp, ConversacionWhatsApp
from .services import normalizar_telefono_wa

# El mensaje completo debe ser una de estas frases: «¿la baja de mi evento?»
# no es una baja. Lo demás lo resuelve el agente con su herramienta.
PALABRAS_BAJA = {
    'baja', 'darme de baja', 'dame de baja', 'dar de baja', 'quiero darme de baja',
    'stop', 'alto', 'ya no me manden mensajes', 'no quiero mas mensajes',
}

CONFIRMACION_BAJA = ('Listo, ya no te enviaremos promociones ni seguimientos por este medio. Los avisos '
                     'de tu reservación (pagos y detalles de tu evento) te siguen llegando. Si necesitas '
                     'algo, escríbenos cuando quieras.')


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize('NFKD', texto or '').encode('ascii', 'ignore').decode().lower()
    return ' '.join(re.sub(r'[^a-z0-9 ]', ' ', texto).split())


def es_palabra_baja(texto: str) -> bool:
    return _normalizar(texto) in PALABRAS_BAJA


def _ultimos_10(telefono: str) -> str:
    return ''.join(filter(str.isdigit, telefono or ''))[-10:]


def registrar_baja(*, telefono: str, origen: str, texto: str = '', wamid: str = ''):
    """Guarda la baja y apaga el permiso de promociones del chat (si el cliente
    cotiza después en el chat, no se registra MARKETING)."""
    telefono = normalizar_telefono_wa(telefono)
    if len(_ultimos_10(telefono)) < 10:
        return None
    baja = BajaWhatsApp.objects.create(telefono=telefono, origen=origen, texto=(texto or '')[:300],
                                       wamid=(wamid or '')[:191])
    ConversacionWhatsApp.objects.filter(telefono=telefono).update(
        consentimiento_marketing=False, updated_at=timezone.now())
    return baja


def dado_de_baja(telefono: str, cliente=None) -> bool:
    """¿Pidió no recibir promociones? Una aceptación de promociones posterior
    a la baja (cotizador web o botón del chat) la deja sin efecto."""
    from legal.models import AceptacionLegal

    ultimos = _ultimos_10(telefono)
    if len(ultimos) < 10:
        return False
    baja = BajaWhatsApp.objects.filter(telefono__endswith=ultimos).order_by('-created_at').first()
    if baja is None:
        return False
    if cliente is not None:
        ultima = (AceptacionLegal.objects.filter(cliente=cliente).order_by('-aceptado_en')
                  .values_list('aceptado_en', 'finalidades_aceptadas').first())
        if ultima and ultima[0] > baja.created_at and 'MARKETING' in (ultima[1] or []):
            return False
    return True
