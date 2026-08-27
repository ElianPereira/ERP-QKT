"""
Tests de la línea de negocio Hospedaje (Issue #230): rango de noches,
exclusividad de fechas bidireccional (Hospedaje vs. Airbnb / Evento / Pasadía
/ Arrendamiento / otro Hospedaje, y viceversa), selector de habitaciones del
cotizador y formato de horas en a.m./p.m.

Ejecutar: python manage.py test comercial.test_hospedaje --verbosity=2
"""
import json
from datetime import date, timedelta
from datetime import time as dt_time
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from airbnb.models import AnuncioAirbnb, ReservaAirbnb
from airbnb.validacion_fechas import (
    verificar_disponibilidad_fecha,
    verificar_disponibilidad_hospedaje,
    verificar_disponibilidad_rango,
)
from comercial.models import Cliente, Cotizacion, Producto
from comercial.views_cotizador import _lineas_cotizador
from comunicacion.tests.utils import RespuestaFalsa, limpiar_cache_emisor, wa_settings
from core_erp.horarios import formato_hora_ampm


def _payload(**extra):
    return {
        'nombre': 'Ana Ruiz',
        'telefono': '5215555550001',
        'email': 'ana@example.com',
        'servicio': 'HOSPEDAJE',
        'fecha': (timezone.localdate() + timedelta(days=60)).strftime('%Y-%m-%d'),
        'noches': '2',
        'personas': '2',
        'acepta_legales': True,
        **extra,
    }


def _crear_habitaciones():
    kaan = Producto.objects.create(
        nombre="Habitación Ka'an", precio_venta_fijo=Decimal('780.00'),
        visible_cotizador=True, rol_cotizador='HABITACION_HOSPEDAJE',
    )
    otoch = Producto.objects.create(
        nombre='Otoch', precio_venta_fijo=Decimal('650.00'),
        visible_cotizador=True, rol_cotizador='HABITACION_HOSPEDAJE',
    )
    return kaan, otoch


def _crear_persona_extra(precio_con_iva='200.00'):
    # precio_venta_fijo va SIN IVA (mismo criterio que las habitaciones):
    # sugerencia_precio() devuelve la base, con_iva() la convierte al exhibirla.
    from core_erp import impuestos
    return Producto.objects.create(
        nombre='Persona Extra Hospedaje',
        precio_venta_fijo=impuestos.sin_iva(Decimal(precio_con_iva)),
        rol_cotizador='PERSONA_EXTRA_HOSPEDAJE',
    )


class FormatoHoraAmPmTest(TestCase):
    def test_mediodia_y_medianoche(self):
        self.assertEqual(formato_hora_ampm(dt_time(14, 0)), '2:00 p.m.')
        self.assertEqual(formato_hora_ampm(dt_time(10, 0)), '10:00 a.m.')
        self.assertEqual(formato_hora_ampm(dt_time(0, 0)), '12:00 a.m.')
        self.assertEqual(formato_hora_ampm(dt_time(12, 0)), '12:00 p.m.')

    def test_sin_hora(self):
        self.assertEqual(formato_hora_ampm(None), '')


class CotizacionRangoOcupadoTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='C', tipo_persona='FISICA')

    def test_servicio_de_un_dia_ocupa_un_dia(self):
        cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='E', tipo_servicio='EVENTO',
            fecha_evento=date(2026, 12, 10),
        )
        self.assertIsNone(cot.noches)
        self.assertEqual(cot.rango_ocupado(), (date(2026, 12, 10), date(2026, 12, 11)))

    def test_hospedaje_ocupa_el_rango_real(self):
        cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='H', tipo_servicio='HOSPEDAJE',
            fecha_evento=date(2026, 12, 10), fecha_salida=date(2026, 12, 13),
        )
        self.assertEqual(cot.noches, 3)
        self.assertEqual(cot.rango_ocupado(), (date(2026, 12, 10), date(2026, 12, 13)))


