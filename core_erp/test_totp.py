"""
Orden 42 del backlog de seguridad (`SEC-AUTHN-002`): TOTP obligatorio para
superusuarios. `SuperuserTOTPGateMiddleware` (core_erp/middleware.py) +
`totp_activar_view`/`totp_verificar_view` (core_erp/views_totp.py).

Ejecutar: python manage.py test core_erp.test_totp --verbosity=2
"""
import django_otp.oath as oath
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

User = get_user_model()


def _codigo_valido(dispositivo):
    token = oath.totp(dispositivo.bin_key, step=dispositivo.step, t0=dispositivo.t0, digits=dispositivo.digits)
    return str(token).zfill(dispositivo.digits)


class SuperuserTOTPGateMiddlewareTest(TestCase):
    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'dir_2fa', 'dir_2fa@quintakooxtanil.com', 'clave-de-prueba',
        )

    def test_superusuario_sin_dispositivo_es_mandado_a_activar(self):
        self.client.force_login(self.superusuario)
        respuesta = self.client.get('/admin/')
        self.assertRedirects(respuesta, reverse('totp_activar'))

    def test_superusuario_con_dispositivo_confirmado_es_mandado_a_verificar(self):
        TOTPDevice.objects.create(user=self.superusuario, name='default', confirmed=True)
        self.client.force_login(self.superusuario)
        respuesta = self.client.get('/admin/')
        self.assertRedirects(respuesta, reverse('totp_verificar'))

    def test_el_gate_se_aplica_a_cualquier_ruta_de_admin_no_solo_al_index(self):
        self.client.force_login(self.superusuario)
        respuesta = self.client.get('/admin/comercial/pago/')
        self.assertRedirects(respuesta, reverse('totp_activar'))

    def test_superusuario_ya_verificado_no_es_redirigido(self):
        dispositivo = TOTPDevice.objects.create(user=self.superusuario, name='default', confirmed=True)
        self.client.force_login(self.superusuario)
        self.client.post(reverse('totp_verificar'), {'codigo': _codigo_valido(dispositivo)})

        respuesta = self.client.get('/admin/')
        self.assertEqual(respuesta.status_code, 200)

    def test_staff_sin_superuser_no_es_afectado_por_el_gate(self):
        staff = User.objects.create_user(
            'ventas_2fa', 'ventas_2fa@quintakooxtanil.com', 'clave-de-prueba', is_staff=True,
        )
        self.client.force_login(staff)
        respuesta = self.client.get('/admin/')
        self.assertNotIn(respuesta.status_code, (301, 302))

    def test_superusuario_sin_verificar_puede_llegar_al_logout(self):
        self.client.force_login(self.superusuario)
        respuesta = self.client.get('/admin/logout/')
        self.assertRedirects(respuesta, '/admin/login/')


class TotpActivarViewTest(TestCase):
    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'dir_activar', 'dir_activar@quintakooxtanil.com', 'clave-de-prueba',
        )
        self.client.force_login(self.superusuario)

    def test_get_crea_un_dispositivo_sin_confirmar_y_muestra_el_qr(self):
        respuesta = self.client.get(reverse('totp_activar'))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'data:image/png;base64,')
        dispositivo = TOTPDevice.objects.get(user=self.superusuario)
        self.assertFalse(dispositivo.confirmed)

    def test_post_con_codigo_correcto_confirma_y_deja_pasar(self):
        self.client.get(reverse('totp_activar'))
        dispositivo = TOTPDevice.objects.get(user=self.superusuario)

        respuesta = self.client.post(reverse('totp_activar'), {'codigo': _codigo_valido(dispositivo)})

        self.assertRedirects(respuesta, reverse('admin_dashboard'))
        dispositivo.refresh_from_db()
        self.assertTrue(dispositivo.confirmed)

        siguiente = self.client.get('/admin/')
        self.assertEqual(siguiente.status_code, 200)

    def test_post_con_codigo_incorrecto_no_confirma_nada(self):
        self.client.get(reverse('totp_activar'))
        dispositivo = TOTPDevice.objects.get(user=self.superusuario)

        respuesta = self.client.post(reverse('totp_activar'), {'codigo': '000000'})

        self.assertEqual(respuesta.status_code, 200)
        dispositivo.refresh_from_db()
        self.assertFalse(dispositivo.confirmed)

    def test_con_dispositivo_ya_confirmado_redirige_a_verificar_en_vez_de_reactivar(self):
        TOTPDevice.objects.create(user=self.superusuario, name='default', confirmed=True)
        respuesta = self.client.get(reverse('totp_activar'))
        self.assertRedirects(respuesta, reverse('totp_verificar'))

    def test_staff_sin_superuser_no_puede_activar_totp_ajeno(self):
        self.client.logout()
        staff = User.objects.create_user(
            'ventas_activar', 'ventas_activar@quintakooxtanil.com', 'clave-de-prueba', is_staff=True,
        )
        self.client.force_login(staff)
        respuesta = self.client.get(reverse('totp_activar'))
        self.assertNotEqual(respuesta.status_code, 200)


class TotpVerificarViewTest(TestCase):
    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'dir_verificar', 'dir_verificar@quintakooxtanil.com', 'clave-de-prueba',
        )
        self.dispositivo = TOTPDevice.objects.create(user=self.superusuario, name='default', confirmed=True)
        self.client.force_login(self.superusuario)

    def test_get_sin_dispositivo_confirmado_redirige_a_activar(self):
        self.dispositivo.delete()
        respuesta = self.client.get(reverse('totp_verificar'))
        self.assertRedirects(respuesta, reverse('totp_activar'))

    def test_post_con_codigo_correcto_deja_pasar(self):
        respuesta = self.client.post(reverse('totp_verificar'), {'codigo': _codigo_valido(self.dispositivo)})
        self.assertRedirects(respuesta, reverse('admin_dashboard'))

        siguiente = self.client.get('/admin/')
        self.assertEqual(siguiente.status_code, 200)

    def test_post_con_codigo_incorrecto_no_deja_pasar(self):
        self.client.post(reverse('totp_verificar'), {'codigo': '000000'})

        siguiente = self.client.get('/admin/')
        self.assertRedirects(siguiente, reverse('totp_verificar'))
