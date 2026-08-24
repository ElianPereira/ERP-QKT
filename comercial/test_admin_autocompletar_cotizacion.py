"""Tests de CotizacionAdmin.autocompletar_cotizacion_nueva.

Al crear una Cotizacion desde el admin: (1) Pasadía/Hospedaje reciben su
horario fijo si se dejó en blanco, sin caer en "Por definir"; (2)
nombre_evento se arma con tipo de servicio + tipo de evento (solo EVENTO) +
primer nombre del cliente, en vez del "Evento General" genérico.
"""
from datetime import date, time

from django.test import TestCase

from comercial.admin import autocompletar_cotizacion_nueva
from comercial.models import Cliente, Cotizacion, TipoEvento
from comercial.views_cotizador import (
    HORA_FIN_HOSPEDAJE,
    HORA_FIN_PASADIA,
    HORA_INICIO_HOSPEDAJE,
    HORA_INICIO_PASADIA,
)


class AutocompletarCotizacionNuevaTest(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Juan Pérez López')

    def _cotizacion(self, **kwargs):
        defaults = dict(cliente=self.cliente, fecha_evento=date(2027, 1, 1))
        defaults.update(kwargs)
        return Cotizacion(**defaults)

    def test_pasadia_recibe_horario_fijo_si_esta_vacio(self):
        obj = self._cotizacion(tipo_servicio='PASADIA')
        autocompletar_cotizacion_nueva(obj)
        self.assertEqual(obj.hora_inicio, HORA_INICIO_PASADIA)
        self.assertEqual(obj.hora_fin, HORA_FIN_PASADIA)

    def test_hospedaje_recibe_horario_fijo_si_esta_vacio(self):
        obj = self._cotizacion(tipo_servicio='HOSPEDAJE')
        autocompletar_cotizacion_nueva(obj)
        self.assertEqual(obj.hora_inicio, HORA_INICIO_HOSPEDAJE)
        self.assertEqual(obj.hora_fin, HORA_FIN_HOSPEDAJE)

    def test_pasadia_no_pisa_un_horario_ya_capturado_a_mano(self):
        obj = self._cotizacion(
            tipo_servicio='PASADIA', hora_inicio=time(9, 0), hora_fin=time(17, 0),
        )
        autocompletar_cotizacion_nueva(obj)
        self.assertEqual(obj.hora_inicio, time(9, 0))
        self.assertEqual(obj.hora_fin, time(17, 0))

    def test_evento_y_arrendamiento_se_quedan_sin_horario(self):
        for tipo in ('EVENTO', 'ARRENDAMIENTO'):
            obj = self._cotizacion(tipo_servicio=tipo)
            autocompletar_cotizacion_nueva(obj)
            self.assertIsNone(obj.hora_inicio)
            self.assertIsNone(obj.hora_fin)

    def test_nombre_evento_pasadia_es_tipo_servicio_mas_primer_nombre(self):
        obj = self._cotizacion(tipo_servicio='PASADIA')
        autocompletar_cotizacion_nueva(obj)
        self.assertEqual(obj.nombre_evento, 'Pasadía - Juan')

    def test_nombre_evento_hospedaje_es_tipo_servicio_mas_primer_nombre(self):
        obj = self._cotizacion(tipo_servicio='HOSPEDAJE')
        autocompletar_cotizacion_nueva(obj)
        self.assertEqual(obj.nombre_evento, 'Hospedaje - Juan')

    def test_nombre_evento_evento_incluye_tipo_de_evento_si_esta_marcado(self):
        boda, _ = TipoEvento.objects.get_or_create(nombre='Boda')
        obj = self._cotizacion(tipo_servicio='EVENTO', tipo_evento=boda)
        autocompletar_cotizacion_nueva(obj)
        self.assertEqual(obj.nombre_evento, 'Evento - Boda - Juan')

    def test_nombre_evento_evento_sin_tipo_de_evento_marcado(self):
        obj = self._cotizacion(tipo_servicio='EVENTO')
        autocompletar_cotizacion_nueva(obj)
        self.assertEqual(obj.nombre_evento, 'Evento - Juan')