class DisponibilidadBidireccionalTest(TestCase):
    """El núcleo del requisito: Hospedaje no convive en fechas con ninguna
    otra línea, y viceversa — en cualquier combinación, incluida una noche
    intermedia de la estancia (no solo el primer/último día)."""

    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='C', tipo_persona='FISICA')
        self.anuncio = AnuncioAirbnb.objects.create(
            nombre='Casa Test', url_ical='https://www.airbnb.mx/calendar/ical/1.ics?s=x',
            afecta_eventos_quinta=True,
        )

    def _cot(self, tipo, fecha_evento, fecha_salida=None, estado='CONFIRMADA'):
        cot = Cotizacion.objects.create(
            cliente=self.cliente, nombre_evento='X', tipo_servicio=tipo,
            fecha_evento=fecha_evento, fecha_salida=fecha_salida,
        )
        # .update() en vez de .save(): evita que Cotizacion.clean() (que ya
        # usa esta misma validación) rechace el fixture al crear a propósito
        # dos reservas que se traslapan.
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
        cot.refresh_from_db()
        return cot

    def _reserva_airbnb(self, ini, fin, estado='CONFIRMADA'):
        return ReservaAirbnb.objects.create(
            anuncio=self.anuncio, uid_ical=f'uid-{ini}', fecha_inicio=ini, fecha_fin=fin,
            estado=estado,
        )

    def test_hospedaje_vs_airbnb_se_traslapan(self):
        self._reserva_airbnb(date(2026, 12, 10), date(2026, 12, 15))
        disponible, msg = verificar_disponibilidad_hospedaje(date(2026, 12, 12), date(2026, 12, 14))
        self.assertFalse(disponible)
        self.assertIn('Fechas no disponibles', msg)

    def test_hospedaje_vs_evento_confirmado(self):
        self._cot('EVENTO', date(2026, 12, 12))
        disponible, msg = verificar_disponibilidad_hospedaje(date(2026, 12, 10), date(2026, 12, 14))
        self.assertFalse(disponible)
        self.assertIn('evento', msg.lower())

    def test_hospedaje_vs_pasadia_confirmada(self):
        self._cot('PASADIA', date(2026, 12, 12))
        disponible, _ = verificar_disponibilidad_hospedaje(date(2026, 12, 10), date(2026, 12, 14))
        self.assertFalse(disponible)

    def test_hospedaje_vs_arrendamiento_confirmado(self):
        self._cot('ARRENDAMIENTO', date(2026, 12, 12))
        disponible, _ = verificar_disponibilidad_hospedaje(date(2026, 12, 10), date(2026, 12, 14))
        self.assertFalse(disponible)

    def test_hospedaje_vs_otro_hospedaje_en_noche_intermedia(self):
        # La reserva existente cubre 10-15 dic; la nueva pide 13-17 — se
        # traslapan solo a mitad de estancia, no en el día de entrada/salida
        # de ninguna de las dos.
        self._cot('HOSPEDAJE', date(2026, 12, 10), fecha_salida=date(2026, 12, 15))
        disponible, msg = verificar_disponibilidad_hospedaje(date(2026, 12, 13), date(2026, 12, 17))
        self.assertFalse(disponible)
        self.assertIn('Hospedaje', msg)

    def test_hospedaje_no_bloquea_fechas_que_no_se_tocan(self):
        self._cot('HOSPEDAJE', date(2026, 12, 10), fecha_salida=date(2026, 12, 13))
        # Empieza justo el día de checkout de la anterior — no hay traslape.
        disponible, _ = verificar_disponibilidad_hospedaje(date(2026, 12, 13), date(2026, 12, 16))
        self.assertTrue(disponible)

    def test_direccion_inversa_evento_vs_hospedaje_confirmado(self):
        self._cot('HOSPEDAJE', date(2026, 12, 10), fecha_salida=date(2026, 12, 15))
        # Un Evento de un solo día, a mitad de una estancia de Hospedaje ya
        # confirmada, también debe verse bloqueado.
        disponible, msg = verificar_disponibilidad_fecha(date(2026, 12, 12))
        self.assertFalse(disponible)
        self.assertIn('Hospedaje', msg)

    def test_direccion_inversa_pasadia_vs_hospedaje_confirmado(self):
        self._cot('HOSPEDAJE', date(2026, 12, 10), fecha_salida=date(2026, 12, 15))
        disponible, _ = verificar_disponibilidad_fecha(date(2026, 12, 14))
        self.assertFalse(disponible)

    def test_cotizacion_borrador_no_bloquea_ninguna_direccion(self):
        self._cot('HOSPEDAJE', date(2026, 12, 10), fecha_salida=date(2026, 12, 15), estado='BORRADOR')
        disponible_evento, _ = verificar_disponibilidad_fecha(date(2026, 12, 12))
        disponible_hosp, _ = verificar_disponibilidad_hospedaje(date(2026, 12, 12), date(2026, 12, 14))
        self.assertTrue(disponible_evento)
        self.assertTrue(disponible_hosp)

    def test_excluye_la_propia_cotizacion(self):
        cot = self._cot('HOSPEDAJE', date(2026, 12, 10), fecha_salida=date(2026, 12, 15))
        disponible, _ = verificar_disponibilidad_rango(
            date(2026, 12, 10), date(2026, 12, 15), cotizacion_id=cot.pk,
        )
        self.assertTrue(disponible)

    def test_dias_libres_de_evento_no_se_bloquean(self):
        disponible, msg = verificar_disponibilidad_fecha(date(2026, 12, 12))
        self.assertTrue(disponible)
        self.assertIsNone(msg)


