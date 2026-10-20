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


def salud_programador(request):
    """Aviso solo para superusuarios en el admin cuando el programador único
    lleva más de 30 min sin registrar una corrida (Issue #423)."""
    usuario = getattr(request, 'user', None)
    if not (usuario and usuario.is_superuser and request.path.startswith('/admin/')):
        return {}
    from operaciones.services_programador import salud_programador as salud
    ultima = salud()
    return {'programador_ultima_vuelta': ultima} if ultima else {}
