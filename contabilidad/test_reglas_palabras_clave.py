"""
Reglas sembradas por `0041`: cada categoría de gasto con palabra clave se asienta
sola en la cuenta de su operación; «Gas LP» le gana a «GAS» (vehículos).
"""
from .models import ConfiguracionContable, ReglaConciliacion
from .test_reglas_banco import ReglasBancoBase


class ReglasPalabrasClaveTest(ReglasBancoBase):

    def _regla_para(self, concepto):
        mov = self._mov(concepto, cargo='100.00')
        reglas = [r for r in ReglaConciliacion.objects.filter(activa=True) if r.coincide(mov)]
        return reglas[0] if reglas else None

    def test_sembradas_y_con_cuenta_configurada(self):
        reglas = ReglaConciliacion.objects.filter(origen='SISTEMA', nombre__startswith='Palabra clave:')
        self.assertGreaterEqual(reglas.count(), 20)
        for r in reglas.filter(operacion__in=['GASTO_LUZ', 'GASTO_AGUA', 'GASTO_GAS', 'COSTO_ALIMENTOS']):
            self.assertIsNotNone(ConfiguracionContable.obtener_cuenta(r.operacion), r.operacion)

    def test_concepto_cae_en_su_operacion(self):
        casos = {
            'CFE recibo octubre': 'GASTO_LUZ',
            'PIPA agua alberca': 'GASTO_AGUA',
            'HONORARIOS notario': 'GASTO_HONORARIOS',
            'RENTA EVENTO sillas': 'COSTO_RENTA_EVENTOS',
            'SEGUROS inmueble': 'GASTO_SEGUROS',
        }
        for concepto, operacion in casos.items():
            regla = self._regla_para(concepto)
            self.assertIsNotNone(regla, concepto)
            self.assertEqual(regla.operacion, operacion, concepto)

    def test_gas_lp_gana_a_gasolina(self):
        self.assertEqual(self._regla_para('GAS LP tanque').operacion, 'GASTO_GAS')
        self.assertEqual(self._regla_para('GAS gasolina camioneta').operacion, 'GASTO_VEHICULOS')

    def test_solo_cargos_y_palabra_completa(self):
        mov = self._mov('LUZ cliente deposito', abono='100.00')
        self.assertFalse(any(r.operacion == 'GASTO_LUZ' and r.coincide(mov) for r in ReglaConciliacion.objects.all()))
        self.assertIsNone(self._regla_para('PAGO INSURGENTES'))
