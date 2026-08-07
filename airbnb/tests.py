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

    def test_el_iva_trasladado_suma_al_deposito(self):
        """
        "Impuestos liquidados como anfitrión" es el IVA que Airbnb cobra al
        huésped y transfiere para que lo entere el anfitrión: suma al neto.
        Antes se guardaba como texto dentro de `notas` y no se podía sumar.
        """
        filas = [
            self._fila_reserva(monto='5000.00', brutos='5000.00', tarifa='0.00'),
            '09/02/2026,Impuestos liquidados como anfitrión,HM001,Ana López,'
            'Casa Miel,09/05/2026,09/08/2026,3,800.00,0.00,0.00\n',
        ]
        self._importar(filas)
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HM001')
        self.assertEqual(pago.iva_trasladado, Decimal('800.00'))
        self.assertEqual(pago.monto_neto, Decimal('5800.00'))

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
    """
    Las tasas se aplican sobre la BASE. La columna "Ingresos brutos" del CSV
    de Airbnb ya es esa base: pese al nombre, no trae IVA.
    """

    def test_el_iva_retenido_es_la_mitad_del_trasladado(self):
        from core_erp.impuestos import retenciones_plataforma

        r = retenciones_plataforma(Decimal('1000.00'))
        self.assertEqual(r['base'], Decimal('1000.00'))
        self.assertEqual(r['iva_trasladado'], Decimal('160.00'))
        self.assertEqual(r['ret_isr'], Decimal('40.00'))
        self.assertEqual(r['ret_iva'], Decimal('80.00'))

    def test_coincide_con_una_reserva_real_del_reporte_de_marzo(self):
        """Base 640.00 -> IVA 102.40, ISR 25.60, IVA retenido 51.20."""
        from core_erp.impuestos import retenciones_plataforma

        r = retenciones_plataforma(Decimal('640.00'))
        self.assertEqual(r['iva_trasladado'], Decimal('102.40'))
        self.assertEqual(r['ret_isr'], Decimal('25.60'))
        self.assertEqual(r['ret_iva'], Decimal('51.20'))

    def test_sin_rfc_las_tasas_suben(self):
        from core_erp.impuestos import retenciones_plataforma

        r = retenciones_plataforma(Decimal('1000.00'), con_rfc=False)
        self.assertEqual(r['ret_isr'], Decimal('200.00'))   # 20%
        self.assertEqual(r['ret_iva'], Decimal('160.00'))   # 100% del IVA


