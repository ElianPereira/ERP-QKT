"""Tests del módulo único de impuestos (core_erp.impuestos)."""

from decimal import Decimal

from django.test import TestCase

from core_erp import impuestos as imp


class PrimitivasTest(TestCase):

    def test_con_iva_caso_del_brief(self):
        self.assertEqual(imp.con_iva(Decimal('100.05')), Decimal('116.06'))

    def test_con_iva_cero(self):
        self.assertEqual(imp.con_iva(Decimal('0.00')), Decimal('0.00'))

    def test_rechaza_float(self):
        with self.assertRaises(TypeError):
            imp.con_iva(100.05)
        with self.assertRaises(TypeError):
            imp.sin_iva(116.06)
        with self.assertRaises(TypeError):
            imp.centavos(1.5)

    def test_acepta_int(self):
        self.assertEqual(imp.con_iva(100), Decimal('116.00'))

    def test_round_half_up_no_bankers(self):
        """Un empate exacto en .005 redondea hacia arriba, no al par."""
        self.assertEqual(imp.centavos(Decimal('0.005')), Decimal('0.01'))
        self.assertEqual(imp.centavos(Decimal('0.015')), Decimal('0.02'))
        self.assertEqual(imp.centavos(Decimal('0.025')), Decimal('0.03'))
        # ROUND_HALF_EVEN daría 0.02 y 0.02 en el primero y el tercero.

    def test_reversibilidad(self):
        """sin_iva(con_iva(x)) == x en un barrido amplio."""
        malos = []
        for centavos_ in range(1, 100001):
            x = Decimal(centavos_) / 100
            if imp.sin_iva(imp.con_iva(x)) != x:
                malos.append(x)
                if len(malos) > 5:
                    break
        self.assertEqual(malos, [], f"sin_iva(con_iva(x)) != x para {malos}")


class AgregacionTest(TestCase):
    """I4: convertir una sola vez sobre el agregado, nunca por línea."""

    def test_tres_lineas_de_100_05_no_derivan(self):
        bases = [Decimal('100.05')] * 3
        por_linea = sum(imp.con_iva(b) for b in bases)      # 348.18
        agregado = imp.total_desde_bases(bases)             # 348.17
        self.assertEqual(agregado, Decimal('348.17'))
        self.assertEqual(por_linea - agregado, Decimal('0.01'))

    def test_siete_lineas_con_centavos_dispares(self):
        bases = [Decimal(v) for v in
                 ('10.01', '20.03', '30.07', '40.01', '50.03', '60.07', '70.01')]
        suma = sum(bases)
        self.assertEqual(imp.total_desde_bases(bases), imp.con_iva(suma))

    def test_lista_vacia(self):
        self.assertEqual(imp.total_desde_bases([]), Decimal('0.00'))


class DesgloseTest(TestCase):
    """4.2: ambas invariantes a la vez, o excepción."""

    def _verificar(self, total, moral=False):
        d = imp.desglosar(total, con_retencion_isr=moral)
        self.assertEqual(d['base'] + d['iva'] - d['ret_isr'], total,
                         f"no cuadra el total para {total}: {d}")
        desviacion = abs(d['iva'] - imp.iva_de(d['base']))
        self.assertLessEqual(desviacion, Decimal('0.01'),
                             f"IVA fuera de la tolerancia del SAT para {total}: {d}")
        return d

    def test_barrido_persona_fisica(self):
        for c in range(1, 5001):
            self._verificar(Decimal(c) / 100)

    def test_barrido_persona_moral(self):
        for c in range(1, 5001):
            self._verificar(Decimal(c) / 100, moral=True)

    def test_montos_grandes(self):
        for v in ('1000.00', '13461.80', '99999.99', '150000.33'):
            self._verificar(Decimal(v))
            self._verificar(Decimal(v), moral=True)

    def test_pago_de_un_centavo(self):
        d = self._verificar(Decimal('0.01'))
        self.assertEqual(d['base'], Decimal('0.01'))
        self.assertEqual(d['iva'], Decimal('0.00'))

    def test_total_cero(self):
        d = imp.desglosar(Decimal('0.00'))
        self.assertEqual(d['base'], Decimal('0.00'))
        self.assertEqual(d['iva'], Decimal('0.00'))
        self.assertEqual(d['ret_isr'], Decimal('0.00'))

    def test_retencion_solo_para_moral(self):
        self.assertEqual(imp.desglosar(Decimal('1000.00'))['ret_isr'], Decimal('0.00'))
        self.assertGreater(imp.desglosar(Decimal('1000.00'), con_retencion_isr=True)['ret_isr'],
                           Decimal('0.00'))

    def test_desglose_rechaza_float(self):
        with self.assertRaises(TypeError):
            imp.desglosar(1000.0)
