"""Pruebas del cache compartido y del rate limiting del admin."""

import time
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from core_erp.ratelimit import limpiar_intentos_login, rate_limit, registrar_login_fallido


class CacheCompartidoTest(TestCase):
    def tearDown(self):
        cache.clear()

    def test_backend_no_es_memoria_local(self):
        backend = settings.CACHES['default']['BACKEND']
        self.assertEqual(backend, 'django.core.cache.backends.db.DatabaseCache')
        self.assertNotIn('LocMemCache', backend)

    def test_valor_del_cache_se_guarda_en_base_de_datos(self):
        clave = 'test-cache-compartido'
        cache.set(clave, 'visible-entre-workers', timeout=60)

        tabla = connection.ops.quote_name(settings.CACHES['default']['LOCATION'])
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT cache_key FROM {tabla} WHERE cache_key = %s',  # noqa: S608 -- tabla viene de settings.CACHES, no de entrada externa; valor va parametrizado
                [cache.make_key(clave)],
            )
            fila = cursor.fetchone()

        self.assertIsNotNone(fila)

    def test_rate_limit_respeta_limite_y_cambia_de_ventana(self):
        request = RequestFactory().get('/prueba-rate-limit/')
        inicio_ventana = int(time.time() // 60) * 60

        @rate_limit(key='prueba_ventana', limit=2, window=60)
        def vista(_request):
            return HttpResponse('ok')

        with patch('core_erp.ratelimit.time.time', return_value=inicio_ventana):
            self.assertEqual(vista(request).status_code, 200)
            self.assertEqual(vista(request).status_code, 200)
            self.assertEqual(vista(request).status_code, 429)

        with patch('core_erp.ratelimit.time.time', return_value=inicio_ventana + 60):
            self.assertEqual(vista(request).status_code, 200)

    def test_contar_no_extiende_el_ttl_al_incrementar(self):
        # Antes del fix, el incremento usaba cache.incr(), cuyo set() interno
        # sin timeout caía al TIMEOUT global (3600s) en vez de respetar
        # window*2 — el mismo defecto que dejaba el candado anti-doble-cobro
        # de Openpay como primer candidato al cull.
        from core_erp.ratelimit import _contar

        bucket = 'rl:test-ttl:0'
        _contar(bucket, window=1)
        _contar(bucket, window=1)

        time.sleep(2.5)

        self.assertIsNone(cache.get(bucket))


@override_settings(
    ADMIN_LOGIN_VENTANA=900,
    ADMIN_LOGIN_MAX_INTENTOS_IP=3,
    ADMIN_LOGIN_MAX_INTENTOS_USUARIO=4,
    RATELIMIT_TRUSTED_PROXY_COUNT=1,
)
class AdminLoginRateLimitTest(TestCase):
    username = 'admin_issue_179'
    password = 'Segura-179!'

    def setUp(self):
        cache.clear()
        self.usuario = get_user_model().objects.create_superuser(
            username=self.username,
            email='admin179@example.test',
            password=self.password,
        )
        self.url = reverse('admin:login')

    def tearDown(self):
        cache.clear()

    def _post(self, password='incorrecta', ip='192.0.2.10', username=None, **extra):
        return self.client.post(
            self.url,
            {'username': username or self.username, 'password': password},
            REMOTE_ADDR=ip,
            **extra,
        )

    def test_bloquea_por_ip_incluso_con_credenciales_correctas(self):
        for _ in range(settings.ADMIN_LOGIN_MAX_INTENTOS_IP):
            self.assertEqual(self._post().status_code, 200)

        response = self._post(password=self.password)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], str(settings.ADMIN_LOGIN_VENTANA))

        response_get = self.client.get(self.url, REMOTE_ADDR='192.0.2.10')
        self.assertEqual(response_get.status_code, 429)

    def test_bloquea_usuario_con_fallos_desde_ips_distintas(self):
        for numero in range(settings.ADMIN_LOGIN_MAX_INTENTOS_USUARIO):
            response = self._post(ip=f'192.0.2.{numero + 20}')
            self.assertEqual(response.status_code, 200)

        response = self._post(password=self.password, ip='192.0.2.99')
        self.assertEqual(response.status_code, 429)

    def test_login_correcto_limpia_solo_el_contador_de_usuario(self):
        # Cada fallo desde una IP distinta para no tocar el límite por IP;
        # lo que se prueba es que el propio usuario recupera su cupo tras
        # autenticar correctamente.
        for numero in range(3):
            self.assertEqual(self._post(ip=f'192.0.2.{60 + numero}').status_code, 200)

        self.assertEqual(
            self._post(password=self.password, ip='192.0.2.63').status_code, 302
        )
        self.client.get(reverse('logout'))

        # Si el login correcto no hubiera limpiado el contador del usuario,
        # este segundo bloque ya arrastraría los 3 fallos previos y el
        # intento final quedaría bloqueado en vez de autenticar.
        for numero in range(3):
            self.assertEqual(self._post(ip=f'192.0.2.{70 + numero}').status_code, 200)
        self.assertEqual(
            self._post(password=self.password, ip='192.0.2.73').status_code, 302
        )

    def test_login_correcto_no_limpia_el_contador_de_ip(self):
        ip = '192.0.2.80'
        # Password spraying: 2 fallos contra usuarios distintos desde la
        # misma IP (el límite por IP es 3).
        self.assertEqual(self._post(ip=ip, username='otro_admin').status_code, 200)
        self.assertEqual(self._post(ip=ip).status_code, 200)

        # Login correcto de la cuenta real, misma IP.
        self.assertEqual(self._post(password=self.password, ip=ip).status_code, 302)
        self.client.get(reverse('logout'))

        # El contador de IP debe seguir en 2, no en 0: un tercer fallo lo deja
        # en el límite y el siguiente intento —aunque traiga la contraseña
        # correcta— debe bloquearse. Si el login correcto hubiera limpiado
        # bucket_ip, este último intento pasaría con 302.
        self.assertEqual(self._post(ip=ip).status_code, 200)
        response = self._post(password=self.password, ip=ip)
        self.assertEqual(response.status_code, 429)

    def test_ip_distinta_no_hereda_el_bloqueo(self):
        for _ in range(settings.ADMIN_LOGIN_MAX_INTENTOS_IP):
            self.assertEqual(self._post(ip='192.0.2.30').status_code, 200)

        response = self._post(password=self.password, ip='192.0.2.31')
        self.assertEqual(response.status_code, 302)

    def test_login_normal_conserva_formulario_y_sesion(self):
        response_get = self.client.get(self.url, REMOTE_ADDR='192.0.2.40')
        self.assertEqual(response_get.status_code, 200)
        self.assertContains(response_get, 'name="username"')

        response_post = self._post(password=self.password, ip='192.0.2.40')
        self.assertEqual(response_post.status_code, 302)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.usuario.pk)

    def test_x_forwarded_for_no_permite_rotar_la_ip(self):
        ip_proxy = '198.51.100.70'
        for numero in range(settings.ADMIN_LOGIN_MAX_INTENTOS_IP):
            response = self._post(
                ip='10.0.0.1',
                HTTP_X_FORWARDED_FOR=f'203.0.113.{numero}, {ip_proxy}',
            )
            self.assertEqual(response.status_code, 200)

        response = self._post(
            password=self.password,
            ip='10.0.0.1',
            HTTP_X_FORWARDED_FOR=f'203.0.113.99, {ip_proxy}',
        )
        self.assertEqual(response.status_code, 429)

    def test_senales_sin_request_no_lanzan(self):
        request = RequestFactory().post('/admin/login/', REMOTE_ADDR='192.0.2.50')
        registrar_login_fallido(None, None)
        registrar_login_fallido(request, None)
        limpiar_intentos_login(request, None)
        limpiar_intentos_login(None, None)

        self.assertFalse(self.client.login(username='inexistente', password='incorrecta'))
        self.client.force_login(self.usuario)
        self.assertEqual(int(self.client.session['_auth_user_id']), self.usuario.pk)

    def test_log_no_incluye_password_ni_credenciales_completas(self):
        with self.assertLogs('core_erp.ratelimit', level='WARNING') as logs:
            self._post(password='secreto-que-no-debe-aparecer')

        salida = '\n'.join(logs.output)
        self.assertIn(self.username, salida)
        self.assertIn('192.0.2.10', salida)
        self.assertNotIn('secreto-que-no-debe-aparecer', salida)
        self.assertNotIn("'password'", salida)

    def test_buckets_de_usuario_no_guardan_username_en_claro(self):
        self._post()
        tabla = connection.ops.quote_name(settings.CACHES['default']['LOCATION'])
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT cache_key FROM {tabla}')  # noqa: S608 -- tabla viene de settings.CACHES, no de entrada externa
            claves = [fila[0] for fila in cursor.fetchall()]
        self.assertFalse(any(self.username in clave_cache for clave_cache in claves))