class CSVRealDeAirbnbTest(TestCase):
    """
    Contraste contra el reporte real de marzo de 2026 (huéspedes anonimizados).

    Es la única prueba que no usa datos que yo inventé, y por eso es la que
    manda: los tres payouts del archivo deben reconstruirse al centavo.
    """

    PAYOUTS_REALES = {
        'HMRPZQNE4S': Decimal('643.36'),
        'HMZQ99HWB8': Decimal('643.33'),
        'HMFRS8DQ8Z': Decimal('15234.36'),
    }

    def setUp(self):
        from pathlib import Path

        from airbnb.services import ImportadorCSVPagosService

        ruta = (Path(__file__).parent / 'tests_fixtures' / 'airbnb_marzo_2026.csv')
        self.resumen = ImportadorCSVPagosService(
            archivo_nombre='airbnb_marzo_2026.csv'
        ).importar(ruta.read_text(encoding='utf-8'))

    def test_importa_las_tres_reservas_del_mes(self):
        self.assertEqual(self.resumen['errores'], [])
        self.assertEqual(sorted(self.resumen['creados']),
                         sorted(self.PAYOUTS_REALES))

    def test_el_neto_reconstruye_el_deposito_real_al_centavo(self):
        """
        La prueba de fuego: si el neto no coincide con lo que Airbnb depositó,
        la conciliación bancaria no cuadra y la declaración tampoco.
        """
        for codigo, payout in self.PAYOUTS_REALES.items():
            with self.subTest(codigo=codigo):
                pago = PagoAirbnb.objects.get(codigo_confirmacion=codigo)
                self.assertEqual(pago.monto_neto, payout)

    def test_ningun_pago_queda_descuadrado(self):
        self.assertEqual(self.resumen['descuadrados'], [])
        for pago in PagoAirbnb.objects.all():
            self.assertTrue(pago.cuadra, f'{pago.codigo_confirmacion} descuadra')

    @staticmethod
    def _holgura(pago):
        """
        Airbnb redondea noche por noche y nosotros sobre el total, así que la
        desviación crece con la estancia: en una reserva de 3 noches de julio
        el IVA difiere 3 centavos y el retenido 4. Un centavo fijo daba un
        falso positivo. Se tolera un centavo por noche, con mínimo de dos.
        """
        return max(Decimal('0.02'), Decimal(pago.noches) * Decimal('0.01'))

    def test_las_retenciones_reales_coinciden_con_las_que_marca_la_ley(self):
        """
        Con el RFC registrado en Airbnb: ISR 4% de la base e IVA retenido la
        mitad del trasladado.
        """
        for codigo in self.PAYOUTS_REALES:
            with self.subTest(codigo=codigo):
                pago = PagoAirbnb.objects.get(codigo_confirmacion=codigo)
                esperado = pago.retenciones_esperadas(con_rfc=True)
                holgura = self._holgura(pago)
                for campo, calculado in (
                    ('retencion_isr', esperado['ret_isr']),
                    ('retencion_iva', esperado['ret_iva']),
                    ('iva_trasladado', esperado['iva_trasladado']),
                ):
                    self.assertLessEqual(
                        abs(getattr(pago, campo) - calculado), holgura,
                        f'{campo}: {getattr(pago, campo)} vs {calculado} esperado',
                    )

    def test_la_reserva_de_julio_de_la_app_cuadra_con_la_formula(self):
        """
        Contraste contra lo que muestra la app de Airbnb, que es lo que ve el
        anfitrión. Tres noches de $1,410:

            4,230.00  base
            -  785.08  comisión (16% + IVA)
            +  676.83  IVA trasladado
            -  169.20  ISR retenido (4%)
            -  338.38  IVA retenido (8%)
            ---------
            3,614.17  "You earn"
        """
        pago = PagoAirbnb(
            huesped='Julio', fecha_checkin=date(2026, 7, 18),
            fecha_checkout=date(2026, 7, 21),
            monto_bruto=Decimal('4230.00'), comision_airbnb=Decimal('785.08'),
            iva_trasladado=Decimal('676.83'), retencion_isr=Decimal('169.20'),
            retencion_iva=Decimal('338.38'), monto_neto=Decimal('3614.17'),
        )
        self.assertEqual(pago.diferencia_neto, Decimal('0.00'))
        self.assertTrue(pago.cuadra)

        # El desvío por el redondeo de Airbnb cabe en la holgura por noche,
        # pero NO en un centavo fijo: es el caso que obligó a ampliarla.
        esperado = pago.retenciones_esperadas(con_rfc=True)
        desvio = abs(pago.retencion_iva - esperado['ret_iva'])
        self.assertGreater(desvio, Decimal('0.01'))
        self.assertLessEqual(desvio, self._holgura(pago))

    def test_distingue_el_iva_trasladado_del_impuesto_al_hospedaje(self):
        """
        Dos columnas distintas que es fácil confundir: el IVA lo transfiere
        Airbnb para que lo entere el anfitrión (suma al depósito), y el ISH lo
        retiene y entera la propia plataforma (no llega).
        """
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HMRPZQNE4S')
        self.assertEqual(pago.iva_trasladado, Decimal('102.40'))
        self.assertEqual(pago.impuesto_hospedaje, Decimal('28.82'))

    def test_agrupa_los_pagos_por_deposito(self):
        """El payout permite cuadrar contra el movimiento bancario."""
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HMFRS8DQ8Z')
        self.assertEqual(pago.payout_id, '0MS1RHz4c8515YKnnfW89NcM0tS')

    def test_la_fecha_de_pago_es_la_del_movimiento_no_la_del_checkin(self):
        """
        HMFRS8DQ8Z tiene check-in el 11 de marzo y check-out el 25 de ABRIL,
        pero Airbnb la liquidó el 12 de marzo: el ingreso es de marzo.
        """
        pago = PagoAirbnb.objects.get(codigo_confirmacion='HMFRS8DQ8Z')
        self.assertEqual(pago.fecha_pago, date(2026, 3, 12))
        self.assertEqual(pago.fecha_checkout, date(2026, 4, 25))

    def test_no_contiene_datos_personales_de_huespedes(self):
        """El fixture va al repositorio: los nombres reales no."""
        from pathlib import Path

        ruta = (Path(__file__).parent / 'tests_fixtures' / 'airbnb_marzo_2026.csv')
        contenido = ruta.read_text(encoding='utf-8')
        for pago in PagoAirbnb.objects.all():
            self.assertIn('Demo', pago.huesped)
        self.assertNotIn('Checking 8931', contenido)


