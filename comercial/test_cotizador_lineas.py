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

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Producto
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
        # Son 8 horas fijas, no 6 + 3 adicionales.
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=10, horas_evento=8,
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
            rol_cotizador='BASE_PASADIA_BASICO',
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
class PersonaExtraPasadiaTest(TestCase):
    """Aforo ampliado de Pasadía (20 → 30, Reglamento Interno v1.2)."""

    def setUp(self):
        cache.clear()
        self.esencial, self.pasadia, self.hora_extra, self.extra = _crear_catalogo()
        self.persona_extra = Producto.objects.create(
            nombre='Persona Extra Pasadía', precio_venta_fijo=Decimal('155.17'),
            visible_cotizador=True, cotizador_pasadia=True,
            rol_cotizador='PERSONA_EXTRA_PASADIA',
        )

    def test_pasadia_20_personas_sin_cargo_extra(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=20, horas_evento=8,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [self.pasadia])

    def test_pasadia_25_personas_cobra_5_extra(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=25, horas_evento=8,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [self.pasadia, self.persona_extra])
        self.assertEqual(lineas[1][1], 5)

    def test_pasadia_30_personas_cobra_10_extra(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=30, horas_evento=8,
        )
        self.assertEqual(lineas[1][1], 10)

    def test_pasadia_31_personas_se_limita_a_30(self):
        # El backend topa antes de llegar aquí (_redondear_personas /
        # api_total_cotizador); esta prueba confirma el tope real de punta a
        # punta, vía el endpoint público, no solo la función interna.
        respuesta = self.client.get(
            reverse('api_total_cotizador'), {'servicio': 'PASADIA', 'personas': '31'},
        )
        self.assertIn('(10 personas adicionales', respuesta.json()['conceptos'][1])

    def test_total_exhibido_pasadia_incluye_extra(self):
        # Misma prueba de fuego que test_total_exhibido_igual_al_precio_final
        # (comercial/test_openpay.py), pero con el aforo ampliado de Pasadía.
        from core_erp import impuestos
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=25, horas_evento=8,
        )
        bases = [Decimal(str(pr.sugerencia_precio())) * Decimal(q) for pr, q, _ in lineas]
        exhibido = impuestos.total_desde_bases(bases)

        respuesta = self.client.get(
            reverse('api_total_cotizador'), {'servicio': 'PASADIA', 'personas': '25'},
        )
        self.assertEqual(str(exhibido), respuesta.json()['total'])

        cliente = Cliente.objects.create(nombre='Cliente Pasadía Ampliada', tipo_persona='FISICA')
        cot = Cotizacion.objects.create(
            cliente=cliente, tipo_servicio='PASADIA', nombre_evento='Pasadía',
            fecha_evento=timezone.localdate() + timedelta(days=30), incluye_refrescos=False,
        )
        for prod, qty, _desc in lineas:
            ItemCotizacion.objects.create(
                cotizacion=cot, producto=prod, descripcion=prod.nombre,
                cantidad=Decimal(qty), precio_unitario=Decimal(str(prod.sugerencia_precio())),
            )
        cot.save()
        cot.refresh_from_db()
        self.assertEqual(exhibido, cot.precio_final)

    def test_persona_extra_precio_iva_incluido(self):
        # 1 unidad de precio_venta_fijo=155.17 (antes de IVA) debe dar $180.00
        # exactos con IVA incluido — el importe ya decidido por el owner.
        from core_erp import impuestos
        base = Decimal(str(self.persona_extra.sugerencia_precio()))
        self.assertEqual(impuestos.con_iva(base), Decimal('180.00'))


