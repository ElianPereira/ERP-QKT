"""
Rate limiting simple basado en cache de Django.
Uso:
    @rate_limit(key='mi_vista', limit=30, window=60)
    def my_view(request): ...
"""
import hashlib
import logging
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

logger = logging.getLogger(__name__)


def _client_ip(request):
    """
    Devuelve la IP del cliente de forma resistente a spoofing.

    X-Forwarded-For lo puede fijar el propio cliente, así que tomar el primer
    valor permitiría a un atacante rotar IPs falsas y saltarse el rate limit.
    Cada proxy de confianza (el edge de Railway) *añade* la IP real al final de
    la cabecera, por lo que la entrada fiable es la que agregó nuestro proxy:
    contando desde la derecha tantos saltos como proxies de confianza haya.

    Se controla con RATELIMIT_TRUSTED_PROXY_COUNT (por defecto 1, el edge de
    Railway). Si no hay XFF, cae a REMOTE_ADDR.
    """
    trusted = getattr(settings, 'RATELIMIT_TRUSTED_PROXY_COUNT', 1)
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        partes = [p.strip() for p in xff.split(',') if p.strip()]
        if partes:
            # Índice contando desde la derecha: la IP que añadió nuestro proxy
            # de confianza. Se acota para no salirnos de la lista si un atacante
            # manda menos entradas de las esperadas.
            idx = max(0, len(partes) - max(1, trusted))
            return partes[idx]
    return request.META.get('REMOTE_ADDR', 'unknown')


def _bucket(key, ident, window):
    """Genera la clave del contador incluyendo su ventana fija actual."""
    return f'rl:{key}:{ident}:{int(time.time() // window)}'


def _contar(bucket, window):
    """
    Incrementa y devuelve un contador con expiración de respaldo.

    La ventana forma parte de la clave, por lo que no se depende de que el
    backend conserve el TTL al incrementar. No se usa cache.incr(): su
    implementación base hace un set() sin timeout explícito, que en
    DatabaseCache cae al TIMEOUT global de settings.py (3600 s) en vez de
    respetar los window*2 fijados por el add() inicial. Un bucket que vive
    30x más de lo previsto es candidato temprano al cull por orden
    lexicográfico de cache_key — y 'pago_openpay_en_curso:' ordena antes que
    'rl:', así que el candado anti-doble-cobro de Openpay sería el primero en
    perderse. Por eso cada escritura fija su propio timeout.
    """
    if cache.add(bucket, 1, timeout=window * 2):
        return 1
    valor = cache.get(bucket)
    if valor is None:
        # La clave expiró entre el add() y este get().
        cache.set(bucket, 1, timeout=window * 2)
        return 1
    valor += 1
    cache.set(bucket, valor, timeout=window * 2)
    return valor


def rate_limit(key, limit=60, window=60):
    """
    Decorador que limita peticiones por IP.

    Args:
        key: prefijo del bucket en cache
        limit: máximo de requests permitidos
        window: ventana de tiempo en segundos
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            ip = _client_ip(request)
            bucket = _bucket(key, ip, window)
            if _contar(bucket, window) > limit:
                return HttpResponse(
                    'Rate limit exceeded',
                    status=429,
                    headers={'Retry-After': str(window)},
                )
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def reset_rate_limit(key, ident, window=60):
    """Borra el contador de rate limit de la ventana fija actual."""
    cache.delete(_bucket(key, ident, window))


def _clave_usuario(username):
    """Hashea el usuario para no guardar nombres en claro en la tabla cache."""
    normalizado = (username or '').strip().lower()
    return hashlib.sha256(normalizado.encode()).hexdigest()[:32]


def _buckets_login(request, username):
    """Devuelve los buckets vigentes por IP y usuario, si están disponibles."""
    ventana = settings.ADMIN_LOGIN_VENTANA
    ip = _client_ip(request) if request is not None else None
    bucket_ip = _bucket('login_admin_ip', ip, ventana) if ip else None
    bucket_usuario = (
        _bucket('login_admin_user', _clave_usuario(username), ventana)
        if username else None
    )
    return bucket_ip, bucket_usuario


def login_bloqueado(request, username=None):
    """Indica si la IP o el usuario agotaron los intentos fallidos permitidos."""
    bucket_ip, bucket_usuario = _buckets_login(request, username)
    if bucket_ip and cache.get(bucket_ip, 0) >= settings.ADMIN_LOGIN_MAX_INTENTOS_IP:
        return True
    if bucket_usuario and cache.get(bucket_usuario, 0) >= settings.ADMIN_LOGIN_MAX_INTENTOS_USUARIO:
        return True
    return False


def registrar_login_fallido(request, username):
    """Registra un fallo de autenticación, tolerando señales sin request."""
    if request is None:
        return

    ventana = settings.ADMIN_LOGIN_VENTANA
    bucket_ip, bucket_usuario = _buckets_login(request, username)
    if bucket_ip:
        _contar(bucket_ip, ventana)
    if bucket_usuario:
        _contar(bucket_usuario, ventana)
    logger.warning(
        'Intento fallido de login admin (IP %s, usuario %s).',
        _client_ip(request),
        username or '',
    )


def limpiar_intentos_login(request, username):
    """Borra el contador del usuario que autenticó correctamente.

    El bucket por IP NO se toca aquí a propósito: si un login válido lo
    limpiara, cualquiera con credenciales de una sola cuenta podría alternar
    intentos fallidos contra otros usuarios (password spraying) con un login
    propio correcto desde la misma IP y anular así el límite por IP. Ese
    bucket solo expira por ventana (ADMIN_LOGIN_VENTANA).
    """
    _, bucket_usuario = _buckets_login(request, username)
    if bucket_usuario:
        cache.delete(bucket_usuario)


def _clave_cotizacion(cotizacion_id):
    """Hashea el id de cotización, igual que _clave_usuario con el username."""
    return hashlib.sha256(str(cotizacion_id).encode()).hexdigest()[:32]


def _bucket_portal(cotizacion_id):
    ventana = settings.PORTAL_ACCESO_VENTANA
    return _bucket('portal_acceso_cot', _clave_cotizacion(cotizacion_id), ventana)


def portal_acceso_bloqueado(cotizacion_id):
    """Indica si esa cotización agotó los intentos de acceso al portal.

    El bucket es por cotización, no por IP: un atacante que reparte los
    intentos entre IPs distintas para saltarse `rate_limit(key='portal_acceso')`
    sigue topando aquí, porque los 4 dígitos del teléfono son el mismo
    objetivo sin importar desde dónde se prueben.
    """
    bucket = _bucket_portal(cotizacion_id)
    return cache.get(bucket, 0) >= settings.PORTAL_ACCESO_MAX_INTENTOS


def registrar_portal_acceso_fallido(cotizacion_id):
    _contar(_bucket_portal(cotizacion_id), settings.PORTAL_ACCESO_VENTANA)


def limpiar_portal_acceso(cotizacion_id):
    """Borra el contador tras un acceso correcto al portal."""
    cache.delete(_bucket_portal(cotizacion_id))
