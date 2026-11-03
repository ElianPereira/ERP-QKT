"""Receptor de reportes de violación de CSP (Issue #460, Fase 3)."""
import json
import logging
from urllib.parse import urlsplit

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core_erp.ratelimit import rate_limit

logger = logging.getLogger('django.security')

MAX_BYTES = 8 * 1024


def _sin_query(uri):
    """Descarta query y fragmento: los tokens del portal pueden ir ahí."""
    partes = urlsplit(str(uri)[:500])
    return f'{partes.scheme}://{partes.netloc}{partes.path}' if partes.scheme else partes.path


@csrf_exempt
@require_POST
@rate_limit(key='csp_report', limit=60, window=60)
def csp_report(request):
    if len(request.body) > MAX_BYTES:
        return HttpResponse(status=413)
    try:
        datos = json.loads(request.body or b'{}')
        informe = datos.get('csp-report', datos) if isinstance(datos, dict) else {}
    except ValueError:
        return HttpResponse(status=400)
    if isinstance(informe, dict):
        logger.warning(
            'CSP violación: directiva=%s bloqueado=%s documento=%s',
            str(informe.get('effective-directive') or informe.get('violated-directive', ''))[:100],
            _sin_query(informe.get('blocked-uri', '')),
            _sin_query(informe.get('document-uri', '')),
        )
    return HttpResponse(status=204)
