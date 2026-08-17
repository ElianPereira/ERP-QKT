"""
Tests de la composición de líneas del cotizador público
(`comercial/views_cotizador.py::_lineas_cotizador`).

El bug que originó este archivo: una solicitud de PASADÍA creaba la cotización
**en cero**. El producto real se llama 'Paquete Pasadía QKT' y la búsqueda era
`nombre__icontains='Pasadia'` — un LIKE, insensible a mayúsculas pero SENSIBLE a
acentos —, así que nunca encontraba nada y `_agregar_item()` se salía en
silencio con su `if not producto: return None`.

Ejecutar: python manage.py test comercial.test_cotizador_lineas --verbosity=2
"""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cotizacion, Producto
from comercial.roles_cotizador import sembrar_roles
from comercial.views_cotizador import _lineas_cotizador
from comunicacion.tests.utils import RespuestaFalsa, limpiar_cache_emisor, wa_settings


def _payload(**extra):
    return {
        'nombre': 'Ana Ruiz',
        'telefono': '5215555550001',
        'email': 'ana@example.com',
        'servicio': 'EVENTO',
        'fecha': (timezone.localdate() + timedelta(days=60)).strftime('%Y-%m-%d'),
        'personas': '80',
        'acepta_legales': True,
        **extra,
    }


def _crear_catalogo():
    """Réplica del catálogo real: los nombres llevan acento y NO son paquetes."""
    esencial = Producto.objects.create(
        nombre='Paquete Esencial QKT', precio_venta_fijo=Decimal('4000.00'),
        visible_cotizador=True, cotizador_evento=True, rol_cotizador='BASE_EVENTO',
    )
    pasadia = Producto.objects.create(
        nombre='Paquete Pasadía QKT', precio_venta_fijo=Decimal('1293.10'),
        visible_cotizador=True, cotizador_pasadia=True, rol_cotizador='BASE_PASADIA',
    )
    hora_extra = Producto.objects.create(
        nombre='Hora Extra De Arrendamiento', precio_venta_fijo=Decimal('800.00'),
        visible_cotizador=True, cotizador_evento=True, rol_cotizador='HORA_EXTRA',
    )
    extra = Producto.objects.create(
        nombre='Habitación Ka´an Para Anfitriones Pernocta',
        precio_venta_fijo=Decimal('600.00'),
        visible_cotizador=True, cotizador_evento=True, cotizador_pasadia=True,
    )
    return esencial, pasadia, hora_extra, extra


class LineaBasePorServicioTest(TestCase):
    """Elegir el servicio basta: la línea base se agrega sola."""

    def setUp(self):
        cache.clear()
        self.esencial, self.pasadia, self.hora_extra, self.extra = _crear_catalogo()

    def _productos(self, lineas):
        return [prod for prod, _, _ in lineas]

    def test_pasadia_sin_nada_seleccionado_agrega_su_producto(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=10, horas_evento=9,
        )
        self.assertEqual(self._productos(lineas), [self.pasadia])

    def test_evento_sin_paquete_agrega_el_paquete_esencial(self):
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[],
            num_personas=80, horas_evento=6,
        )
        self.assertEqual(self._productos(lineas), [self.esencial])

    def test_evento_con_paquete_elegido_no_duplica_la_base(self):
        # Un paquete prediseñado ya incluye el arrendamiento de la quinta.
        paquete = Producto.objects.create(
            nombre='Paquete Premium QKT', precio_venta_fijo=Decimal('60000.00'),
            visible_cotizador=True, cotizador_evento=True, es_paquete=True,
        )
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=paquete.id, extras_ids=[],
            num_personas=80, horas_evento=6,
        )
        self.assertEqual(self._productos(lineas), [paquete])

    def test_evento_de_mas_de_seis_horas_cobra_las_horas_extra(self):
        lineas = _lineas_cotizador(
            servicio='EVENTO', paquete_id=None, extras_ids=[],
            num_personas=80, horas_evento=8,
        )
        self.assertEqual(self._productos(lineas), [self.esencial, self.hora_extra])
        self.assertEqual(lineas[1][1], 2)  # 8 − 6 horas base

    def test_la_pasadia_nunca_cobra_horas_extra(self):
        # Son 9 horas fijas, no 6 + 3 adicionales.
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=10, horas_evento=9,
        )
        self.assertNotIn(self.hora_extra, self._productos(lineas))

    def test_los_extras_se_suman_a_la_base(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[self.extra.id],
            num_personas=10, horas_evento=9,
        )
        self.assertEqual(self._productos(lineas), [self.pasadia, self.extra])


