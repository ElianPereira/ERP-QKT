"""
Tests de seguridad del portal del cliente — Issue #190, backlog:
SEC-AUTHN-001b (orden 10), SEC-AUTHN-001c (orden 11), SEC-SESS-001 (orden 27).

Ejecutar: python manage.py test comercial.test_seguridad_portal --verbosity=2
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, PortalCliente
from core_erp.test_utils import login_superuser_con_totp


def _crear_cotizacion(telefono='5551234567', dias_evento=90, nombre='Cliente Portal'):
    cliente = Cliente.objects.create(nombre=nombre, telefono=telefono)
    cotizacion = Cotizacion.objects.create(
        cliente=cliente,
        nombre_evento='Evento de prueba',
        fecha_evento=date.today() + timedelta(days=dias_evento),
    )
    # Cotizacion.save() ya crea el PortalCliente automáticamente (ver save()
    # en models.py). Un item real evita que precio_final quede en 0.
    ItemCotizacion.objects.create(
        cotizacion=cotizacion, descripcion='Servicio', cantidad=1,
        precio_unitario=1000,
    )
    return cotizacion


class PortalRateLimitPorCotizacionTest(TestCase):
    """SEC-AUTHN-001b: bloqueo por cotización, no elude cambiando de IP."""

    def setUp(self):
        cache.clear()
        self.cotizacion = _crear_cotizacion(telefono='5551234567')
        self.url = reverse('portal_acceso')

    def _intento_fallido(self, ip):
        return self.client.post(
            self.url,
            {'codigo': str(self.cotizacion.id), 'telefono': '0000'},
            REMOTE_ADDR=ip,
        )

    def test_diez_intentos_fallidos_desde_ips_distintas_bloquean_la_cotizacion(self):
        for i in range(10):
            respuesta = self._intento_fallido(f'10.0.0.{i}')
            self.assertEqual(respuesta.status_code, 200)

        # El intento 11, desde una IP nueva otra vez, ya no llega a comparar
        # el teléfono: la cotización quedó bloqueada, sin importar la IP.
        respuesta = self._intento_fallido('10.0.0.99')
        self.assertEqual(respuesta.status_code, 429)

    def test_el_bloqueo_devuelve_retry_after(self):
        for i in range(10):
            self._intento_fallido(f'10.0.1.{i}')

        respuesta = self._intento_fallido('10.0.1.99')
        self.assertEqual(respuesta.status_code, 429)
        self.assertIn('Retry-After', respuesta.headers)

    def test_el_bloqueo_es_por_cotizacion_no_global(self):
        for i in range(10):
            self._intento_fallido(f'10.0.2.{i}')
        self.assertEqual(self._intento_fallido('10.0.2.99').status_code, 429)

        # Otra cotización, mismas IPs ya usadas: no debe verse afectada.
        otra = _crear_cotizacion(telefono='5559999999', nombre='Otro cliente')
        respuesta = self.client.post(
            self.url,
            {'codigo': str(otra.id), 'telefono': '9999'},
            REMOTE_ADDR='10.0.2.0',
        )
        self.assertEqual(respuesta.status_code, 302)

    def test_un_acceso_correcto_limpia_el_contador(self):
        for i in range(5):
            self._intento_fallido(f'10.0.3.{i}')

        correcto = self.client.post(
            self.url,
            {'codigo': str(self.cotizacion.id), 'telefono': '4567'},
            REMOTE_ADDR='10.0.3.50',
        )
        self.assertEqual(correcto.status_code, 302)

        # Tras el acceso correcto, 5 fallos más no deberían alcanzar por sí
        # solos el límite de 10 (el contador se reinició en 0).
        for i in range(5):
            respuesta = self._intento_fallido(f'10.0.3.{60 + i}')
        self.assertEqual(respuesta.status_code, 200)


class PortalAccesoSinCreacionImplicitaTest(TestCase):
    """SEC-AUTHN-001c: portal_acceso solo resuelve portales existentes."""

    def setUp(self):
        cache.clear()
        self.cotizacion = _crear_cotizacion(telefono='5551234567')
        self.url = reverse('portal_acceso')

    def test_no_recrea_el_portal_borrado_aunque_las_credenciales_sean_correctas(self):
        PortalCliente.objects.filter(cotizacion=self.cotizacion).delete()
        self.assertFalse(PortalCliente.objects.filter(cotizacion=self.cotizacion).exists())

        respuesta = self.client.post(
            self.url, {'codigo': str(self.cotizacion.id), 'telefono': '4567'}
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('no coinciden', respuesta.context['error'])
        self.assertFalse(PortalCliente.objects.filter(cotizacion=self.cotizacion).exists())

    def test_acceso_correcto_con_portal_vigente_redirige(self):
        portal = PortalCliente.objects.get(cotizacion=self.cotizacion)

        respuesta = self.client.post(
            self.url, {'codigo': str(self.cotizacion.id), 'telefono': '4567'}
        )

        self.assertRedirects(
            respuesta, reverse('portal_evento', args=[portal.token]),
            fetch_redirect_response=False,
        )

    def test_cotizacion_inexistente_no_revela_que_no_existe(self):
        respuesta = self.client.post(
            self.url, {'codigo': '999999', 'telefono': '0000'}
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('no coinciden', respuesta.context['error'])


class PortalExpiradoUnifica404Test(TestCase):
    """SEC-SESS-001: inactivo, expirado o inexistente responden igual, 404."""

    def setUp(self):
        self.cotizacion = _crear_cotizacion()
        self.portal = PortalCliente.objects.get(cotizacion=self.cotizacion)

    def _urls_del_portal(self, token):
        return [
            reverse('portal_evento', args=[token]),
            reverse('portal_descargar_cotizacion', args=[token]),
            reverse('portal_descargar_plan', args=[token]),
            reverse('portal_descargar_contrato', args=[token]),
            reverse('portal_descargar_guia', args=[token]),
            reverse('portal_ficha_paynet', args=[token, 'tx-inexistente']),
            reverse('portal_retorno_3ds', args=[token]),
        ]

    def test_portal_expirado_da_404_en_todas_las_rutas(self):
        self.portal.expira_en = timezone.now() - timedelta(days=1)
        self.portal.save(update_fields=['expira_en'])

        for url in self._urls_del_portal(self.portal.token):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_portal_inactivo_da_404_en_todas_las_rutas(self):
        self.portal.activo = False
        self.portal.save(update_fields=['activo'])

        for url in self._urls_del_portal(self.portal.token):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_token_inexistente_da_404(self):
        for url in self._urls_del_portal('token-que-nunca-existio'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_portal_pago_openpay_expirado_da_404(self):
        self.portal.expira_en = timezone.now() - timedelta(days=1)
        self.portal.save(update_fields=['expira_en'])

        respuesta = self.client.post(
            reverse('portal_procesar_pago_openpay', args=[self.portal.token]),
            {'metodo': 'store', 'monto': '100.00'},
        )
        self.assertEqual(respuesta.status_code, 404)

    def test_portal_vigente_sigue_abriendo(self):
        respuesta = self.client.get(reverse('portal_evento', args=[self.portal.token]))
        self.assertEqual(respuesta.status_code, 200)


class PortalClienteVigenciaModelTest(TestCase):
    """Cálculo de expira_en y la propiedad vigente."""

    def test_expira_90_dias_despues_del_evento(self):
        cotizacion = _crear_cotizacion(dias_evento=30)
        portal = PortalCliente.objects.get(cotizacion=cotizacion)

        esperado = timezone.make_aware(
            timezone.datetime.combine(cotizacion.fecha_evento, timezone.datetime.min.time())
        ) + timedelta(days=90)
        self.assertEqual(portal.expira_en, esperado)

    def test_fecha_evento_como_cadena_no_revienta(self):
        cliente = Cliente.objects.create(nombre='Cliente cadena', telefono='5550000000')
        cotizacion = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Evento', fecha_evento='2027-03-10',
        )
        portal = PortalCliente.objects.get(cotizacion=cotizacion)
        self.assertEqual(portal.expira_en.date(), date(2027, 6, 8))

    def test_vigente_requiere_activo_y_no_expirado(self):
        cotizacion = _crear_cotizacion()
        portal = PortalCliente.objects.get(cotizacion=cotizacion)
        self.assertTrue(portal.vigente)

        portal.activo = False
        self.assertFalse(portal.vigente)

        portal.activo = True
        portal.expira_en = timezone.now() - timedelta(seconds=1)
        self.assertFalse(portal.vigente)

    def test_regenerar_no_toca_expira_en_de_portales_ya_creados(self):
        # Guardar un portal existente (p. ej. registrar_visita) no debe
        # recalcular su expiración: solo se calcula al crear.
        cotizacion = _crear_cotizacion()
        portal = PortalCliente.objects.get(cotizacion=cotizacion)
        original = portal.expira_en

        portal.registrar_visita()
        portal.refresh_from_db()

        self.assertEqual(portal.expira_en, original)


class PortalClienteAdminRegenerarTokenTest(TestCase):
    """La acción del admin es la vía para renovar acceso sin tocar la BD."""

    def setUp(self):
        self.superusuario = User.objects.create_superuser(
            'jefa', 'jefa@quintakooxtanil.com', 'clave-de-prueba'
        )
        self.client = Client()
        login_superuser_con_totp(self.client, self.superusuario)
        self.cotizacion = _crear_cotizacion()
        self.portal = PortalCliente.objects.get(cotizacion=self.cotizacion)

    def test_sin_confirmar_no_regenera_el_token(self):
        """SEC-BIZ-002: un solo POST sin 'confirmar=si' no debe regenerar el
        token — invalidarlo sin querer deja sin acceso a quien lo tenga."""
        token_viejo = self.portal.token

        respuesta = self.client.post(
            reverse('admin:comercial_portalcliente_changelist'),
            {
                'action': 'regenerar_token',
                '_selected_action': [str(self.portal.pk)],
            },
            follow=True,
        )

        self.portal.refresh_from_db()
        self.assertEqual(self.portal.token, token_viejo)
        self.assertContains(respuesta, '¿Confirmar esta acción?')

    def test_regenerar_cambia_token_reactiva_y_extiende_90_dias(self):
        token_viejo = self.portal.token
        self.portal.activo = False
        self.portal.expira_en = timezone.now() - timedelta(days=1)
        self.portal.save(update_fields=['activo', 'expira_en'])

        respuesta = self.client.post(
            reverse('admin:comercial_portalcliente_changelist'),
            {
                'action': 'regenerar_token',
                '_selected_action': [str(self.portal.pk)],
                'confirmar': 'si',
            },
            follow=True,
        )
        self.assertEqual(respuesta.status_code, 200)

        self.portal.refresh_from_db()
        self.assertNotEqual(self.portal.token, token_viejo)
        self.assertTrue(self.portal.activo)
        self.assertGreater(self.portal.expira_en, timezone.now() + timedelta(days=89))

    def test_el_token_viejo_deja_de_servir_y_el_nuevo_funciona(self):
        token_viejo = self.portal.token

        self.client.post(
            reverse('admin:comercial_portalcliente_changelist'),
            {
                'action': 'regenerar_token',
                '_selected_action': [str(self.portal.pk)],
                'confirmar': 'si',
            },
        )
        self.portal.refresh_from_db()

        self.assertEqual(
            self.client.get(reverse('portal_evento', args=[token_viejo])).status_code, 404
        )
        self.assertEqual(
            self.client.get(reverse('portal_evento', args=[self.portal.token])).status_code, 200
        )
