"""
Bloqueo de fechas sin contratación (mantenimiento, reparaciones, uso propio).

Un `BloqueoFecha` activo debe cerrar la fecha en todo lo que pregunta por
disponibilidad (cotizador, portal, confirmación, agente de WhatsApp) sin
revelar al cliente el motivo, y crearse desde el calendario del admin.

Ejecutar: python manage.py test comercial.test_bloqueo_fecha
"""
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.disponibilidad import obtener_fechas_bloqueadas, verificar_disponibilidad_rango
from comercial.models import BloqueoFecha, Cliente, Cotizacion
from comunicacion import herramientas_agente
from core_erp.test_utils import login_superuser_con_totp

NOTA_SECRETA = 'Fuga en la cisterna'


class BaseBloqueo(TestCase):
    def setUp(self):
        cache.clear()
        self.cliente = Cliente.objects.create(nombre='C', tipo_persona='FISICA', telefono='9990000001')
        self.d = timezone.localdate() + timedelta(days=30)

    def _bloqueo(self, inicio, fin=None, **kw):
        return BloqueoFecha.objects.create(
            fecha_inicio=inicio, fecha_fin=fin or inicio, motivo='REPARACION', notas=NOTA_SECRETA, **kw,
        )

    def _cot(self, fecha, estado='CONFIRMADA', tipo='EVENTO', salida=None):
        return Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='X', tipo_servicio=tipo,
            fecha_evento=fecha, fecha_salida=salida, estado=estado,
        )


class DisponibilidadConBloqueoTest(BaseBloqueo):
    def test_bloqueo_activo_cierra_la_fecha_sin_revelar_el_motivo(self):
        self._bloqueo(self.d)
        libre, msg = verificar_disponibilidad_rango(self.d, self.d + timedelta(days=1))
        self.assertFalse(libre)
        self.assertNotIn(NOTA_SECRETA, msg)
        self.assertNotIn('Reparación', msg)

    def test_bloqueo_inactivo_no_cierra_nada(self):
        self._bloqueo(self.d, activo=False)
        self.assertTrue(verificar_disponibilidad_rango(self.d, self.d + timedelta(days=1))[0])

    def test_el_ultimo_dia_del_bloqueo_esta_incluido(self):
        self._bloqueo(self.d, self.d + timedelta(days=2))
        ultimo = self.d + timedelta(days=2)
        self.assertFalse(verificar_disponibilidad_rango(ultimo, ultimo + timedelta(days=1))[0])
        siguiente = ultimo + timedelta(days=1)
        self.assertTrue(verificar_disponibilidad_rango(siguiente, siguiente + timedelta(days=1))[0])

    def test_hospedaje_que_cruza_el_bloqueo_no_esta_disponible(self):
        self._bloqueo(self.d + timedelta(days=1))
        self.assertFalse(verificar_disponibilidad_rango(self.d, self.d + timedelta(days=3))[0])

    def test_hospedaje_con_salida_el_primer_dia_bloqueado_si_cabe(self):
        # Checkout a las 10:00 del primer día cerrado: la noche anterior no
        # está bloqueada (mismo criterio que entre dos reservaciones).
        self._bloqueo(self.d)
        self.assertTrue(verificar_disponibilidad_rango(self.d - timedelta(days=2), self.d)[0])

    def test_fechas_bloqueadas_incluye_los_bloqueos(self):
        self._bloqueo(self.d, self.d + timedelta(days=1))
        bloqueos = obtener_fechas_bloqueadas(self.d - timedelta(days=5), self.d + timedelta(days=5))
        self.assertEqual(bloqueos, [{
            'fecha_inicio': self.d, 'fecha_fin': self.d + timedelta(days=2), 'titulo': 'No disponible',
        }])


class CotizadorPublicoConBloqueoTest(BaseBloqueo):
    def test_api_fechas_ocupadas_marca_cada_dia_bloqueado(self):
        self._bloqueo(self.d, self.d + timedelta(days=2))
        cuerpo = self.client.get('/api/fechas-ocupadas/').json()
        esperadas = [(self.d + timedelta(days=i)).isoformat() for i in range(3)]
        self.assertEqual(cuerpo['fechas_ocupadas'], esperadas)

    def test_api_disponibilidad_no_revela_el_motivo(self):
        self._bloqueo(self.d)
        respuesta = self.client.get('/api/disponibilidad/', {'fecha': self.d.isoformat()})
        cuerpo = respuesta.json()
        self.assertFalse(cuerpo['disponible'])
        self.assertNotIn(NOTA_SECRETA, respuesta.content.decode())

    def test_agente_de_whatsapp_la_ve_ocupada(self):
        self._bloqueo(self.d)
        r = herramientas_agente.consultar_disponibilidad(fecha=self.d.isoformat())
        self.assertFalse(r['disponible'])


class CotizacionConBloqueoTest(BaseBloqueo):
    def test_cotizacion_abierta_en_fecha_bloqueada_no_admite_pago(self):
        cot = self._cot(self.d, estado='COTIZADA')
        self._bloqueo(self.d)
        self.assertFalse(cot.admite_pago_detalle()[0])

    def test_no_se_puede_confirmar_en_fecha_bloqueada(self):
        cot = self._cot(self.d, estado='COTIZADA')
        self._bloqueo(self.d)
        ok, _ = cot.cambiar_estado('CONFIRMADA')
        self.assertFalse(ok)
        cot.refresh_from_db()
        self.assertEqual(cot.estado, 'COTIZADA')


