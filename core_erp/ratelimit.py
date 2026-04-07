"""
Rate limiting simple basado en cache de Django.
Uso:
    @rate_limit(key='webhook_manychat', limit=30, window=60)
    def my_view(request): ...
"""
from functools import wraps
from django.core.cache import cache
from django.http import HttpResponse


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
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
