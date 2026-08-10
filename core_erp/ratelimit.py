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
    backend conserve el TTL al incrementar. DatabaseCache no hace el incr de
    forma atómica y puede perder un incremento concurrente; Redis sí es exacto.
    """
    if cache.add(bucket, 1, timeout=window * 2):
        return 1
    try:
        return cache.incr(bucket)
    except ValueError:
        # La clave expiró entre add() e incr().
        cache.set(bucket, 1, timeout=window * 2)
        return 1


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
    """Borra los contadores por IP y usuario después de un login correcto."""
    bucket_ip, bucket_usuario = _buckets_login(request, username)
    if bucket_ip:
        cache.delete(bucket_ip)
    if bucket_usuario:
        cache.delete(bucket_usuario)