class ValidacionBloqueoTest(BaseBloqueo):
    def test_no_bloquea_sobre_una_reservacion_confirmada(self):
        cot = self._cot(self.d)
        bloqueo = BloqueoFecha(fecha_inicio=self.d - timedelta(days=1), fecha_fin=self.d + timedelta(days=1))
        with self.assertRaises(ValidationError) as ctx:
            bloqueo.full_clean()
        self.assertIn(f'COT-{cot.id:03d}', str(ctx.exception))

    def test_reactivar_tambien_valida(self):
        bloqueo = self._bloqueo(self.d, activo=False)
        self._cot(self.d)
        bloqueo.activo = True
        with self.assertRaises(ValidationError):
            bloqueo.full_clean()

    def test_fin_anterior_al_inicio(self):
        with self.assertRaises(ValidationError):
            BloqueoFecha(fecha_inicio=self.d, fecha_fin=self.d - timedelta(days=1)).full_clean()

    def test_cotizacion_abierta_no_impide_bloquear(self):
        self._cot(self.d, estado='COTIZADA')
        BloqueoFecha(fecha_inicio=self.d, fecha_fin=self.d).full_clean()


class AdminYCalendarioTest(BaseBloqueo):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)
        self.add_url = reverse('admin:comercial_bloqueofecha_add')

    def test_alta_desde_el_calendario_prellena_fechas_y_regresa(self):
        qs = f'?desde=calendario&fecha_inicio={self.d.isoformat()}&fecha_fin={self.d.isoformat()}'
        respuesta = self.client.get(self.add_url + qs)
        self.assertContains(respuesta, self.d.isoformat())

        respuesta = self.client.post(self.add_url + '?desde=calendario', {
            'fecha_inicio': self.d.isoformat(), 'fecha_fin': self.d.isoformat(),
            'motivo': 'MANTENIMIENTO', 'notas': '', 'activo': 'on',
        })
        self.assertRedirects(
            respuesta, f"{reverse('calendario_unificado')}?fecha={self.d.isoformat()}",
            fetch_redirect_response=False,
        )
        bloqueo = BloqueoFecha.objects.get()
        self.assertEqual(bloqueo.created_by, self.admin)
        self.assertEqual(bloqueo.updated_by, self.admin)

    def test_avisa_de_cotizaciones_abiertas_en_esas_fechas(self):
        cot = self._cot(self.d, estado='BORRADOR')
        respuesta = self.client.post(self.add_url, {
            'fecha_inicio': self.d.isoformat(), 'fecha_fin': self.d.isoformat(),
            'motivo': 'MANTENIMIENTO', 'activo': 'on',
        }, follow=True)
        self.assertContains(respuesta, f'COT-{cot.id:03d}')

    def test_no_se_borra_se_libera(self):
        bloqueo = self._bloqueo(self.d)
        request = RequestFactory().get('/')
        request.user = self.admin
        self.assertFalse(admin.site._registry[BloqueoFecha].has_delete_permission(request, bloqueo))
        self.client.post(reverse('admin:comercial_bloqueofecha_changelist'), {
            'action': 'liberar_fechas', '_selected_action': [bloqueo.pk],
        })
        bloqueo.refresh_from_db()
        self.assertFalse(bloqueo.activo)
        self.assertTrue(verificar_disponibilidad_rango(self.d, self.d + timedelta(days=1))[0])

    def test_eventos_del_calendario_incluyen_el_bloqueo(self):
        bloqueo = self._bloqueo(self.d, self.d + timedelta(days=1))
        self._bloqueo(self.d + timedelta(days=3), activo=False)
        eventos = self.client.get(reverse('calendario_unificado_eventos'), {
            'start': (self.d - timedelta(days=5)).isoformat(),
            'end': (self.d + timedelta(days=5)).isoformat(),
        }).json()
        bloqueos = [e for e in eventos if e['extendedProps']['tipo'] == 'bloqueo']
        self.assertEqual(len(bloqueos), 1)
        self.assertEqual(bloqueos[0]['start'], self.d.isoformat())
        self.assertEqual(bloqueos[0]['end'], (self.d + timedelta(days=2)).isoformat())  # exclusivo
        self.assertIn(f'/bloqueofecha/{bloqueo.id}/', bloqueos[0]['url'])

    def test_pagina_del_calendario_ofrece_bloquear_y_acepta_fecha_inicial(self):
        respuesta = self.client.get(reverse('calendario_unificado'), {'fecha': '2026-02-31'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'Bloquear fechas')

    def test_sin_permiso_de_alta_no_se_ofrece_bloquear(self):
        vendedor = get_user_model().objects.create_user('v', password='x-segura-123', is_staff=True)
        vendedor.user_permissions.add(Permission.objects.get(codename='view_cotizacion'))
        self.client.force_login(vendedor)
        respuesta = self.client.get(reverse('calendario_unificado'))
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'Bloquear fechas')

