"""
Pantallas de acceso (login y 2FA) y páginas de error con el sistema de
diseño del admin. El login y el 2FA extienden registration/base.html, que
no pasa por templates/admin/base.html: si la base propia se pierde,
qkt_ui.css deja de llegar y vuelven a verse como antes, sin que falle nada.

Ejecutar: python manage.py test core_erp.test_pantallas_acceso
"""
from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


class PantallasAccesoTest(TestCase):
    def test_login_carga_qkt_ui_y_usa_sus_componentes(self):
        respuesta = self.client.get('/admin/login/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'css/qkt_ui.css')
        self.assertContains(respuesta, 'qkt-acceso__titulo')
        self.assertContains(respuesta, 'qkt-btn--primario')
        # Etiquetas visibles, no solo placeholder.
        self.assertContains(respuesta, '<label for="id_username">')
        self.assertContains(respuesta, 'autocomplete="current-password"')

    @override_settings(STATIC_VERSION='abc123')
    def test_login_versiona_qkt_ui_para_saltar_la_cache(self):
        respuesta = self.client.get('/admin/login/')
        self.assertContains(respuesta, 'css/qkt_ui.css?v=abc123')

    def test_login_fallido_muestra_el_aviso_de_error(self):
        respuesta = self.client.post('/admin/login/', {'username': 'nadie', 'password': 'mal'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'qkt-aviso--error')
        self.assertContains(respuesta, 'value="nadie"')

    def test_pantalla_de_activar_2fa_usa_los_componentes(self):
        superusuario = User.objects.create_superuser('dir_ui', 'dir_ui@quintakooxtanil.com', 'clave-de-prueba')
        self.client.force_login(superusuario)
        respuesta = self.client.get(reverse('totp_activar'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'css/qkt_ui.css')
        self.assertContains(respuesta, 'qkt-2fa__codigo')
        self.assertNotContains(respuesta, 'onclick=')


class PaginasErrorTest(TestCase):
    def test_las_cuatro_renderizan_sin_contexto(self):
        # Mismo camino que django.views.defaults.server_error: sin request.
        for nombre in ('400.html', '403.html', '404.html', '500.html'):
            with self.subTest(nombre=nombre):
                html = get_template(nombre).render({})
                self.assertIn('--ui-surface', html)
                self.assertIn('https://quintakooxtanil.com/', html)

    def test_whatsapp_lleva_al_agente_con_el_error_escrito(self):
        for codigo in ('400', '403', '404', '500'):
            with self.subTest(codigo=codigo):
                html = get_template(f'{codigo}.html').render({})
                self.assertIn('https://wa.me/529991699191?text=', html)
                self.assertIn(f'error%20{codigo}', html)
                self.assertNotIn('529994457178', html)