class BaseNoSeCobraDosVecesTest(TestCase):
    """La línea base no puede llegar además como extra."""

    def setUp(self):
        cache.clear()
        self.esencial, self.pasadia, _, self.extra = _crear_catalogo()

    def test_la_api_de_extras_no_ofrece_los_productos_base(self):
        respuesta = self.client.get(
            reverse('api_productos_cotizador'), {'servicio': 'PASADIA'},
        )
        nombres = [
            p['nombre']
            for grupo in respuesta.json()['grupos'] for p in grupo['productos']
        ]
        self.assertNotIn(self.pasadia.nombre, nombres)
        self.assertIn(self.extra.nombre, nombres)

    def test_un_id_base_mandado_a_mano_como_extra_se_ignora(self):
        # Los ids llegan del cliente: nada impide mandarlos a mano.
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None,
            extras_ids=[self.pasadia.id, self.extra.id],
            num_personas=10, horas_evento=9,
        )
        productos = [prod for prod, _, _ in lineas]
        self.assertEqual(productos.count(self.pasadia), 1)
        self.assertIn(self.extra, productos)


class BusquedaSinAcentosTest(TestCase):
    """Red de seguridad: sin `rol_cotizador` marcado, se encuentra por nombre."""

    def setUp(self):
        cache.clear()

    def test_encuentra_el_producto_aunque_el_nombre_lleve_acento(self):
        pasadia = Producto.objects.create(
            nombre='Paquete Pasadía QKT', precio_venta_fijo=Decimal('1293.10'),
            visible_cotizador=True, cotizador_pasadia=True,  # sin rol_cotizador
        )
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=10, horas_evento=9,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [pasadia])

    def test_el_rol_manda_sobre_el_nombre(self):
        Producto.objects.create(
            nombre='Paquete Pasadía QKT (viejo)', precio_venta_fijo=Decimal('900.00'),
            visible_cotizador=True, cotizador_pasadia=True,
        )
        vigente = Producto.objects.create(
            nombre='Estancia de día', precio_venta_fijo=Decimal('1293.10'),
            visible_cotizador=True, cotizador_pasadia=True,
            rol_cotizador='BASE_PASADIA',
        )
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=10, horas_evento=9,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [vigente])

    def test_sin_producto_base_avisa_en_el_log(self):
        # Antes la cotización salía en cero sin dejar rastro de por qué.
        with self.assertLogs('comercial.views_cotizador', level='WARNING') as registro:
            lineas = _lineas_cotizador(
                servicio='PASADIA', paquete_id=None, extras_ids=[],
                num_personas=10, horas_evento=9,
            )
        self.assertEqual(lineas, [])
        self.assertIn('BASE_PASADIA', registro.output[0])


class SembradoDeRolesTest(TestCase):
    """La migración 0072 marca sola los productos del catálogo real."""

    def test_los_nombres_reales_del_catalogo_se_reconocen(self):
        esencial = Producto.objects.create(nombre='Paquete Esencial QKT')
        pasadia = Producto.objects.create(nombre='Paquete Pasadía QKT')
        hora = Producto.objects.create(nombre='Hora Extra De Arrendamiento')
        Producto.objects.create(nombre='Habitación Ka´an Para Anfitriones Pernocta')

        sembrar_roles(Producto)

        for producto, rol in ((esencial, 'BASE_EVENTO'),
                              (pasadia, 'BASE_PASADIA'),
                              (hora, 'HORA_EXTRA')):
            producto.refresh_from_db()
            self.assertEqual(producto.rol_cotizador, rol)

        # Un producto normal no se marca (ojo: la BD de test ya trae productos
        # sembrados por migraciones anteriores, por eso se filtra por nombre).
        self.assertEqual(
            Producto.objects.get(nombre__startswith='Habitación').rol_cotizador, '',
        )


