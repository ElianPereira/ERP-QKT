"""Plan óptimo anual (Issue #410): el DP contra fuerza bruta, restricciones, ejemplo a mano y la página."""
import itertools
import random
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core_erp.test_utils import login_superuser_con_totp
from indicadores.models import ParametrosNegocio
from indicadores.services.base import HABITACIONES, dias_del_rango
from indicadores.services.plan_anual import PREPARACION, PlanAnualService, calcular_plan, optimizar

D = Decimal
VALORES = {'EVENTO': D('7931.03'), 'PASADIA': D('1424.14'), 'HOSPEDAJE': D('1484.14')}


def parametros(**extra) -> ParametrosNegocio:
    return ParametrosNegocio(**extra)


def valido(secuencia, dias, valores, p, bloqueados) -> bool:
    """¿Respeta el calendario esta asignación (`None`, un tipo o PREPARACION por día)? Verificación independiente del DP."""
    por_semana, pendientes = Counter(), 0
    for dia, tipo in zip(dias, secuencia):
        if pendientes:                                    # un día de preparación no admite otra cosa
            if tipo != PREPARACION:
                return False
            pendientes -= 1
            continue
        if tipo == PREPARACION:
            return False
        if tipo is None:
            continue
        if tipo not in valores or dia in bloqueados or dia.isoweekday() not in p.dias_de(tipo):
            return False
        if tipo == 'EVENTO':
            por_semana[dia.isocalendar()[:2]] += 1
            if por_semana[dia.isocalendar()[:2]] > p.max_eventos_semana:
                return False
            pendientes = p.dias_preparacion_evento
    return True


def fuerza_bruta(dias, valores, p, bloqueados) -> Decimal:
    """Mejor valor probando todas las asignaciones posibles (con la preparación truncada al fin del rango)."""
    mejor = D('0')
    for combinacion in itertools.product((None, 'EVENTO', 'PASADIA', 'HOSPEDAJE'), repeat=len(dias)):
        secuencia, restantes = [], 0
        for tipo in combinacion:                          # los días de preparación se rellenan solos
            if restantes:
                secuencia.append(PREPARACION)
                restantes -= 1
                continue
            secuencia.append(tipo)
            if tipo == 'EVENTO':
                restantes = p.dias_preparacion_evento
        if valido(secuencia, dias, valores, p, bloqueados):
            mejor = max(mejor, sum((valores[t] for t in secuencia if t in valores), D('0')))
    return mejor


def valor_de(asignacion, valores) -> Decimal:
    return sum((valores[t] for t in asignacion.values() if t != PREPARACION), D('0'))


class ProgramacionDinamicaTest(TestCase):

    def test_coincide_con_fuerza_bruta_con_y_sin_dias_de_preparacion(self):
        azar = random.Random(410)  # noqa: S311 — semilla fija para reproducir el caso, no es criptografía
        for caso in range(36):
            inicio = date(2027, 1, 1) + timedelta(days=azar.randrange(0, 300))
            dias = [inicio + timedelta(days=i) for i in range(7)]       # casi siempre cruza de semana ISO
            p = parametros(
                max_eventos_semana=azar.choice([1, 2, 3]), dias_preparacion_evento=caso % 3,
                dias_evento=''.join(sorted(azar.sample('1234567', azar.randrange(1, 6)))),
                dias_pasadia=''.join(sorted(azar.sample('1234567', azar.randrange(1, 8)))),
                dias_hospedaje=''.join(sorted(azar.sample('1234567', azar.randrange(1, 8)))))
            valores = {t: v + D(azar.randrange(0, 4000)) for t, v in VALORES.items()}
            bloqueados = {d for d in dias if azar.random() < 0.15}
            asignacion = optimizar(dias, valores, p, bloqueados)
            secuencia = [asignacion.get(d) for d in dias]
            self.assertTrue(valido(secuencia, dias, valores, p, bloqueados), (caso, secuencia))
            self.assertEqual(valor_de(asignacion, valores), fuerza_bruta(dias, valores, p, bloqueados),
                             (caso, p.dias_preparacion_evento, secuencia))

    def test_un_evento_con_preparacion_ocupa_sus_dias_consecutivos(self):
        dias = list(dias_del_rango(date(2027, 3, 1), date(2027, 3, 14)))
        p = parametros(max_eventos_semana=1, dias_preparacion_evento=2, dias_evento='1234567')
        asignacion = optimizar(dias, {'EVENTO': D('10000')}, p)
        eventos = [d for d, t in asignacion.items() if t == 'EVENTO']
        self.assertEqual(len(eventos), 2)                  # uno por semana ISO
        for dia in eventos:
            for i in (1, 2):
                if dia + timedelta(days=i) <= dias[-1]:
                    self.assertEqual(asignacion[dia + timedelta(days=i)], PREPARACION)

    def test_un_servicio_sin_margen_positivo_no_se_asigna(self):
        dias = list(dias_del_rango(date(2027, 3, 1), date(2027, 3, 7)))
        self.assertEqual(optimizar(dias, {'PASADIA': D('0'), 'HOSPEDAJE': D('-5')}, parametros()), {})


