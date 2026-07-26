"""
Rate limiting simple basado en cache de Django.
Uso:
    @rate_limit(key='mi_vista', limit=30, window=60)
    def my_view(request): ...
"""
from functools import wraps
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


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
            bucket = f'rl:{key}:{ip}'
            count = cache.get(bucket, 0)
            if count >= limit:
                return HttpResponse(
                    'Rate limit exceeded',
                    status=429,
                    headers={'Retry-After': str(window)},
                )
            try:
                cache.incr(bucket)
            except ValueError:
                cache.set(bucket, 1, timeout=window)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
