"""Context processors propios del proyecto."""
from django.conf import settings


def session_idle(request):
    """
    Expone el timeout de inactividad (en minutos) a las plantillas para que el
    auto-logout del navegador use exactamente el mismo valor que el backend
    (SESSION_IDLE_TIMEOUT), evitando desincronización.
    """
    timeout = getattr(settings, 'SESSION_IDLE_TIMEOUT', 1800)
    return {'session_idle_minutes': max(1, int(timeout) // 60)}


def version_estaticos(request):
    """Sufijo `?v=<commit>` para los estáticos propios del admin (ver
    STATIC_VERSION en settings): cada deploy los descarga de nuevo."""
    version = getattr(settings, 'STATIC_VERSION', '')
    return {'static_v': f'?v={version}' if version else ''}
