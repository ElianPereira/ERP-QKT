"""Reseñas de Google Business Profile (Issue #468, fase 7).

Cliente REST directo (sin SDK). Apagado si falta cualquiera de las cuatro variables `GOOGLE_BUSINESS_*`. Solo se guarda
lo necesario: id, estrellas, fechas y si ya tiene respuesta; nunca el nombre ni el texto de quien reseña. Una reseña de
3 estrellas o menos, reciente y sin respuesta abre un `Pendiente` URGENTE para DIRECCION (idempotente por id de reseña).
Nota: el endpoint de reseñas es el v4 de Business Profile (`mybusiness.googleapis.com`); no se pudo verificar contra la
documentación vigente desde el contenedor, probar con credenciales reales.
"""
import logging
from datetime import timedelta
from typing import Optional

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..models import ResenaGoogle

logger = logging.getLogger(__name__)

OAUTH_URL = 'https://oauth2.googleapis.com/token'  # noqa: S105 (URL pública, no una credencial)
API_URL = 'https://mybusiness.googleapis.com/v4/{ficha}/reviews'
TIMEOUT = 20
ESTRELLAS = {'ONE': 1, 'TWO': 2, 'THREE': 3, 'FOUR': 4, 'FIVE': 5}
UMBRAL_AVISO = 3
DIAS_AVISO = 14  # no inundar de urgentes con reseñas viejas la primera vez
MAX_PAGINAS = 5


class GoogleError(Exception):
    pass


def _motivo(r) -> str:
    """`status` y `message` del error que devuelve Google (nunca el cuerpo completo ni el token)."""
    try:
        error = r.json().get('error') or {}
        return f" {error.get('status', '')}: {str(error.get('message', ''))[:200]}".rstrip(': ')
    except (ValueError, AttributeError):
        return ''


def configurado() -> bool:
    return all(getattr(settings, n, '') for n in (
        'GOOGLE_BUSINESS_CLIENT_ID', 'GOOGLE_BUSINESS_CLIENT_SECRET',
        'GOOGLE_BUSINESS_REFRESH_TOKEN', 'GOOGLE_BUSINESS_LOCATION'))


def _token() -> str:
    r = requests.post(OAUTH_URL, data={
        'client_id': settings.GOOGLE_BUSINESS_CLIENT_ID, 'client_secret': settings.GOOGLE_BUSINESS_CLIENT_SECRET,
        'refresh_token': settings.GOOGLE_BUSINESS_REFRESH_TOKEN, 'grant_type': 'refresh_token'}, timeout=TIMEOUT)
    if r.status_code != 200 or not r.json().get('access_token'):
        raise GoogleError(f'Google rechazó el token (HTTP {r.status_code}).')
    return r.json()['access_token']


def listar_resenas() -> list:
    """Reseñas del perfil (más recientes primero, hasta `MAX_PAGINAS` páginas)."""
    token = _token()
    url = API_URL.format(ficha=settings.GOOGLE_BUSINESS_LOCATION.strip('/'))
    resenas, pagina = [], None
    for _ in range(MAX_PAGINAS):
        params = {'pageSize': 50, 'orderBy': 'updateTime desc'}
        if pagina:
            params['pageToken'] = pagina
        r = requests.get(url, params=params, headers={'Authorization': f'Bearer {token}'}, timeout=TIMEOUT)
        if r.status_code != 200:
            raise GoogleError(f'Google respondió HTTP {r.status_code} al listar reseñas.{_motivo(r)}')
        cuerpo = r.json()
        resenas.extend(cuerpo.get('reviews') or [])
        pagina = cuerpo.get('nextPageToken')
        if not pagina:
            break
    return resenas


def _fecha(texto) -> Optional[object]:
    return parse_datetime(texto) if texto else None


def sincronizar(ahora=None, resenas=None) -> dict:
    """Guarda las reseñas nuevas o modificadas y avisa de las malas. Devuelve conteos; nunca lanza por datos raros."""
    from comunicacion.services_pendientes import registrar_pendiente

    if resenas is None:
        if not configurado():
            return {'apagado': True, 'nuevas': 0, 'avisos': 0}
        resenas = listar_resenas()
    ahora = ahora or timezone.now()
    nuevas = avisos = 0
    for datos in resenas:
        id_ext = str(datos.get('reviewId') or datos.get('name') or '')[:200]
        estrellas = ESTRELLAS.get(datos.get('starRating'))
        if not id_ext or estrellas is None:
            continue
        publicada = _fecha(datos.get('createTime'))
        resena, creada = ResenaGoogle.objects.update_or_create(
            id_externo=id_ext,
            defaults={'estrellas': estrellas, 'publicada_en': publicada,
                      'actualizada_en': _fecha(datos.get('updateTime')), 'respondida': bool(datos.get('reviewReply'))})
        nuevas += creada
        reciente = publicada is None or ahora - publicada <= timedelta(days=DIAS_AVISO)
        if estrellas <= UMBRAL_AVISO and not resena.respondida and reciente and not resena.avisada:
            fecha = f'{timezone.localtime(publicada):%d/%m/%Y}' if publicada else 'reciente'
            pendiente = registrar_pendiente(
                titulo=f'Reseña de {estrellas} estrella{"s" if estrellas != 1 else ""} en Google',
                rol='DIRECCION', tipo='QUEJA', severidad='URGENTE',
                detalle=f'Reseña del {fecha} con {estrellas} de 5 estrellas y sin respuesta. Léela y respóndela '
                        'en tu perfil de Google Business Profile.',
                clave_idempotencia=f'resena_google:{id_ext}')
            ResenaGoogle.objects.filter(pk=resena.pk).update(avisada=True)
            avisos += pendiente is not None
    return {'apagado': False, 'nuevas': nuevas, 'avisos': avisos}
