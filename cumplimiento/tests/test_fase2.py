from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core_erp.test_utils import login_superuser_con_totp
from cumplimiento import services
from cumplimiento.models import DocumentoExpediente, RequisitoTramite, Tramite, Vigencia

HOY = date(2026, 10, 16)


class AgregarMesesTest(SimpleTestCase):
    def test_fin_de_mes(self):
        self.assertEqual(services.agregar_meses(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(services.agregar_meses(date(2027, 1, 31), 1), date(2027, 2, 28))

    def test_29_de_febrero(self):
        self.assertEqual(services.agregar_meses(date(2028, 2, 29), 12), date(2029, 2, 28))
        self.assertEqual(services.agregar_meses(date(2028, 2, 29), 48), date(2032, 2, 29))

    def test_cambio_de_anio(self):
        self.assertEqual(services.agregar_meses(date(2026, 12, 15), 2), date(2027, 2, 15))


def _vigencia(tramite, estado='OBTENIDA', vence=None, **kw):
    return Vigencia.objects.create(tramite=tramite, estado=estado, fecha_vencimiento=vence, **kw)


class SemaforoTest(TestCase):
    def setUp(self):
        self.t = Tramite.objects.create(nombre='Licencia', periodicidad_meses=12, dias_aviso=60)

    def estado(self):
        t = Tramite.objects.prefetch_related('vigencias').get(pk=self.t.pk)
        return services.estado_tramite(t, HOY)

    def test_sin_registro(self):
        self.assertEqual(self.estado().estado, services.SIN_REGISTRO)

    def test_canceladas_no_cuentan(self):
        _vigencia(self.t, 'CANCELADA', date(2027, 1, 1))
        self.assertEqual(self.estado().estado, services.SIN_REGISTRO)

    def test_en_tramite_sin_obtenida(self):
        _vigencia(self.t, 'EN_TRAMITE')
        self.assertEqual(self.estado().estado, services.EN_TRAMITE)

    def test_vigente(self):
        _vigencia(self.t, vence=date(2027, 1, 15))
        self.assertEqual(self.estado().estado, services.VIGENTE)

    def test_por_vencer_en_el_limite(self):
        _vigencia(self.t, vence=date(2026, 12, 15))  # 60 días
        self.assertEqual(self.estado().estado, services.POR_VENCER)

    def test_vigente_un_dia_despues_del_limite(self):
        _vigencia(self.t, vence=date(2026, 12, 16))  # 61 días
        self.assertEqual(self.estado().estado, services.VIGENTE)

    def test_vence_hoy_es_por_vencer(self):
        _vigencia(self.t, vence=HOY)
        est = self.estado()
        self.assertEqual((est.estado, est.dias), (services.POR_VENCER, 0))

    def test_vencido(self):
        _vigencia(self.t, vence=date(2026, 10, 15))
        self.assertEqual(self.estado().estado, services.VENCIDO)

    def test_vencido_con_renovacion_es_en_tramite(self):
        _vigencia(self.t, vence=date(2026, 10, 1))
        _vigencia(self.t, 'EN_TRAMITE')
        self.assertEqual(self.estado().estado, services.EN_TRAMITE)

    def test_vigente_con_renovacion_conserva_estado_e_indicador(self):
        _vigencia(self.t, vence=date(2027, 6, 1))
        renovacion = _vigencia(self.t, 'EN_TRAMITE')
        est = self.estado()
        self.assertEqual(est.estado, services.VIGENTE)
        self.assertEqual(est.renovacion_en_curso.pk, renovacion.pk)

    def test_sin_vencimiento_es_vigente(self):
        _vigencia(self.t, vence=None)
        self.assertEqual(self.estado().estado, services.VIGENTE)

    def test_vigencia_actual_es_la_de_vencimiento_mas_lejano(self):
        _vigencia(self.t, vence=date(2025, 1, 1))
        nueva = _vigencia(self.t, vence=date(2027, 1, 1))
        self.assertEqual(self.estado().vigencia.pk, nueva.pk)


class RenovacionTest(TestCase):
    def setUp(self):
        self.t = Tramite.objects.create(nombre='Licencia', periodicidad_meses=12)

    def test_datos_prellenados(self):
        _vigencia(self.t, vence=date(2026, 12, 31), costo=Decimal('1234.50'))
        datos = services.datos_renovacion(Tramite.objects.prefetch_related('vigencias').get(pk=self.t.pk))
        self.assertEqual(datos['fecha_inicio'], '2027-01-01')
        self.assertEqual(datos['fecha_vencimiento'], '2027-12-31')
        self.assertEqual(datos['costo'], '1234.50')
        self.assertEqual(datos['estado'], 'EN_TRAMITE')

    def test_avance_requisitos(self):
        r1 = RequisitoTramite.objects.create(tramite=self.t, nombre='A')
        RequisitoTramite.objects.create(tramite=self.t, nombre='B')
        RequisitoTramite.objects.create(tramite=self.t, nombre='C', obligatorio=False)
        v = _vigencia(self.t, 'EN_TRAMITE')
        DocumentoExpediente.objects.create(vigencia=v, requisito=r1, nombre='a.pdf', archivo='x')
        cargados, total, faltantes = services.avance_requisitos(
            Vigencia.objects.prefetch_related('documentos', 'tramite__requisitos').get(pk=v.pk))
        self.assertEqual((cargados, total, [r.nombre for r in faltantes]), (1, 2, ['B']))


class CostoAnualYCatalogoTest(TestCase):
    def test_costo_anual_decimal_exacto(self):
        t = Tramite.objects.create(nombre='X')
        for costo, inicio, estado in (('100.10', date(2026, 2, 1), 'OBTENIDA'), ('200.20', date(2026, 9, 1), 'EN_TRAMITE'),
                                      ('999.00', date(2026, 3, 1), 'CANCELADA'), ('50.00', date(2025, 5, 1), 'OBTENIDA')):
            Vigencia.objects.create(tramite=t, estado=estado, fecha_inicio=inicio, costo=Decimal(costo))
        self.assertEqual(services.costo_anual(2026), Decimal('300.30'))

    def test_catalogo_sugerido_idempotente(self):
        self.assertEqual(services.cargar_catalogo_sugerido(), 4)
        self.assertEqual(services.cargar_catalogo_sugerido(), 0)
        self.assertEqual(Tramite.objects.count(), 4)
        self.assertTrue(RequisitoTramite.objects.filter(tramite__nombre='Licencia de funcionamiento').exists())


class TableroYAdminTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)

    def test_tablero_vacio_ofrece_catalogo_y_lo_carga(self):
        r = self.client.get(reverse('cumplimiento:tablero'))
        self.assertContains(r, 'Cargar catálogo sugerido')
        r = self.client.post(reverse('cumplimiento:cargar_catalogo'))
        self.assertRedirects(r, reverse('cumplimiento:tablero'))
        self.assertEqual(Tramite.objects.count(), 4)

    def test_catalogo_solo_superusuario(self):
        staff = User.objects.create_user('ayuda', password='x-segura-123', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.post(reverse('cumplimiento:cargar_catalogo')).status_code, 403)
        self.assertEqual(Tramite.objects.count(), 0)

    def test_tablero_requiere_permiso(self):
        staff = User.objects.create_user('ayuda', password='x-segura-123', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse('cumplimiento:tablero')).status_code, 403)

    def test_tablero_con_estados_y_riesgo_de_clausura(self):
        hoy = timezone.localdate()
        casos = {'Vencida crítica': (-5, True), 'Por vencer': (10, False), 'Vigente': (200, False)}
        for nombre, (dias, critico) in casos.items():
            t = Tramite.objects.create(nombre=nombre, periodicidad_meses=12, critico=critico)
            Vigencia.objects.create(tramite=t, estado='OBTENIDA', fecha_vencimiento=hoy + timedelta(days=dias),
                                    fecha_inicio=hoy, costo=Decimal('100.00'))
        Tramite.objects.create(nombre='Sin registro')
        r = self.client.get(reverse('cumplimiento:tablero'))
        self.assertContains(r, 'Riesgo de clausura')
        self.assertEqual(r.context['conteo'], {'VENCIDO': 1, 'POR_VENCER': 1, 'EN_TRAMITE': 0,
                                               'SIN_REGISTRO': 1, 'VIGENTE': 1})
        self.assertEqual(r.context['costo_anual'], Decimal('300.00'))
        self.assertEqual([f['tramite'].nombre for f in r.context['proximos_90']], ['Por vencer'])

    def test_lista_de_tramites_carga_con_semaforo(self):
        t = Tramite.objects.create(nombre='Lic', periodicidad_meses=12)
        Vigencia.objects.create(tramite=t, estado='OBTENIDA', fecha_vencimiento=date(2020, 1, 1))
        r = self.client.get(reverse('admin:cumplimiento_tramite_changelist'))
        self.assertContains(r, 'Vencido')

    def test_renovar_abre_alta_prellenada(self):
        t = Tramite.objects.create(nombre='Lic', periodicidad_meses=12)
        Vigencia.objects.create(tramite=t, estado='OBTENIDA', fecha_vencimiento=date(2026, 12, 31), costo=Decimal('10.00'))
        r = self.client.get(reverse('admin:cumplimiento_tramite_renovar', args=[t.pk]))
        destino = urlparse(r['Location'])
        self.assertEqual(destino.path, reverse('admin:cumplimiento_vigencia_add'))
        q = parse_qs(destino.query)
        self.assertEqual(q['fecha_vencimiento'], ['2027-12-31'])
        self.assertEqual(Vigencia.objects.count(), 1)  # no crea nada hasta guardar

    def test_renovar_con_una_en_tramite_abre_esa(self):
        t = Tramite.objects.create(nombre='Lic', periodicidad_meses=12)
        v = Vigencia.objects.create(tramite=t, estado='EN_TRAMITE')
        r = self.client.get(reverse('admin:cumplimiento_tramite_renovar', args=[t.pk]))
        self.assertEqual(r['Location'], reverse('admin:cumplimiento_vigencia_change', args=[v.pk]))

    def test_formulario_muestra_requisitos_faltantes(self):
        t = Tramite.objects.create(nombre='Lic', periodicidad_meses=12)
        RequisitoTramite.objects.create(tramite=t, nombre='Dictamen eléctrico')
        v = Vigencia.objects.create(tramite=t, estado='EN_TRAMITE')
        r = self.client.get(reverse('admin:cumplimiento_vigencia_change', args=[v.pk]))
        self.assertContains(r, 'Dictamen eléctrico')
