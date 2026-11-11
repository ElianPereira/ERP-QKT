from django.test import TestCase

from core_erp.middleware import PUBLIC_CSP


class SeoTest(TestCase):
    def test_robots_publico_apunta_al_sitemap_y_cierra_lo_privado(self):
        r = self.client.get('/robots.txt', secure=True)
        self.assertEqual(r.status_code, 200)
        cuerpo = r.content.decode()
        self.assertIn('Disallow: /admin/', cuerpo)
        self.assertIn('Sitemap: https://quintakooxtanil.com/sitemap.xml', cuerpo)

    def test_robots_del_erp_bloquea_todo(self):
        r = self.client.get('/robots.txt', secure=True, HTTP_HOST='erp.quintakooxtanil.com')
        self.assertIn('Disallow: /\n', r.content.decode())

    def test_sitemap_lista_la_landing_y_el_cotizador(self):
        r = self.client.get('/sitemap.xml', secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'<loc>https://quintakooxtanil.com/cotizar/</loc>', r.content)

    def test_iconos_responden_png(self):
        for ruta in ('/favicon.ico', '/apple-touch-icon.png', '/apple-touch-icon-precomposed.png'):
            r = self.client.get(ruta, secure=True)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r['Content-Type'], 'image/png')

    def test_csp_publica_permite_el_beacon_de_cloudflare(self):
        self.assertIn('https://static.cloudflareinsights.com', PUBLIC_CSP)
        self.assertIn('https://cloudflareinsights.com', PUBLIC_CSP)