class LineasHospedajeTest(TestCase):
    """_lineas_cotizador para HOSPEDAJE: sin base automática, una línea por
    habitación elegida, cantidad = noches."""

    def setUp(self):
        cache.clear()
        self.kaan, self.otoch = _crear_habitaciones()

    def test_sin_habitaciones_no_hay_lineas(self):
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None, extras_ids=[],
            num_personas=2, horas_evento=0, noches=2, habitaciones_ids=[],
        )
        self.assertEqual(lineas, [])

    def test_una_habitacion_cobra_por_noche(self):
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None, extras_ids=[],
            num_personas=2, horas_evento=0, noches=3, habitaciones_ids=[self.kaan.id],
        )
        self.assertEqual(len(lineas), 1)
        prod, cantidad, desc = lineas[0]
        self.assertEqual(prod, self.kaan)
        self.assertEqual(cantidad, 3)
        self.assertIn('3 noches', desc)
        self.assertIn('check-in 2:00 p.m.', desc)
        self.assertIn('check-out 10:00 a.m.', desc)

    def test_dos_habitaciones_son_dos_lineas_independientes(self):
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None, extras_ids=[],
            num_personas=2, horas_evento=0, noches=2,
            habitaciones_ids=[self.kaan.id, self.otoch.id],
        )
        productos = {prod for prod, _, _ in lineas}
        self.assertEqual(productos, {self.kaan, self.otoch})
        self.assertTrue(all(cantidad == 2 for _, cantidad, _ in lineas))

    def test_habitacion_no_aparece_como_extra(self):
        respuesta = self.client.get(
            reverse('api_productos_cotizador'), {'servicio': 'HOSPEDAJE'},
        )
        nombres = [
            p['nombre']
            for grupo in respuesta.json()['grupos'] for p in grupo['productos']
        ]
        self.assertNotIn(self.kaan.nombre, nombres)

    def test_un_id_de_habitacion_mandado_como_extra_se_ignora(self):
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None,
            extras_ids=[self.kaan.id], num_personas=2, horas_evento=0,
            noches=2, habitaciones_ids=[],
        )
        self.assertEqual(lineas, [])


class ApiHabitacionesCotizadorTest(TestCase):
    def setUp(self):
        cache.clear()
        self.kaan, self.otoch = _crear_habitaciones()

    def test_devuelve_las_habitaciones_con_iva(self):
        respuesta = self.client.get(reverse('api_habitaciones_cotizador'))
        datos = respuesta.json()
        self.assertTrue(datos['ok'])
        nombres = {h['nombre'] for h in datos['habitaciones']}
        self.assertEqual(nombres, {"Habitación Ka'an", 'Otoch'})
        kaan_datos = next(h for h in datos['habitaciones'] if h['nombre'] == "Habitación Ka'an")
        self.assertEqual(Decimal(kaan_datos['precio_noche']), Decimal('780.00') * Decimal('1.16'))


class ApiTotalCotizadorHospedajeTest(TestCase):
    def setUp(self):
        cache.clear()
        self.kaan, self.otoch = _crear_habitaciones()

    def test_total_escala_con_las_noches(self):
        respuesta = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'HOSPEDAJE', 'noches': '3', 'habitaciones': str(self.kaan.id),
        })
        datos = respuesta.json()
        self.assertEqual(datos['lineas'], 1)
        self.assertGreater(Decimal(datos['total']), Decimal('780.00') * 3)  # con IVA

    def test_dos_habitaciones_suman(self):
        r1 = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'HOSPEDAJE', 'noches': '2', 'habitaciones': str(self.kaan.id),
        })
        r2 = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'HOSPEDAJE', 'noches': '2',
            'habitaciones': f'{self.kaan.id},{self.otoch.id}',
        })
        self.assertGreater(Decimal(r2.json()['total']), Decimal(r1.json()['total']))


