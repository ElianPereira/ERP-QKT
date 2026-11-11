"""robots.txt, sitemap.xml e iconos que navegadores y buscadores piden solos."""
from django.conf import settings
from django.http import FileResponse, HttpResponse
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

DOMINIO_PUBLICO = 'https://quintakooxtanil.com'
RUTAS_SITEMAP = ('/', '/cotizar/', '/aviso-de-privacidad/', '/terminos-y-condiciones/',
                 '/politica-de-cancelacion/', '/reglamento/')
LOGO = settings.BASE_DIR / 'static' / 'img' / 'logo.png'


def _es_erp(request):
    return request.get_host().split(':')[0].lower().startswith('erp.')


@require_GET
@cache_control(max_age=3600)
def robots_txt(request):
    if _es_erp(request):
        cuerpo = 'User-agent: *\nDisallow: /\n'
    else:
        cuerpo = (
            'User-agent: *\n'
            'Allow: /\n'
            'Disallow: /admin/\n'
            'Disallow: /api/\n'
            'Disallow: /mi-evento/\n'
            'Disallow: /pagos/\n'
            f'\nSitemap: {DOMINIO_PUBLICO}{reverse("sitemap_xml")}\n'
        )
    return HttpResponse(cuerpo, content_type='text/plain; charset=utf-8')


@require_GET
@cache_control(max_age=3600)
def sitemap_xml(request):
    urls = ''.join(f'<url><loc>{DOMINIO_PUBLICO}{r}</loc></url>' for r in RUTAS_SITEMAP)
    cuerpo = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    )
    return HttpResponse(cuerpo, content_type='application/xml')


@require_GET
@cache_control(max_age=86400)
def favicon(request):
    return FileResponse(open(LOGO, 'rb'), content_type='image/png')  # noqa: SIM115


apple_touch_icon = favicon
