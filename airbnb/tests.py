"""
Tests del módulo Airbnb
=======================
"""
from datetime import date, time, timedelta
from decimal import Decimal

from django.test import TestCase

from airbnb.models import AnuncioAirbnb, PagoAirbnb, ReservaAirbnb
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


# ==========================================
# IMPORTACIÓN DE PAGOS (CSV de Airbnb)
# ==========================================
class ImportadorCSVPagosTest(TestCase):
    """
    Cada prueba fija uno de los defectos fiscales que producían cifras
    incorrectas en la declaración mensual.
    """

    CABECERA = ('Fecha,Tipo,Código de confirmación,Huésped,Espacio,'
                'Fecha de inicio,Fecha de finalización,Noches,Monto,'
                'Tarifa de servicio,Ingresos brutos\n')

    def _importar(self, filas, **kwargs):
        from airbnb.services import ImportadorCSVPagosService
        csv = self.CABECERA + ''.join(filas)
        return ImportadorCSVPagosService(archivo_nombre='t.csv').importar(csv, **kwargs)

    def _fila_reserva(self, codigo='HM001', monto='5000.00', brutos='5000.00',
                      tarifa='150.00', fecha_pago='09/02/2026'):
        return (f'{fecha_pago},Reservación,{codigo},Ana López,Casa Miel,'
                f'09/05/2026,09/08/2026,3,{monto},{tarifa},{brutos}\n')

    def test_no_inventa_retenciones_que_airbnb_no_aplico(self):
        """
        El defecto más caro: `save()` recalculaba si alguna retención venía en
        cero, así que a una reserva sin retención de IVA el sistema le
        inventaba un 8% inexistente y recalculaba el neto. La declaración
        dejaba de cuadrar con la constancia de Airbnb.
        """
        self._importar([self._fila_reserva()])

        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        self.assertEqual(pago.retencion_iva, Decimal('0.00'))
        self.assertEqual(pago.retencion_isr, Decimal('0.00'))
        # El neto es el bruto: no hubo retenciones que restar.
        self.assertEqual(pago.monto_neto, Decimal('5000.00'))

    def test_no_resta_dos_veces_la_comision(self):
        """
        `Ingresos brutos` ya viene neto de la tarifa de servicio. Antes se
        tomaba el bruto de ese campo y se le volvía a restar la comisión.
        """
        self._importar([self._fila_reserva(monto='4850.00', brutos='4850.00',
                                           tarifa='150.00')])
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        self.assertEqual(pago.monto_bruto, Decimal('4850.00'))
        self.assertEqual(pago.monto_neto, Decimal('4850.00'))

    def test_registra_la_fecha_de_pago_que_define_el_periodo_fiscal(self):
        """Antes se marcaba PAGADO con `fecha_pago` en null."""
        self._importar([self._fila_reserva(fecha_pago='09/02/2026')])
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        self.assertEqual(pago.fecha_pago, date(2026, 9, 2))
        self.assertEqual(pago.estado, 'PAGADO')

    def test_reimportar_actualiza_en_vez_de_omitir(self):
        """
        Airbnb altera reservas y ajusta montos después del hecho. Antes el
        segundo CSV decía 'duplicado' y el ERP se quedaba con el dato viejo.
        """
        self._importar([self._fila_reserva(monto='5000.00', brutos='5000.00')])
        resumen = self._importar([self._fila_reserva(monto='5500.00', brutos='5500.00')])

        self.assertEqual(len(resumen['actualizados']), 1)
        self.assertEqual(PagoAirbnb.objects.count(), 1)
        self.assertEqual(
            PagoAirbnb.objects.get(codigo_confirmacion='HM001').monto_bruto,
            Decimal('5500.00'),
        )

    def test_reimportar_sin_cambios_no_toca_nada(self):
        self._importar([self._fila_reserva()])
        resumen = self._importar([self._fila_reserva()])
        self.assertEqual(resumen['sin_cambios'], ['HM001'])
        self.assertEqual(resumen['actualizados'], [])

    def test_no_pisa_un_pago_capturado_a_mano(self):
        self._importar([self._fila_reserva()])
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        pago.origen = 'MANUAL'
        pago.monto_bruto = Decimal('9999.00')
        pago.save()

        self._importar([self._fila_reserva(monto='5000.00', brutos='5000.00')])
        pago.refresh_from_db()
        self.assertEqual(pago.monto_bruto, Decimal('9999.00'))

    def test_los_reembolsos_dejan_de_desaparecer(self):
        """
        Las filas de reembolso/ajuste no caían en ninguna rama del
        clasificador, así que un reembolso al huésped no se reflejaba.
        """
        filas = [
            self._fila_reserva(monto='5000.00', brutos='5000.00', tarifa='0.00'),
            '09/10/2026,Reembolso,HM001,Ana López,Casa Miel,'
            '09/05/2026,09/08/2026,3,-5000.00,0.00,0.00\n',
        ]
        self._importar(filas)

        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        self.assertEqual(pago.monto_neto, Decimal('0.00'))
        self.assertEqual(pago.estado, 'REEMBOLSADO')

    def test_guarda_el_impuesto_de_hospedaje_como_campo(self):
        """Antes se escribía como texto dentro de `notas` y no se podía sumar."""
        filas = [
            self._fila_reserva(),
            '09/02/2026,Impuestos liquidados como anfitrión,HM001,Ana López,'
            'Casa Miel,09/05/2026,09/08/2026,3,250.00,0.00,0.00\n',
        ]
        self._importar(filas)
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        self.assertEqual(pago.impuesto_hospedaje, Decimal('250.00'))

    def test_la_simulacion_no_escribe_nada(self):
        resumen = self._importar([self._fila_reserva()], simular=True)
        self.assertTrue(resumen['simulado'])
        self.assertEqual(resumen['creados'], ['HM001'])
        self.assertEqual(PagoAirbnb.objects.count(), 0)

    def test_vincula_el_pago_con_su_reserva_del_calendario(self):
        """El FK existía desde siempre pero nadie lo llenaba."""
        anuncio = AnuncioAirbnb.objects.create(
            nombre='Casa Miel', url_ical='https://airbnb.mx/calendar/ical/1.ics')
        reserva = ReservaAirbnb.objects.create(
            anuncio=anuncio, uid_ical='uid-1',
            fecha_inicio=date(2026, 9, 5), fecha_fin=date(2026, 9, 8))

        self._importar([self._fila_reserva()])
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        self.assertEqual(pago.reserva_id, reserva.id)

    def test_marca_los_pagos_cuyo_neto_no_cuadra(self):
        pago = PagoAirbnb.objects.create(
            huesped='X', fecha_checkin=date(2026, 9, 5),
            fecha_checkout=date(2026, 9, 8), monto_bruto=Decimal('1000.00'),
            comision_airbnb=Decimal('30.00'), retencion_isr=Decimal('40.00'),
            retencion_iva=Decimal('80.00'), monto_neto=Decimal('900.00'),
        )
        # 1000 - 30 - 40 - 80 = 850, no 900.
        self.assertEqual(pago.diferencia_neto, Decimal('50.00'))
        self.assertFalse(pago.cuadra)


class RetencionesPlataformaTest(TestCase):
    """El 8% del que habla la regla es de la BASE, no del monto con IVA."""

    def test_el_iva_retenido_es_la_mitad_del_trasladado(self):
        from core_erp.impuestos import retenciones_plataforma

        r = retenciones_plataforma(Decimal('1160.00'))
        self.assertEqual(r['base'], Decimal('1000.00'))
        self.assertEqual(r['iva_trasladado'], Decimal('160.00'))
        self.assertEqual(r['ret_isr'], Decimal('40.00'))
        self.assertEqual(r['ret_iva'], Decimal('80.00'))
        # Aplicar 8% sobre el bruto daría 92.80: un 16% de más, que es
        # exactamente lo que calculaba el código anterior.
        self.assertNotEqual(r['ret_iva'], Decimal('92.80'))

    def test_sin_rfc_las_tasas_suben(self):
        from core_erp.impuestos import retenciones_plataforma

        r = retenciones_plataforma(Decimal('1160.00'), con_rfc=False)
        self.assertEqual(r['ret_isr'], Decimal('200.00'))   # 20%
        self.assertEqual(r['ret_iva'], Decimal('160.00'))   # 100% del IVA