class TotalExhibidoTest(TestCase):
    """El total que se le muestra al cliente incluye la línea base."""

    def setUp(self):
        cache.clear()
        self.esencial, self.pasadia, _, self.extra = _crear_catalogo()

    def test_el_total_de_una_pasadia_sin_extras_no_es_cero(self):
        respuesta = self.client.get(
            reverse('api_total_cotizador'), {'servicio': 'PASADIA', 'personas': '10'},
        )
        datos = respuesta.json()
        self.assertGreater(Decimal(datos['total']), Decimal('0'))
        self.assertEqual(datos['lineas'], 1)
        self.assertIn(self.pasadia.nombre, datos['conceptos'][0])

    def test_el_tipo_de_evento_se_refleja_en_el_concepto(self):
        respuesta = self.client.get(
            reverse('api_total_cotizador'),
            {'servicio': 'EVENTO', 'personas': '80', 'horas': '6', 'tipo': 'Boda'},
        )
        self.assertIn('Boda', respuesta.json()['conceptos'][0])

    def test_un_tipo_de_evento_fuera_de_las_opciones_no_se_refleja(self):
        respuesta = self.client.get(
            reverse('api_total_cotizador'),
            {'servicio': 'EVENTO', 'personas': '80', 'horas': '6',
             'tipo': '<script>alert(1)</script>'},
        )
        concepto = respuesta.json()['conceptos'][0]
        self.assertNotIn('script', concepto)
        self.assertIn('Evento General', concepto)

    def test_la_pasadia_ignora_las_horas_que_mande_el_navegador(self):
        # El horario es fijo: 10 horas en la query string no compran hora extra.
        respuesta = self.client.get(
            reverse('api_total_cotizador'),
            {'servicio': 'PASADIA', 'personas': '10', 'horas': '10'},
        )
        self.assertEqual(respuesta.json()['lineas'], 1)


@wa_settings()
class CotizacionCreadaConLineasTest(TestCase):
    """De punta a punta: el POST público crea la cotización ya cobrada."""

    def setUp(self):
        limpiar_cache_emisor()
        cache.clear()
        self.esencial, self.pasadia, self.hora_extra, self.extra = _crear_catalogo()

    def _enviar(self, **extra):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value='5215555550003'):
            return self.client.post(
                reverse('cotizador_enviar'),
                data=json.dumps(_payload(**extra)),
                content_type='application/json',
            )

    def test_una_pasadia_sin_extras_no_queda_en_cero(self):
        respuesta = self._enviar(servicio='PASADIA', personas='10')
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(
            [item.producto for item in cotizacion.items.all()], [self.pasadia],
        )
        self.assertGreater(cotizacion.precio_final, Decimal("0"))

    def test_la_pasadia_queda_con_su_horario_fijo(self):
        self._enviar(servicio='PASADIA', personas='10',
                     hora_inicio='23:00', hora_fin='23:30')

        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(cotizacion.hora_inicio.strftime('%H:%M'), '10:00')
        self.assertEqual(cotizacion.hora_fin.strftime('%H:%M'), '19:00')
        self.assertEqual(cotizacion.horas_servicio, 9)
        self.assertTrue(cotizacion.nombre_evento.startswith('Pasadía —'))

    def test_un_evento_sin_paquete_trae_el_esencial(self):
        respuesta = self._enviar(servicio='EVENTO', personas='80',
                                 hora_inicio='14:00', hora_fin='20:00')
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        self.assertIn(self.esencial, [item.producto for item in cotizacion.items.all()])
        self.assertGreater(cotizacion.precio_final, Decimal("0"))
