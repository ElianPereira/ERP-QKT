"""Ciclo de venta medido con el histórico (percentil 80, muestra mínima, respaldo manual)."""
from datetime import timedelta

from comercial.models import Cotizacion, Pago
from marketing.models import ParametroMarketing
from marketing.services.ciclo import ciclo_historico

from .test_kpis import HOY, Base, aware


class CicloHistoricoTest(Base):
    def venta(self, dias, tipo='EVENTO', estado='CONFIRMADA', prospecto_antes=None, metodo='TRANSFERENCIA',
              historica=False):
        """Venta creada en HOY − 200 días y pagada `dias` después."""
        cot = self.cotizacion('1000', tipo=tipo, estado=estado)
        creada = HOY - timedelta(days=200)
        Cotizacion.objects.filter(pk=cot.pk).update(created_at=aware(creada), es_historica=historica)
        Pago.objects.bulk_create([Pago(cotizacion=cot, monto=1, metodo=metodo,
                                       fecha_pago=creada + timedelta(days=dias))])
        if prospecto_antes is not None:
            self.lead(creada - timedelta(days=prospecto_antes), cot=cot)
        return cot

    def test_percentil_80_y_no_promedio(self):
        for d in (10, 20, 30, 40, 200):
            self.venta(d)
        self.assertEqual(ciclo_historico('EVENTO'), {'dias': 40, 'muestra': 5})

    def test_muestra_insuficiente(self):
        for d in (10, 20, 30, 40):
            self.venta(d)
        self.assertEqual(ciclo_historico('EVENTO'), {'dias': None, 'muestra': 4})

    def test_cuenta_desde_el_prospecto_si_es_anterior(self):
        for _ in range(5):
            self.venta(10, prospecto_antes=20)
        self.assertEqual(ciclo_historico('EVENTO')['dias'], 30)

    def test_excluye_lo_que_no_es_venta_real(self):
        for d in (10, 10, 10, 10):
            self.venta(d)
        self.venta(10, estado='COTIZADA')
        self.venta(10, estado='CANCELADA')
        self.venta(10, historica=True)
        self.venta(10, metodo='CONDONACION')
        self.venta(10, tipo='PASADIA')
        self.assertEqual(ciclo_historico('EVENTO')['muestra'], 4)

    def test_ciclo_de_usa_el_historico_o_el_manual(self):
        for d in (10, 20, 30, 40, 50):
            self.venta(d)
        p = ParametroMarketing(ciclo_dias_evento=120, ciclo_dias_pasadia=30)
        self.assertEqual(p.ciclo_de('EVENTO'), 40)
        self.assertEqual(p.ciclo_de('PASADIA'), 30)       # sin ventas de pasadía: manual
        p = ParametroMarketing(ciclo_dias_evento=120, ciclo_automatico=False)
        self.assertEqual(p.ciclo_de('EVENTO'), 120)


class SupuestosAdminCicloTest(Base):
    def test_la_pantalla_muestra_el_ciclo_calculado(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse

        from core_erp.test_utils import login_superuser_con_totp

        for d in (10, 20, 30, 40, 50):
            CicloHistoricoTest.venta(self, d)
        su = get_user_model().objects.create_superuser('dir', 'd@x.com', 'x')
        login_superuser_con_totp(self.client, su)
        r = self.client.get(reverse('admin:marketing_parametromarketing_change', args=[self.p.pk]))
        self.assertContains(r, 'Evento: 40 días (80 % de 5 ventas)')
        self.assertContains(r, 'Pasadía: muestra insuficiente (0 de 5 ventas)')