class PersonaExtraHospedajeTest(TestCase):
    """Recargo por persona extra sobre la capacidad base de las habitaciones
    elegidas (pedido directo del propietario): sin tope duro de huéspedes,
    $200/noche por persona, se quede o no a dormir. Ka'an y Otoch tienen la
    capacidad por default del modelo (4)."""

    def setUp(self):
        cache.clear()
        self.kaan, self.otoch = _crear_habitaciones()

    def test_personas_dentro_de_capacidad_no_cobra_extra(self):
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None, extras_ids=[],
            num_personas=4, horas_evento=0, noches=2, habitaciones_ids=[self.kaan.id],
        )
        self.assertEqual(len(lineas), 1)  # solo la habitación, sin recargo

    def test_sin_producto_persona_extra_no_cobra_nada(self):
        # Nadie ha dado de alta el producto todavía (mismo patrón que
        # HORA_EXTRA/BASE_EVENTO ausentes): no revienta, solo no cobra el
        # recargo — el propietario lo crea en el admin cuando lo necesite.
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None, extras_ids=[],
            num_personas=6, horas_evento=0, noches=2, habitaciones_ids=[self.kaan.id],
        )
        self.assertEqual(len(lineas), 1)

    def test_personas_extra_cobra_recargo_por_noche(self):
        extra = _crear_persona_extra()
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None, extras_ids=[],
            num_personas=6, horas_evento=0, noches=3, habitaciones_ids=[self.kaan.id],
        )
        self.assertEqual(len(lineas), 2)
        prod, cantidad, desc = next(l for l in lineas if l[0] == extra)
        # Capacidad 4, 6 huéspedes -> 2 personas extra x 3 noches = 6.
        self.assertEqual(cantidad, 6)
        self.assertIn('2 persona', desc)
        self.assertIn('3 noche', desc)

    def test_dos_habitaciones_suman_capacidad(self):
        _crear_persona_extra()
        # Capacidad conjunta 4+4=8; 8 huéspedes no genera recargo.
        lineas = _lineas_cotizador(
            servicio='HOSPEDAJE', paquete_id=None, extras_ids=[],
            num_personas=8, horas_evento=0, noches=2,
            habitaciones_ids=[self.kaan.id, self.otoch.id],
        )
        self.assertEqual(len(lineas), 2)  # las dos habitaciones, sin recargo

    def test_api_habitaciones_expone_capacidad_e_imagen(self):
        respuesta = self.client.get(reverse('api_habitaciones_cotizador'))
        datos = respuesta.json()
        kaan_datos = next(h for h in datos['habitaciones'] if h['nombre'] == "Habitación Ka'an")
        self.assertEqual(kaan_datos['capacidad'], 4)
        self.assertIsNone(kaan_datos['imagen_url'])
        self.assertIsNone(datos['precio_persona_extra'])

    def test_api_habitaciones_expone_precio_persona_extra(self):
        _crear_persona_extra('200.00')
        respuesta = self.client.get(reverse('api_habitaciones_cotizador'))
        datos = respuesta.json()
        self.assertEqual(Decimal(datos['precio_persona_extra']), Decimal('200.00'))

    def test_api_total_incluye_el_recargo(self):
        _crear_persona_extra()
        r_sin_extra = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'HOSPEDAJE', 'personas': '4', 'noches': '2',
            'habitaciones': str(self.kaan.id),
        })
        r_con_extra = self.client.get(reverse('api_total_cotizador'), {
            'servicio': 'HOSPEDAJE', 'personas': '6', 'noches': '2',
            'habitaciones': str(self.kaan.id),
        })
        self.assertGreater(Decimal(r_con_extra.json()['total']), Decimal(r_sin_extra.json()['total']))
        self.assertTrue(any('Persona extra' in c for c in r_con_extra.json()['conceptos']))