@wa_settings()
class NivelPasadiaTest(TestCase):
    """Toggle Básico/Premium de Pasadía (pedido directo del propietario,
    2026-09-03): dos productos fijos por rol_cotizador, Básico por default,
    y Premium reemplaza —no se suma a— el cargo de persona extra (21-30),
    porque su precio fijo ya incluye ese mobiliario."""

    def setUp(self):
        limpiar_cache_emisor()
        cache.clear()
        self.basico = Producto.objects.create(
            nombre='Pasadía Básico', precio_venta_fijo=Decimal('1724.14'),
            visible_cotizador=False, cotizador_pasadia=True,
            rol_cotizador='BASE_PASADIA_BASICO',
        )
        self.premium = Producto.objects.create(
            nombre='Pasadía Premium', precio_venta_fijo=Decimal('2586.21'),
            visible_cotizador=False, cotizador_pasadia=True,
            rol_cotizador='BASE_PASADIA_PREMIUM',
        )
        self.persona_extra = Producto.objects.create(
            nombre='Persona Extra Pasadía', precio_venta_fijo=Decimal('155.17'),
            visible_cotizador=False, rol_cotizador='PERSONA_EXTRA_PASADIA',
        )

    def test_default_es_basico(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=15, horas_evento=9,
        )
        self.assertEqual([prod for prod, _, _ in lineas], [self.basico])

    def test_premium_elegido_usa_el_producto_premium(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=15, horas_evento=9, nivel_pasadia='PREMIUM',
        )
        self.assertEqual([prod for prod, _, _ in lineas], [self.premium])

    def test_premium_no_duplica_el_cargo_de_persona_extra(self):
        # Premium ya incluye mobiliario para las 10 personas extra (21-30)
        # en su precio fijo — el cargo aparte no debe aparecer, aunque el
        # producto de recargo sí exista en el catálogo.
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=27, horas_evento=9, nivel_pasadia='PREMIUM',
        )
        self.assertEqual([prod for prod, _, _ in lineas], [self.premium])

    def test_basico_si_cobra_el_cargo_de_persona_extra(self):
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=27, horas_evento=9, nivel_pasadia='BASICO',
        )
        self.assertEqual([prod for prod, _, _ in lineas], [self.basico, self.persona_extra])
        self.assertEqual(lineas[1][1], 7)

    def test_premium_sin_configurar_cae_a_basico(self):
        # Instalación donde el owner todavía no dio de alta el producto
        # Premium: la cotización no debe quedarse sin línea base.
        self.premium.delete()
        lineas = _lineas_cotizador(
            servicio='PASADIA', paquete_id=None, extras_ids=[],
            num_personas=15, horas_evento=9, nivel_pasadia='PREMIUM',
        )
        self.assertEqual([prod for prod, _, _ in lineas], [self.basico])

    def test_api_productos_expone_los_dos_precios_con_iva(self):
        from core_erp import impuestos
        respuesta = self.client.get(
            reverse('api_productos_cotizador'), {'servicio': 'PASADIA'},
        )
        datos = respuesta.json()
        self.assertEqual(
            datos['nivel_pasadia_basico_precio'],
            str(impuestos.con_iva(Decimal(str(self.basico.sugerencia_precio())))),
        )
        self.assertEqual(
            datos['nivel_pasadia_premium_precio'],
            str(impuestos.con_iva(Decimal(str(self.premium.sugerencia_precio())))),
        )

    def test_api_productos_no_ofrece_premium_sin_configurar(self):
        self.premium.delete()
        respuesta = self.client.get(
            reverse('api_productos_cotizador'), {'servicio': 'PASADIA'},
        )
        self.assertIsNone(respuesta.json()['nivel_pasadia_premium_precio'])

    def test_api_total_cotizador_respeta_el_nivel_elegido(self):
        respuesta = self.client.get(
            reverse('api_total_cotizador'),
            {'servicio': 'PASADIA', 'personas': '15', 'nivel_pasadia': 'PREMIUM'},
        )
        self.assertEqual(respuesta.json()['total'], '3000.00')

    def test_cotizador_enviar_crea_la_cotizacion_con_premium(self):
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value='5215555550003'):
            respuesta = self.client.post(
                reverse('cotizador_enviar'),
                data=json.dumps(_payload(
                    servicio='PASADIA', personas='27', nivel_pasadia='PREMIUM',
                )),
                content_type='application/json',
            )
        self.assertEqual(respuesta.status_code, 200)
        cotizacion = Cotizacion.objects.latest('id')
        self.assertEqual(
            [item.producto for item in cotizacion.items.all()], [self.premium],
        )
        self.assertEqual(cotizacion.precio_final, Decimal('3000.00'))


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
        self.assertEqual(cotizacion.hora_inicio.strftime('%H:%M'), '11:00')
        self.assertEqual(cotizacion.hora_fin.strftime('%H:%M'), '19:00')
        self.assertEqual(cotizacion.horas_servicio, 8)
        self.assertTrue(cotizacion.nombre_evento.startswith('Pasadía —'))

    def test_un_evento_sin_paquete_trae_el_esencial(self):
        respuesta = self._enviar(servicio='EVENTO', personas='80',
                                 hora_inicio='14:00', hora_fin='20:00')
        self.assertEqual(respuesta.status_code, 200)

        cotizacion = Cotizacion.objects.latest('id')
        self.assertIn(self.esencial, [item.producto for item in cotizacion.items.all()])
        self.assertGreater(cotizacion.precio_final, Decimal("0"))
