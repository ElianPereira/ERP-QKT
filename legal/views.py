from django.http import Http404
from django.shortcuts import render
from django.views.decorators.cache import cache_page

from .models import TipoDocumento
from .services import LegalService


@cache_page(60 * 60)
def documento_publico(request, tipo):
    """Renderiza siempre la versión vigente. Público, sin autenticación."""
    if tipo not in TipoDocumento.values:
        raise Http404
    documento = LegalService.documento_vigente(tipo)
    if not documento:
        raise Http404
    return render(request, 'legal/documento.html', {'documento': documento})