@wa_settings()
class CotizadorEnviarHospedajeTest(TestCase):
    """De punta a punta: el POST público crea la Cotización de Hospedaje."""

    def setUp(self):
        limpiar_cache_emisor()
        cache.clear()
        self.kaan, self.otoch = _crear_habitaciones()

    def _enviar(self, **extra):
        payload = _payload(**extra)
        with patch('comunicacion.services.requests.post', return_value=RespuestaFalsa()), \
             patch('comunicacion.services.numero_emisor_wa', return_value='5215555550003'):
            return self.client.post(
                reverse('cotizador_enviar'),
                data=json.dumps(payload),
                content_type='application/json',
            )

    def test_crea_cotizacion_con_fecha_salida_y_horario_fijo(self):
        respuesta = self._enviar(habitaciones_ids=[self.kaan.id])
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        self.assertTrue(respuesta.json()['ok'])

        cot = Cotizacion.objects.latest('id')
        self.assertEqual(cot.tipo_servicio, 'HOSPEDAJE')
        self.assertEqual(cot.noches, 2)
        self.assertEqual(cot.hora_inicio, dt_time(14, 0))
        self.assertEqual(cot.hora_fin, dt_time(10, 0))
        self.assertTrue(cot.nombre_evento.startswith('Hospedaje (2 noches)'))
        self.assertEqual([i.producto for i in cot.items.all()], [self.kaan])
        self.assertEqual(cot.items.first().cantidad, 2)

    def test_sin_habitaciones_se_rechaza(self):
        respuesta = self._enviar(habitaciones_ids=[])
        self.assertEqual(respuesta.status_code, 400)
        self.assertFalse(respuesta.json()['ok'])
        self.assertEqual(Cotizacion.objects.count(), 0)

    def test_sin_noches_se_rechaza_desde_el_form(self):
        respuesta = self._enviar(noches='', habitaciones_ids=[self.kaan.id])
        self.assertEqual(respuesta.status_code, 400)
        self.assertEqual(Cotizacion.objects.count(), 0)

    def test_dos_habitaciones_crean_dos_items(self):
        respuesta = self._enviar(habitaciones_ids=[self.kaan.id, self.otoch.id])
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cot = Cotizacion.objects.latest('id')
        self.assertEqual(cot.items.count(), 2)

    def test_noches_se_acota_al_maximo(self):
        respuesta = self._enviar(noches='999', habitaciones_ids=[self.kaan.id])
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cot = Cotizacion.objects.latest('id')
        self.assertEqual(cot.noches, 30)

    def test_personas_sobre_capacidad_crean_item_de_recargo(self):
        _crear_persona_extra()
        respuesta = self._enviar(personas='6', noches='2', habitaciones_ids=[self.kaan.id])
        self.assertEqual(respuesta.status_code, 200, respuesta.content)
        cot = Cotizacion.objects.latest('id')
        self.assertEqual(cot.items.count(), 2)
        item_extra = cot.items.exclude(producto=self.kaan).get()
        # Capacidad 4, 6 huéspedes -> 2 personas extra x 2 noches = 4.
        self.assertEqual(item_extra.cantidad, 4)


class MontoMinimoHospedajeTest(TestCase):
    """DIAS_PAGO_TOTAL['HOSPEDAJE'] = 7, mismo umbral que Pasadía."""

    def _crear(self, dias_para_checkin):
        cliente = Cliente.objects.create(nombre='C', tipo_persona='FISICA')
        cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='H', tipo_servicio='HOSPEDAJE',
            fecha_evento=date.today() + timedelta(days=dias_para_checkin),
            fecha_salida=date.today() + timedelta(days=dias_para_checkin + 2),
        )
        from comercial.models import ItemCotizacion
        ItemCotizacion.objects.create(
            cotizacion=cot, descripcion='Habitación', cantidad=2,
            precio_unitario=Decimal('780.00'),
        )
        cot.save()
        cot.refresh_from_db()
        return cot

    def test_a_mas_de_siete_dias_admite_anticipo(self):
        cot = self._crear(10)
        minimo, _ = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final * Decimal('0.50'))

    def test_a_menos_de_siete_dias_exige_el_total(self):
        cot = self._crear(5)
        minimo, motivo = cot.monto_minimo_pago_detalle()
        self.assertEqual(minimo, cot.precio_final)
        self.assertIn('7 días', motivo)