class RestriccionesDelAnioTest(TestCase):
    """El año completo: calendario respetado, bloqueos fuera y desglose mensual que suma el anual."""

    SERVICIOS = {'EVENTO': {'neto': D('12931.03'), 'costo': D('5000')},
                 'PASADIA': {'neto': D('1724.14'), 'costo': D('300')},
                 'HOSPEDAJE': {'neto': D('862.07'), 'costo': D('120')}}

    def plan(self, p=None, bloqueados=frozenset(), cf=D('600000')):
        return calcular_plan(2027, p or parametros(), self.SERVICIOS, cf, bloqueados=bloqueados)

    def test_eventos_en_dias_permitidos_y_nunca_mas_del_maximo_por_semana(self):
        p = parametros(max_eventos_semana=2, dias_evento='567', dias_preparacion_evento=1)
        plan = self.plan(p)
        eventos = [d for d, t in plan['asignacion'].items() if t == 'EVENTO']
        self.assertTrue(eventos)
        self.assertTrue(all(d.isoweekday() in {5, 6, 7} for d in eventos))
        self.assertLessEqual(max(Counter(d.isocalendar()[:2] for d in eventos).values()), 2)
        for dia in eventos:                                # la preparación sigue al evento
            siguiente = dia + timedelta(days=1)
            if siguiente.year == 2027:
                self.assertEqual(plan['asignacion'][siguiente], PREPARACION)

    def test_los_dias_bloqueados_se_excluyen(self):
        bloqueados = set(dias_del_rango(date(2027, 6, 1), date(2027, 6, 30)))
        plan = self.plan(bloqueados=bloqueados)
        self.assertFalse(bloqueados & set(plan['asignacion']))
        junio = plan['meses'][5]
        self.assertEqual((junio['eventos'], junio['pasadias'], junio['noches'], junio['dias_disponibles']), (0, 0, 0, 0))
        self.assertEqual(plan['dias_bloqueados'], 30)

    def test_los_bloqueos_del_calendario_se_leen_de_la_base(self):
        from comercial.models import BloqueoFecha

        BloqueoFecha.objects.create(fecha_inicio=date(2027, 2, 1), fecha_fin=date(2027, 2, 28))
        plan = calcular_plan(2027, parametros(), self.SERVICIOS, D('0'))
        self.assertEqual(plan['meses'][1]['dias_disponibles'], 0)
        self.assertEqual(plan['dias_bloqueados'], 28)

    def test_el_desglose_mensual_suma_el_total_anual(self):
        plan = self.plan(cf=D('100000.01'))
        meses = plan['meses']
        self.assertEqual(len(meses), 12)
        for clave in ('venta', 'contribucion', 'cf', 'utilidad'):
            self.assertEqual(sum(m[clave] for m in meses), {'venta': plan['venta'], 'contribucion': plan['contribucion'],
                                                            'cf': plan['cf'], 'utilidad': plan['utilidad']}[clave], clave)
        self.assertEqual(sum(m['eventos'] for m in meses), plan['unidades']['EVENTO'])
        self.assertEqual(sum(m['noches'] for m in meses), plan['unidades']['HOSPEDAJE'])
        self.assertEqual(sum(m['dias_ocupados'] for m in meses), plan['dias_ocupados'])

    def test_sin_precios_el_plan_esta_vacio_y_no_revienta(self):
        plan = calcular_plan(2027, parametros(), {}, D('1200'), bloqueados=frozenset())
        self.assertTrue(plan['vacio'])
        self.assertEqual(plan['utilidad'], D('-1200.00'))
        self.assertIn('no hay servicios con precio', plan['frase'])
        self.assertIsNone(plan['equilibrio']['dias'])


