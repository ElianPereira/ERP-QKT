import hashlib

from django.core.cache import cache
from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML as WeasyHTML

from .models import (
    ConfiguracionCatalogo, BadgeServicio, SeccionCatalogo, PaqueteCatalogo,
    TarjetaCatalogo, DescuentoTarjeta, QuienesSomos, EstadisticaQuienesSomos,
    PasoProceso, SeccionBadge, OcasionCard, GaleriaSeccion, GaleriaSeccionBullet,
    GaleriaSeccionFoto, ItemPaqueteCatalogo, CaracteristicaSeccion,
)

# Tablas cuyo estado afecta el contenido del PDF. Se combinan count() +
# max(actualizado_en) — el count() detecta altas/bajas incluso en modelos
# sin auto_now (ej. bullets, badges, fotos, items) donde editar una fila
# hija no dispara el timestamp de su padre (Django admin no re-guarda el
# padre al guardar un inline). Robusto ante nuevos modelos sin timestamp.
_MODELOS_CON_TIMESTAMP = [
    ConfiguracionCatalogo, SeccionCatalogo, TarjetaCatalogo, DescuentoTarjeta,
    PaqueteCatalogo, QuienesSomos, GaleriaSeccion, BadgeServicio,
    EstadisticaQuienesSomos, PasoProceso,
]
_MODELOS_SIN_TIMESTAMP = [
    SeccionBadge, OcasionCard, GaleriaSeccionBullet, GaleriaSeccionFoto,
    ItemPaqueteCatalogo, CaracteristicaSeccion,
]


def _hash_estado_catalogo():
    """
    Genera una huella del estado actual del catálogo. Si nada cambió, el
    hash es igual y se sirve el PDF cacheado sin regenerar.
    """
    partes = []

    for modelo in _MODELOS_CON_TIMESTAMP:
        ultimo = modelo.objects.order_by('-actualizado_en').first()
        partes.append(str(ultimo.actualizado_en) if ultimo else '0')

    for modelo in _MODELOS_SIN_TIMESTAMP:
        partes.append(str(modelo.objects.count()))

    firma = '|'.join(partes)
    return hashlib.md5(firma.encode()).hexdigest()


def descargar_catalogo_pdf(request):
    cache_key = f"catalogo_pdf_{_hash_estado_catalogo()}"
    pdf_bytes = cache.get(cache_key)

    if pdf_bytes is None:
        config = ConfiguracionCatalogo.cargar()
        secciones = SeccionCatalogo.objects.filter(activa=True).prefetch_related(
            'caracteristicas', 'tarjetas__producto', 'tarjetas__descuento',
            'badges_cover', 'ocasiones', 'galeria__bullets', 'galeria__fotos',
        )
        paquetes = PaqueteCatalogo.objects.filter(activo=True).prefetch_related('items')
        badges = BadgeServicio.objects.all()
        quienes_somos = QuienesSomos.objects.first()
        estadisticas = EstadisticaQuienesSomos.objects.all()
        pasos = PasoProceso.objects.all()

        html_string = render_to_string('catalogo/catalogo_pdf.html', {
            'config': config,
            'secciones': secciones,
            'paquetes': paquetes,
            'badges': badges,
            'quienes_somos': quienes_somos,
            'estadisticas': estadisticas,
            'pasos': pasos,
        })
        pdf_bytes = WeasyHTML(
            string=html_string,
            base_url=request.build_absolute_uri('/'),
        ).write_pdf()
        cache.set(cache_key, pdf_bytes, timeout=60 * 60 * 24)  # 24h

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'inline; filename="Catalogo_QKT.pdf"'
    # El servidor ya cachea por 24h con invalidación automática (hash de
    # actualizado_en); evita que el navegador guarde su propia copia y
    # muestre una versión vieja tras un cambio en el admin.
    response['Cache-Control'] = 'no-cache, must-revalidate'
    return response