class ConciliacionDepositosTest(TestCase):
    """
    C2: cuadrar lo que Airbnb dice haber depositado contra el estado de cuenta.

    Airbnb junta en un payout las reservas que liquida el mismo día, así que
    el banco trae un abono por depósito y no uno por reserva: la conciliación
    solo cuadra si se suma primero por `payout_id`.
    """

    def setUp(self):
        from contabilidad.models import CuentaBancaria, EstadoCuentaBancario

        self.anuncio = AnuncioAirbnb.objects.create(
            nombre='Kaan Room', url_ical='https://airbnb.com/ical/1'
        )
        self.cuenta = CuentaBancaria.objects.create(
            nombre='BBVA Principal', banco='BBVA',
            clabe='012345678901234567',
        )
        self.estado_cuenta = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta, periodo_mes=3, periodo_anio=2026,
            archivo='estados_cuenta/2026/03/demo.pdf', formato='PDF',
        )

    def _pago(self, codigo, neto, fecha_pago, payout_id='PAYOUT-1', **extra):
        campos = {
            'anuncio': self.anuncio,
            'codigo_confirmacion': codigo,
            'huesped': 'Huésped Demo',
            'fecha_checkin': fecha_pago,
            'fecha_checkout': fecha_pago + timedelta(days=1),
            'monto_bruto': neto,
            'monto_neto': neto,
            'fecha_pago': fecha_pago,
            'payout_id': payout_id,
            'estado': 'PAGADO',
        }
        campos.update(extra)
        return PagoAirbnb.objects.create(**campos)

    def _abono(self, monto, fecha, **extra):
        from contabilidad.models import MovimientoEstadoCuenta

        return MovimientoEstadoCuenta.objects.create(
            estado_cuenta=self.estado_cuenta, fecha=fecha,
            abono=Decimal(monto), **extra
        )

    @staticmethod
    def _conciliar(mes=3, anio=2026):
        from airbnb.services import ConciliacionDepositosService

        servicio = ConciliacionDepositosService(mes=mes, anio=anio)
        return servicio, servicio.conciliar()

    def test_suma_los_pagos_de_un_payout_y_cuadra_contra_el_abono(self):
        self._pago('HM1', Decimal('600.00'), date(2026, 3, 12))
        self._pago('HM2', Decimal('400.00'), date(2026, 3, 12))
        self._abono('1000.00', date(2026, 3, 17))

        _, depositos = self._conciliar()

        self.assertEqual(len(depositos), 1)
        deposito = depositos[0]
        self.assertEqual(deposito['total'], Decimal('1000.00'))
        self.assertEqual(len(deposito['pagos']), 2)
        self.assertEqual(deposito['estado'], 'CONCILIADO')
        self.assertEqual(deposito['diferencia'], Decimal('0.00'))

    def test_el_abono_llega_dias_despues_del_payout(self):
        """
        Airbnb libera el pago y el banco lo abona después —en el CSV real la
        llegada estimada va cinco días más tarde—, así que exigir la misma
        fecha dejaría todo sin conciliar.
        """
        self._pago('HM1', Decimal('643.36'), date(2026, 3, 29))
        self._abono('643.36', date(2026, 4, 3))

        _, depositos = self._conciliar()

        self.assertEqual(depositos[0]['estado'], 'CONCILIADO')

    def test_un_abono_fuera_de_la_ventana_no_se_empareja(self):
        self._pago('HM1', Decimal('643.36'), date(2026, 3, 1))
        self._abono('643.36', date(2026, 3, 30))

        _, depositos = self._conciliar()

        self.assertEqual(depositos[0]['estado'], 'SIN_MOVIMIENTO')
        self.assertIsNone(depositos[0]['movimiento'])

    def test_la_referencia_manda_sobre_el_importe(self):
        """
        Si el banco conservó el id del payout no hay ambigüedad posible: ese
        es el depósito aunque el importe no coincida, y la diferencia es
        justamente lo que hay que revisar.
        """
        self._pago('HM1', Decimal('1000.00'), date(2026, 3, 12),
                   payout_id='0MS1RHz4c8515YKnnfW89NcM0tS')
        movimiento = self._abono('950.00', date(2026, 3, 15),
                                 referencia='0MS1RHz4c8515YKnnfW89NcM0tS')

        _, depositos = self._conciliar()

        self.assertEqual(depositos[0]['movimiento'], movimiento)
        self.assertEqual(depositos[0]['estado'], 'DIFERENCIA')
        self.assertEqual(depositos[0]['diferencia'], Decimal('-50.00'))

    def test_dos_depositos_del_mismo_importe_no_comparten_abono(self):
        """Dos payouts iguales son posibles; un abono solo cuadra con uno."""
        self._pago('HM1', Decimal('643.36'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        self._pago('HM2', Decimal('643.36'), date(2026, 3, 20),
                   payout_id='PAYOUT-2')
        self._abono('643.36', date(2026, 3, 14))

        _, depositos = self._conciliar()

        estados = sorted(d['estado'] for d in depositos)
        self.assertEqual(estados, ['CONCILIADO', 'SIN_MOVIMIENTO'])

    def test_no_adivina_cuando_dos_abonos_encajan_igual(self):
        """
        Dos habitaciones con la misma tarifa liquidadas la misma semana: el
        importe no distingue nada y la fecha tampoco. Asignar por proximidad
        daría algo que parece conciliado sin serlo, así que no se asigna.
        """
        self._pago('HM1', Decimal('643.36'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        self._abono('643.36', date(2026, 3, 14))
        self._abono('643.36', date(2026, 3, 16))

        _, depositos = self._conciliar()

        self.assertEqual(depositos[0]['estado'], 'AMBIGUO')
        self.assertIsNone(depositos[0]['movimiento'])
        self.assertEqual(len(depositos[0]['candidatos']), 2)

    def test_resolver_un_ambiguo_desambigua_al_otro(self):
        """
        Cada asignación inequívoca descarta ese abono, y eso puede volver
        inequívoco a un depósito que antes tenía dos candidatos: por eso el
        emparejamiento por importe se hace en varias pasadas.
        """
        # PAYOUT-2 solo alcanza al abono del 22 (el del 14 le queda fuera de
        # ventana), así que ese se asigna primero y libera al del 14.
        self._pago('HM1', Decimal('643.36'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        self._pago('HM2', Decimal('643.36'), date(2026, 3, 21),
                   payout_id='PAYOUT-2')
        self._abono('643.36', date(2026, 3, 14))
        self._abono('643.36', date(2026, 3, 22))

        _, depositos = self._conciliar()

        self.assertEqual([d['estado'] for d in depositos],
                         ['CONCILIADO', 'CONCILIADO'])
        self.assertNotEqual(depositos[0]['movimiento'],
                            depositos[1]['movimiento'])

    def test_lo_confirmado_a_mano_manda_sobre_el_automatico(self):
        from airbnb.services import ConciliacionDepositosService

        self._pago('HM1', Decimal('643.36'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        automatico = self._abono('643.36', date(2026, 3, 14))
        real = self._abono('643.36', date(2026, 3, 16))
        ConciliacionDepositosService.confirmar('PAYOUT-1', real)

        _, depositos = self._conciliar()

        self.assertEqual(depositos[0]['movimiento'], real)
        self.assertNotEqual(depositos[0]['movimiento'], automatico)
        self.assertTrue(depositos[0]['confirmado'])
        self.assertEqual(depositos[0]['estado'], 'CONCILIADO')

    def test_confirmar_no_deja_un_abono_en_dos_depositos(self):
        from airbnb.models import DepositoConciliado
        from airbnb.services import ConciliacionDepositosService

        movimiento = self._abono('643.36', date(2026, 3, 14))
        ConciliacionDepositosService.confirmar('PAYOUT-1', movimiento)
        ConciliacionDepositosService.confirmar('PAYOUT-2', movimiento)

        self.assertEqual(
            list(DepositoConciliado.objects.values_list('payout_id', flat=True)),
            ['PAYOUT-2'])

    def test_deshacer_devuelve_el_deposito_al_emparejamiento_automatico(self):
        from airbnb.services import ConciliacionDepositosService

        self._pago('HM1', Decimal('643.36'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        automatico = self._abono('643.36', date(2026, 3, 14))
        ConciliacionDepositosService.confirmar(
            'PAYOUT-1', self._abono('643.36', date(2026, 3, 16)))

        ConciliacionDepositosService.deshacer('PAYOUT-1')
        _, depositos = self._conciliar()

        self.assertFalse(depositos[0]['confirmado'])
        self.assertEqual(depositos[0]['estado'], 'AMBIGUO')
        self.assertIn(automatico, depositos[0]['candidatos'])

    def test_la_vista_confirma_el_deposito_ambiguo(self):
        from django.contrib.auth.models import User

        self._pago('HM1', Decimal('643.36'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        self._abono('643.36', date(2026, 3, 14))
        elegido = self._abono('643.36', date(2026, 3, 16))
        User.objects.create_superuser('staff_amb', 'amb@demo.mx', 'x' * 12)
        self.client.force_login(User.objects.get(username='staff_amb'))

        respuesta = self.client.post(
            '/admin/airbnb/conciliacion-depositos/?mes=3&anio=2026',
            {'payout_id': 'PAYOUT-1', 'movimiento_id': elegido.pk})

        self.assertEqual(respuesta.status_code, 302)
        _, depositos = self._conciliar()
        self.assertEqual(depositos[0]['movimiento'], elegido)
        self.assertTrue(depositos[0]['confirmado'])

    def test_los_pagos_sin_payout_se_agrupan_aparte(self):
        """No se pueden cuadrar contra el banco, pero tienen que verse."""
        self._pago('HM1', Decimal('500.00'), date(2026, 3, 12), payout_id='')

        _, depositos = self._conciliar()

        self.assertEqual(len(depositos), 1)
        self.assertEqual(depositos[0]['estado'], 'SIN_PAYOUT')
        self.assertEqual(depositos[0]['payout_id'], '')

    def test_los_cancelados_no_entran_en_el_deposito(self):
        self._pago('HM1', Decimal('600.00'), date(2026, 3, 12))
        self._pago('HM2', Decimal('400.00'), date(2026, 3, 12),
                   estado='REEMBOLSADO')

        _, depositos = self._conciliar()

        self.assertEqual(depositos[0]['total'], Decimal('600.00'))
        self.assertEqual(len(depositos[0]['pagos']), 1)

    def test_solo_toma_los_pagos_del_mes_consultado(self):
        self._pago('HM1', Decimal('600.00'), date(2026, 3, 12))
        self._pago('HM2', Decimal('400.00'), date(2026, 4, 12),
                   payout_id='PAYOUT-ABRIL')

        _, depositos = self._conciliar(mes=3, anio=2026)

        self.assertEqual(len(depositos), 1)
        self.assertEqual(depositos[0]['total'], Decimal('600.00'))

    def test_los_totales_resumen_lo_conciliado_y_lo_pendiente(self):
        self._pago('HM1', Decimal('600.00'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        self._pago('HM2', Decimal('400.00'), date(2026, 3, 20),
                   payout_id='PAYOUT-2')
        self._abono('600.00', date(2026, 3, 14))

        servicio, depositos = self._conciliar()
        totales = servicio.totales(depositos)

        self.assertEqual(totales['num_depositos'], 2)
        self.assertEqual(totales['num_conciliados'], 1)
        self.assertEqual(totales['esperado'], Decimal('1000.00'))
        self.assertEqual(totales['conciliado'], Decimal('600.00'))

    def test_los_tres_depositos_del_csv_real_cuadran_contra_el_banco(self):
        """
        Contra el archivo real de marzo: los tres payouts del CSV se
        emparejan con sus abonos sin intervención manual.
        """
        from pathlib import Path

        from airbnb.services import ImportadorCSVPagosService

        ruta = Path(__file__).parent / 'tests_fixtures' / 'airbnb_marzo_2026.csv'
        ImportadorCSVPagosService(archivo_nombre='airbnb_marzo_2026.csv').importar(
            ruta.read_text(encoding='utf-8'))

        # Las fechas de llegada estimada que trae el propio CSV.
        self._abono('15234.36', date(2026, 3, 19))
        self._abono('643.33', date(2026, 3, 27))
        self._abono('643.36', date(2026, 4, 3))

        servicio, depositos = self._conciliar()
        totales = servicio.totales(depositos)

        self.assertEqual(totales['num_depositos'], 3)
        self.assertEqual(totales['num_conciliados'], 3)
        self.assertEqual(totales['esperado'], Decimal('16521.05'))
        self.assertEqual(totales['diferencia'], Decimal('0.00'))

    def test_la_vista_del_admin_muestra_el_deposito(self):
        from django.contrib.auth.models import User

        self._pago('HM1', Decimal('600.00'), date(2026, 3, 12),
                   payout_id='PAYOUT-1')
        self._abono('600.00', date(2026, 3, 14))
        User.objects.create_superuser('staff_demo', 'staff@demo.mx', 'x' * 12)
        self.client.force_login(User.objects.get(username='staff_demo'))

        respuesta = self.client.get(
            '/admin/airbnb/conciliacion-depositos/?mes=3&anio=2026')

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, 'PAYOUT-1')

    def test_la_vista_no_revienta_con_parametros_basura(self):
        """Un `?mes=abc` en la URL devolvía 500 en el reporte fiscal viejo."""
        from django.contrib.auth.models import User

        User.objects.create_superuser('staff_demo2', 'staff2@demo.mx', 'x' * 12)
        self.client.force_login(User.objects.get(username='staff_demo2'))

        respuesta = self.client.get(
            '/admin/airbnb/conciliacion-depositos/?mes=abc&anio=13')

        self.assertEqual(respuesta.status_code, 200)

    def test_la_vista_exige_sesion_de_staff(self):
        respuesta = self.client.get('/admin/airbnb/conciliacion-depositos/')

        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('/admin/login/', respuesta['Location'])