class EjemploFijoTest(TestCase):
    """
    2027 calculado a mano: evento $15,000 / pasadía $2,000 / hospedaje $1,000 (con IVA), costos variables
    $5,000 / $300 / $120 (sin IVA) y costos fijos $600,000. Días de venta y tope por defecto.

    Netos: 12,931.03 / 1,724.14 / 862.07. MC: evento 7,931.03; pasadía 1,424.14; noche 742.07, o sea 1,484.14
    por día con las dos habitaciones, que le gana a la pasadía. El 1 de enero de 2027 es viernes y cae en la
    semana ISO 53 de 2026, así que esa semana (vie-dom) y cada una de las 51 completas dan 2 eventos, y el
    viernes 31 de diciembre otro: 2 + 51 × 2 + 1 = 105. Los otros 260 días del año son hospedaje: 520
    noches-habitación y ninguna pasadía.
    """

    def setUp(self):
        servicios = {'EVENTO': {'neto': D('12931.03'), 'costo': D('5000')},
                     'PASADIA': {'neto': D('1724.14'), 'costo': D('300')},
                     'HOSPEDAJE': {'neto': D('862.07'), 'costo': D('120')}}
        self.plan = calcular_plan(2027, parametros(), servicios, D('600000'), bloqueados=frozenset(), con_umbrales=True)

    def test_la_combinacion_y_las_cifras_del_ano(self):
        plan = self.plan
        self.assertEqual(plan['unidades'], {'EVENTO': 105, 'PASADIA': 0, 'HOSPEDAJE': 520})
        self.assertEqual(plan['venta'], D('1806034.55'))               # 105 × 12,931.03 + 520 × 862.07
        self.assertEqual(plan['costo_directo'], D('587400.00'))        # 105 × 5,000 + 520 × 120
        self.assertEqual(plan['contribucion'], D('1218634.55'))
        self.assertEqual(plan['utilidad'], D('618634.55'))
        self.assertEqual((plan['dias_ocupados'], plan['dias_disponibles']), (365, 365))
        self.assertEqual(plan['frase'], 'En 2027 lo que más te conviene es: 105 eventos (vie-dom), 0 pasadías '
                                        'y 520 noches-habitación entre semana')

    def test_el_costo_fijo_se_reparte_entre_doce_meses(self):
        self.assertEqual({m['cf'] for m in self.plan['meses']}, {D('50000.00')})
        enero = self.plan['meses'][0]
        self.assertEqual(enero['eventos'], 10)                         # 1-3 ene (2) y las semanas del 4, 11, 18 y 25
        self.assertEqual(enero['noches'], 2 * (31 - 10))

    def test_si_no_lleno_todo(self):
        esc = {e['pct']: e for e in self.plan['escenarios']}
        self.assertEqual(esc[100]['utilidad'], D('618634.55'))
        self.assertEqual(esc[25]['contribucion'], D('304658.64'))
        self.assertEqual(esc[25]['utilidad'], D('-295341.36'))
        self.assertEqual(esc[50]['utilidad'], D('9317.28'))                # 609,317.28 − 600,000
        eq = self.plan['equilibrio']
        self.assertEqual((eq['dias'], eq['pct_plan'], eq['alcanza']), (180, D('49.24'), True))

    def test_precio_para_entrar_de_la_pasadia(self):
        pasadia = self.plan['servicios']['PASADIA']
        self.assertFalse(pasadia['entra'])
        # Tiene que dejar más que los 1,484.14 que deja un día de hospedaje: neto > 1,784.14 → $2,070 con IVA
        self.assertEqual(pasadia['umbral'], {'precio': D('2070'), 'mc_dia': D('1484.48')})

    def test_el_precio_para_entrar_hace_que_el_servicio_efectivamente_entre(self):
        umbral = self.plan['servicios']['PASADIA']['umbral']['precio']
        for precio, debe_entrar in ((umbral, True), (umbral - 1, False)):
            from core_erp.impuestos import sin_iva
            servicios = {t: {'neto': s['neto'], 'costo': s['costo']} for t, s in self.plan['servicios'].items()}
            servicios['PASADIA']['neto'] = sin_iva(precio)
            plan = calcular_plan(2027, parametros(), servicios, D('600000'), bloqueados=frozenset())
            self.assertEqual(plan['servicios']['PASADIA']['entra'], debe_entrar, precio)

    def test_un_servicio_que_ya_entra_no_lleva_umbral(self):
        self.assertIsNone(self.plan['servicios']['HOSPEDAJE']['umbral'])
        self.assertTrue(self.plan['servicios']['EVENTO']['entra'])

    def test_sin_dias_libres_el_servicio_no_puede_entrar_a_ningun_precio(self):
        p = parametros(dias_pasadia='')
        plan = calcular_plan(2027, p, {'PASADIA': {'neto': D('1724.14'), 'costo': D('300')}}, D('0'),
                             bloqueados=frozenset(), con_umbrales=True)
        self.assertIsNone(plan['servicios']['PASADIA']['umbral'])


class PaginaPlanTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.direccion = User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123')
        cls.p = ParametrosNegocio.objects.create(
            precio_venta_evento=D('15000'), costo_directo_evento=D('5000'), precio_venta_pasadia=D('2000'),
            costo_directo_pasadia=D('300'), precio_venta_hospedaje=D('1000'), costo_directo_hospedaje=D('120'),
            costo_fijo_mensual_override=D('50000'))

    def setUp(self):
        self.url = reverse('indicadores:plan')
        login_superuser_con_totp(self.client, self.direccion)

    def datos(self, accion, **extra):
        datos = {'anio': '2027', 'cf_anual': '600000.00', 'precio_EVENTO': '15000.00', 'costo_EVENTO': '5000.00',
                 'precio_PASADIA': '2000.00', 'costo_PASADIA': '300.00', 'precio_HOSPEDAJE': '1000.00',
                 'costo_HOSPEDAJE': '120.00', 'accion': accion}
        datos.update(extra)
        return datos

    def test_abre_ya_llena_y_con_la_respuesta(self):
        r = self.client.get(self.url, {'anio': 2027})
        self.assertEqual(r.status_code, 200)
        form = r.context['form']
        self.assertEqual((form['anio'].value(), form['precio_EVENTO'].value(), form['costo_PASADIA'].value()),
                         (2027, D('15000.00'), D('300.00')))
        self.assertEqual(form['cf_anual'].value(), D('600000.00'))      # 50,000 × 12
        self.assertContains(r, 'En 2027 lo que más te conviene es: 105 eventos (vie-dom), 0 pasadías')
        for seccion in ('Tus números', 'Qué vender', 'Mes por mes', 'Ajustes avanzados'):
            self.assertContains(r, seccion)
        self.assertContains(r, 'Entra desde $2,070.00')
        self.assertContains(r, 'Eventos solo vie, sáb y dom, máximo 2 por semana; 2 habitaciones; un servicio por día')
        self.assertContains(r, '$618,634.55')

    def test_por_defecto_el_ano_es_el_siguiente(self):
        from django.utils import timezone
        r = self.client.get(self.url)
        self.assertEqual(r.context['form']['anio'].value(), timezone.localdate().year + 1)

    def test_calcular_no_guarda_nada(self):
        r = self.client.post(self.url, self.datos('calcular', precio_PASADIA='3000.00'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['plan']['servicios']['PASADIA']['precio'], D('3000.00'))
        self.assertEqual(ParametrosNegocio.objects.count(), 1)
        self.assertEqual(ParametrosNegocio.objects.get().precio_venta_pasadia, D('2000.00'))

    def test_guardar_crea_un_parametro_activo_y_desactiva_el_anterior(self):
        r = self.client.post(self.url, self.datos('guardar', precio_PASADIA='3000.00', cf_anual='1200000'))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(ParametrosNegocio.objects.count(), 2)
        nuevo = ParametrosNegocio.vigente()
        self.assertNotEqual(nuevo.pk, self.p.pk)
        self.assertEqual((nuevo.precio_venta_pasadia, nuevo.costo_directo_hospedaje), (D('3000.00'), D('120.00')))
        self.assertEqual(nuevo.costo_fijo_mensual_override, D('100000.00'))
        self.assertEqual((nuevo.created_by, nuevo.activo), (self.direccion, True))
        self.p.refresh_from_db()
        self.assertFalse(self.p.activo)                                  # se conserva como histórico
        self.assertEqual(nuevo.max_eventos_semana, self.p.max_eventos_semana)     # lo demás se hereda

    def test_guardar_con_un_costo_que_rebasa_el_precio_no_crea_nada(self):
        r = self.client.post(self.url, self.datos('guardar', costo_PASADIA='1800'), follow=True)
        self.assertEqual(ParametrosNegocio.objects.count(), 1)
        self.assertContains(r, 'El costo directo no puede ser igual o mayor al precio sin IVA')

    def test_un_servicio_sin_precio_no_entra_al_calculo(self):
        r = self.client.post(self.url, self.datos('calcular', precio_PASADIA='', costo_PASADIA=''))
        self.assertNotIn('PASADIA', r.context['plan']['servicios'])
        self.assertContains(r, 'Sin precio')

    def test_datos_invalidos_se_explican_sin_calcular(self):
        r = self.client.post(self.url, self.datos('calcular', precio_EVENTO='-1', anio='1999'))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.context['plan'])
        self.assertTrue(r.context['form'].errors)

    def test_solo_direccion(self):
        self.client.logout()
        self.client.force_login(User.objects.create_user('s', 's@x.mx', 'clave-de-prueba-123', is_staff=True))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.datos('guardar')).status_code, 403)
        self.assertEqual(ParametrosNegocio.objects.count(), 1)

    def test_el_menu_tiene_este_mes_y_plan_del_ano(self):
        r = self.client.get(reverse('admin:index'))
        self.assertContains(r, self.url)
        self.assertContains(r, 'Plan del año')
        self.assertContains(r, reverse('indicadores:tablero'))
        self.assertNotContains(r, 'indicadores/parametrosnegocio/"')


class ValoresInicialesTest(TestCase):

    def test_sin_parametros_ni_historico_no_hay_servicios_y_avisa(self):
        iniciales = PlanAnualService.valores_iniciales(ParametrosNegocio.vigente(), date(2026, 10, 17))
        self.assertEqual(iniciales['servicios'], {})
        self.assertTrue(iniciales['avisos'])

    def test_costos_fijos_del_ano_son_el_mensual_por_doce(self):
        p = ParametrosNegocio.objects.create(costo_fijo_mensual_override=D('33333.33'))
        iniciales = PlanAnualService.valores_iniciales(p, date(2026, 10, 17))
        self.assertEqual(iniciales['cf_anual'], D('399999.96'))
        self.assertEqual(iniciales['fuente_cf'], 'presupuestado')

    def test_el_hospedaje_vale_por_dos_habitaciones(self):
        self.assertEqual(HABITACIONES, 2)
