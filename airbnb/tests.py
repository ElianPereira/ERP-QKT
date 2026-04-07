"""
Tests del módulo Airbnb
=======================
"""
from datetime import date, time, timedelta
from django.test import TestCase

from airbnb.models import AnuncioAirbnb, ReservaAirbnb
from airbnb.services import DetectorConflictosService
from comercial.models import Cliente, Cotizacion


class ConflictoFechasTest(TestCase):

    def setUp(self):
        self.svc = DetectorConflictosService()
        self.anuncio = AnuncioAirbnb.objects.create(
            nombre='Casa Test',
            url_ical='https://www.airbnb.mx/calendar/ical/123.ics?s=x',
            afecta_eventos_quinta=True,
        )
        self.cliente = Cliente.objects.create(nombre='C')

    def _reserva(self, ini, fin):
        return ReservaAirbnb.objects.create(
            anuncio=self.anuncio,
            uid_ical=f'uid-{ini}',
            fecha_inicio=ini,
            fecha_fin=fin,
        )

    def _cot(self, fecha, hi=None, hf=None):
        return Cotizacion.objects.create(
            cliente=self.cliente,
            nombre_evento='E',
            fecha_evento=fecha,
            hora_inicio=hi, hora_fin=hf,
            incluye_refrescos=False, incluye_cerveza=False,
            incluye_licor_nacional=False, incluye_licor_premium=False,
            incluye_cocteleria_basica=False, incluye_cocteleria_premium=False,
        )

    def test_evento_mismo_dia_conflicta(self):
        r = self._reserva(date(2026, 5, 10), date(2026, 5, 12))
        c = self._cot(date(2026, 5, 11))
        self.assertTrue(self.svc._hay_conflicto_fechas(r, c))

    def test_evento_no_solapado_no_conflicta(self):
        r = self._reserva(date(2026, 5, 10), date(2026, 5, 12))
        c = self._cot(date(2026, 5, 15))
        self.assertFalse(self.svc._hay_conflicto_fechas(r, c))

    def test_evento_overnight_invade_dia_siguiente(self):
        """Evento 9am-5am del día siguiente debe detectar reserva del día sig."""
        r = self._reserva(date(2026, 5, 11), date(2026, 5, 13))
        c = self._cot(date(2026, 5, 10), hi=time(9, 0), hf=time(5, 0))
        self.assertTrue(self.svc._hay_conflicto_fechas(r, c))

    def test_checkout_mismo_dia_no_conflicta(self):
        """Reserva que termina el día del evento (checkout AM) no conflicta."""
        r = self._reserva(date(2026, 5, 8), date(2026, 5, 10))
        c = self._cot(date(2026, 5, 10))
        self.assertFalse(self.svc._hay_conflicto_fechas(r, c))
