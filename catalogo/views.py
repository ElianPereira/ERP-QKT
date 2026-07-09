import hashlib

from django.core.cache import cache
from django.http import HttpResponse
from django.template.loader import render_to_string
from weasyprint import HTML as WeasyHTML

from .models import (
    ConfiguracionCatalogo, BadgeServicio, SeccionCatalogo, PaqueteCatalogo,
    TarjetaCatalogo, DescuentoTarjeta,
)


def _hash_estado_catalogo():
    """
    Genera una huella del estado actual del catálogo usando la última
    modificación de cada tabla relevante (campo actualizado_en). Si nada
    cambió, el hash es igual y se sirve el PDF cacheado sin regenerar.
    """
    partes = []

    config = ConfiguracionCatalogo.objects.order_by('-actualizado_en').first()
    partes.append(str(config.actualizado_en) if config else '0')

    ultima_seccion = SeccionCatalogo.objects.order_by('-actualizado_en').first()
    partes.append(str(ultima_seccion.actualizado_en) if ultima_seccion else '0')

    ultima_tarjeta = TarjetaCatalogo.objects.order_by('-actualizado_en').first()
    partes.append(str(ultima_tarjeta.actualizado_en) if ultima_tarjeta else '0')

    ultimo_descuento = DescuentoTarjeta.objects.order_by('-actualizado_en').first()
    partes.append(str(ultimo_descuento.actualizado_en) if ultimo_descuento else '0')

    ultimo_paquete = PaqueteCatalogo.objects.order_by('-actualizado_en').first()
    partes.append(str(ultimo_paquete.actualizado_en) if ultimo_paquete else '0')

    firma = '|'.join(partes)
    return hashlib.md5(firma.encode()).hexdigest()


def descargar_catalogo_pdf(request):
    cache_key = f"catalogo_pdf_{_hash_estado_catalogo()}"
    pdf_bytes = cache.get(cache_key)

    if pdf_bytes is None:
        config = ConfiguracionCatalogo.cargar()
        secciones = SeccionCatalogo.objects.filter(activa=True).prefetch_related(
            'caracteristicas', 'tarjetas__producto', 'tarjetas__descuento'
        )
        paquetes = PaqueteCatalogo.objects.filter(activo=True).prefetch_related('items')
        badges = BadgeServicio.objects.all()

        html_string = render_to_string('catalogo/catalogo_pdf.html', {
            'config': config,
            'secciones': secciones,
            'paquetes': paquetes,
            'badges': badges,
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
