"""Tablero «Ventas», «Mis comisiones», seguimiento y lecturas del PR 5 (Issue #505).

Solo lectura: ninguna prueba espera que estos módulos cambien cómo se devenga ni cómo se paga."""
from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente
from comunicacion.models import Responsable, Visita
from core_erp.test_utils import login_superuser_con_totp
from nomina.models import Empleado
from ventas import services_comisiones as comisiones
from ventas import services_tablero as tablero
from ventas.models import CorteComision, MovimientoComision
from ventas.test_comisiones import CLAVE, D, pagar, vendedora, venta
from ventas.test_cortes import STORAGE, mov

URL_TABLERO = 'ventas:tablero'
URL_MIS = 'ventas:mis_comisiones'


def con_usuario(empleado, username):
    """Liga un usuario del grupo Vendedor al Empleado (el candado del acuerdo se cubre en nomina)."""
    usuario = User.objects.create_user(username, password=CLAVE, is_staff=True)
    grupo, _ = Group.objects.get_or_create(name='Vendedor')
    usuario.groups.add(grupo)
    Empleado.objects.filter(pk=empleado.pk).update(usuario=usuario)
    empleado.refresh_from_db()
    return usuario


def visita_atendida(vendedor, nombre='Visitante', dias=0, hora=11):
    inicio = (timezone.localtime() + timedelta(days=dias)).replace(hour=hora, minute=0, second=0, microsecond=0)
    v = Visita.objects.create(nombre=nombre, telefono='9990000000', inicio=inicio, estado='CONFIRMADA',
                              vendedor=vendedor, resultado='ATENDIDA')
    comisiones.devengar_visita(v)
    return v


class ResumenVendedorTest(TestCase):
    def setUp(self):
        self.ana = vendedora()
        self.hoy = timezone.localdate()
        self.desde = self.hoy.replace(day=1)

    def test_caso_armado_a_mano(self):
        # Contratación n.º 1: $350 + 5 % de $3,000 = $500.00; con el 50 % pagado lleva $250.00. Visita: $100.00.
        cot = venta(self.ana)
        pagar(cot, '9860.00')
        comisiones.sincronizar(cot)
        visita_atendida(self.ana)
        r = tablero.resumen_vendedor(self.ana, self.desde, self.hoy, self.hoy)
        self.assertEqual(r['contrataciones'], 1)
        self.assertEqual(r['visitas']['atendidas'], 1)
        self.assertEqual(r['visitas']['pagadas'], 1)
        self.assertEqual(r['visitas_devengado'], D('100.00'))
        self.assertEqual(r['comisiones_devengado'], D('250.00'))
        self.assertEqual(r['devengado'], D('350.00'))
        self.assertEqual(r['costo_de_ventas'], D('350.00'))
        self.assertEqual(r['venta_sin_iva'], D('17000.00'))
        self.assertEqual(r['por_pagar'], D('350.00'))
        fila = r['filas'][0]
        self.assertEqual((fila['numero'], fila['total'], fila['devengado'], fila['por_devengar']),
                         (1, D('500.00'), D('250.00'), D('250.00')))

    def test_escalon_cuenta_y_falta_para_el_siguiente(self):
        for _ in range(3):
            comisiones.asegurar_comision(venta(self.ana, extras=D('0.00')))
        e = tablero.resumen_vendedor(self.ana, self.desde, self.hoy, self.hoy)['escalon']
        self.assertEqual(e['contrataciones_anio'], 3)
        self.assertEqual(e['monto_siguiente'], D('350.00'))
        self.assertEqual(e['monto_proximo_escalon'], D('500.00'))
        self.assertEqual(e['faltan_para_escalon'], 10)

    def test_devolucion_total_no_cuenta_para_el_escalon(self):
        cot = venta(self.ana, extras=D('0.00'))
        comisiones.asegurar_comision(cot)
        pagar(cot, cot.precio_final)
        pagar(cot, cot.precio_final, tipo='REEMBOLSO')
        from comercial.models import Cotizacion
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CANCELADA')
        self.assertEqual(tablero.resumen_vendedor(self.ana, self.desde, self.hoy, self.hoy)['escalon']['contrataciones_anio'], 0)

    def test_sexta_visita_de_la_semana_cuenta_sobre_el_tope(self):
        lunes = comisiones.lunes_de(self.hoy)
        for i in range(6):
            v = Visita.objects.create(
                nombre=f'V{i}', telefono='9990000000', estado='CONFIRMADA', vendedor=self.ana, resultado='ATENDIDA',
                inicio=timezone.make_aware(timezone.datetime.combine(lunes, timezone.datetime.min.time())
                                           .replace(hour=10 + i)))
            comisiones.devengar_visita(v)
        r = tablero.resumen_vendedor(self.ana, lunes, lunes + timedelta(days=6), self.hoy)
        self.assertEqual((r['visitas']['atendidas'], r['visitas']['pagadas'], r['visitas']['sobre_tope']), (6, 5, 1))
        self.assertEqual(r['visitas_devengado'], D('500.00'))

    def test_pagado_solo_cuenta_cortes_pagados_en_el_periodo(self):
        mov(self.ana, '200.00', self.hoy)
        c = CorteComision.objects.create(vendedor=self.ana, tipo='COMISIONES', desde=self.desde, hasta=self.hoy,
                                         total=D('200.00'), estado='PAGADO', pagado_en=timezone.now())
        MovimientoComision.objects.filter(vendedor=self.ana).update(corte=c)
        r = tablero.resumen_vendedor(self.ana, self.desde, self.hoy, self.hoy)
        self.assertEqual(r['pagado'], D('200.00'))
        self.assertEqual(r['por_pagar'], D('0.00'))
        fuera = tablero.resumen_vendedor(self.ana, self.desde - timedelta(days=40), self.desde - timedelta(days=10), self.hoy)
        self.assertEqual(fuera['pagado'], D('0.00'))

    def test_absorcion_va_al_costo_y_no_al_devengado(self):
        mov(self.ana, '250.00', self.hoy)
        mov(self.ana, '250.00', self.hoy, tipo='AJUSTE', absorcion=True)
        r = tablero.resumen_vendedor(self.ana, self.desde, self.hoy, self.hoy)
        self.assertEqual(r['devengado'], D('250.00'))
        self.assertEqual(r['absorbido'], D('250.00'))
        self.assertEqual(r['costo_de_ventas'], D('500.00'))
        self.assertEqual(tablero.costo_de_ventas(self.desde, self.hoy), D('500.00'))

    def test_baja_deja_lo_no_devengado_visible(self):
        cot = venta(self.ana)
        comisiones.asegurar_comision(cot)
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=self.hoy)
        self.ana.refresh_from_db()
        r = tablero.resumen_vendedor(self.ana, self.desde, self.hoy, self.hoy)
        self.assertEqual(r['no_devengado_por_baja'], D('500.00'))

    def test_tablero_suma_personas_y_lista_efectivo(self):
        otra = vendedora('Bea', '9992223333')
        mov(self.ana, '100.00', self.hoy)
        mov(otra, '40.00', self.hoy)
        t = tablero.tablero(self.desde, self.hoy, self.hoy)
        self.assertEqual({p['vendedor'].nombre for p in t['personas']}, {'Ana', 'Bea'})
        self.assertEqual(t['totales']['devengado'], D('140.00'))

    def test_vacio_no_truena(self):
        r = tablero.resumen_vendedor(self.ana, self.desde, self.hoy, self.hoy)
        self.assertIsNone(r['tasa_cierre'])
        self.assertEqual(r['devengado'], D('0.00'))


class SeguimientoTest(TestCase):
    def test_linea_de_tiempo_ordenada_y_sin_comision_por_defecto(self):
        ana = vendedora()
        cot = venta(ana)
        pagar(cot, '9860.00')
        comisiones.sincronizar(cot)
        filas = tablero.linea_de_tiempo(cot)
        tipos = [f['tipo'] for f in filas]
        self.assertIn('Creada', tipos)
        self.assertIn('Pago', tipos)
        self.assertNotIn('Comisión', tipos)
        self.assertEqual([f['cuando'] for f in filas], sorted(f['cuando'] for f in filas))
        self.assertIn('Comisión', [f['tipo'] for f in tablero.linea_de_tiempo(cot, incluir_comision=True)])

    def test_panel_en_la_cotizacion_del_admin(self):
        ana = vendedora()
        cot = venta(ana)
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        r = self.client.get(reverse('admin:comercial_cotizacion_change', args=[cot.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Qué le ha pasado a la venta')


class PantallasTest(TestCase):
    def setUp(self):
        self.ana = vendedora('Ana', '9991234567')
        self.bea = vendedora('Bea', '9992223333')
        self.usuario_ana = con_usuario(self.ana, 'ana')
        self.usuario_bea = con_usuario(self.bea, 'bea')
        self.cot_ana = venta(self.ana, cliente=Cliente.objects.create(nombre='Cliente de Ana', telefono='9991110001'))
        self.cot_bea = venta(self.bea, cliente=Cliente.objects.create(nombre='Cliente de Bea', telefono='9991110002'))
        for cot in (self.cot_ana, self.cot_bea):
            comisiones.asegurar_comision(cot)

    def test_tablero_es_solo_de_direccion(self):
        self.client.force_login(self.usuario_ana)
        self.assertEqual(self.client.get(reverse(URL_TABLERO)).status_code, 403)
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        r = self.client.get(reverse(URL_TABLERO))
        self.assertContains(r, 'Ana')
        self.assertContains(r, 'Bea')

    def test_tablero_acepta_fechas_invalidas(self):
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        self.assertEqual(self.client.get(reverse(URL_TABLERO), {'desde': 'basura', 'hasta': '2026-99-99'}).status_code, 200)

    def test_mis_comisiones_solo_muestra_lo_suyo(self):
        self.client.force_login(self.usuario_ana)
        r = self.client.get(reverse(URL_MIS))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, f'COT-{self.cot_ana.pk:03d}')
        self.assertContains(r, 'Cliente de Ana')
        self.assertNotContains(r, 'Cliente de Bea')
        self.assertNotContains(r, f'COT-{self.cot_bea.pk:03d}')

    def test_el_anio_no_sale_con_separador_de_miles(self):
        anio = timezone.localdate().year
        self.client.force_login(self.usuario_ana)
        self.assertContains(self.client.get(reverse(URL_MIS)), f'Contrataciones del {anio}')
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        self.assertContains(self.client.get(reverse(URL_TABLERO)), f'Escalón del {anio}')

    def test_cambiar_parametros_no_muestra_a_otra_persona(self):
        self.client.force_login(self.usuario_ana)
        for extra in ({'vendedor': self.bea.pk}, {'empleado': self.bea.pk}, {'usuario': self.usuario_bea.pk},
                      {'vendedor_id': self.bea.pk, 'desde': '2020-01-01'}, {'pk': self.bea.pk}):
            r = self.client.get(reverse(URL_MIS), extra)
            self.assertEqual(r.status_code, 200)
            self.assertNotContains(r, 'Cliente de Bea')
            self.assertEqual(r.context['d']['vendedor'], self.ana)

    def test_no_expone_costos_ni_margenes(self):
        self.client.force_login(self.usuario_ana)
        r = self.client.get(reverse(URL_MIS))
        for texto in ('Costo de ventas', 'margen', 'Margen', 'Utilidad', 'costo directo'):
            self.assertNotContains(r, texto)
        for clave in ('costo_de_ventas', 'venta_sin_iva', 'absorbido', 'saldo_en_contra_otros'):
            self.assertNotIn(clave, r.context['d']['mes'])
            self.assertNotIn(clave, r.context['d']['anio'])

    def test_quien_no_es_vendedora_recibe_403(self):
        contador = User.objects.create_user('conta', password=CLAVE, is_staff=True)
        self.client.force_login(contador)
        self.assertEqual(self.client.get(reverse(URL_MIS)).status_code, 403)
        su = User.objects.create_superuser('dir', 'd@x.mx', CLAVE)
        login_superuser_con_totp(self.client, su)
        self.assertEqual(self.client.get(reverse(URL_MIS)).status_code, 403)

    def test_vendedora_dada_de_baja_pierde_la_pantalla(self):
        Empleado.objects.filter(pk=self.ana.pk).update(fecha_baja=timezone.localdate() - timedelta(days=1))
        self.client.force_login(self.usuario_ana)
        self.assertEqual(self.client.get(reverse(URL_MIS)).status_code, 403)

    def test_vendedora_no_abre_pantallas_de_direccion(self):
        self.client.force_login(self.usuario_ana)
        for nombre in ('admin:ventas_cortecomision_changelist', 'admin:ventas_comisioncontratacion_changelist',
                       'admin:ventas_movimientocomision_changelist', 'admin:ventas_entregaefectivo_changelist',
                       'reportes:comisiones'):
            self.assertIn(self.client.get(reverse(nombre)).status_code, (302, 403), nombre)

    @override_settings(STORAGES=STORAGE)
    def test_estado_de_cuenta_solo_el_propio(self):
        propio = CorteComision.objects.create(vendedor=self.ana, tipo='COMISIONES', desde=timezone.localdate(),
                                              hasta=timezone.localdate(), estado='PAGADO')
        ajeno = CorteComision.objects.create(vendedor=self.bea, tipo='COMISIONES', desde=timezone.localdate(),
                                             hasta=timezone.localdate(), estado='PAGADO')
        for c in (propio, ajeno):
            c.archivo_pdf.save('estado.pdf', ContentFile(b'%PDF-1.4 prueba'))
        self.client.force_login(self.usuario_ana)
        r = self.client.get(reverse('ventas:mis_comisiones_estado', args=[propio.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(self.client.get(reverse('ventas:mis_comisiones_estado', args=[ajeno.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('ventas:mis_comisiones_estado', args=[99999])).status_code, 404)

    @override_settings(STORAGES=STORAGE)
    def test_corte_cancelado_o_sin_pdf_no_se_descarga(self):
        sin = CorteComision.objects.create(vendedor=self.ana, tipo='VISITAS', desde=timezone.localdate(),
                                           hasta=timezone.localdate(), estado='PAGADO')
        self.client.force_login(self.usuario_ana)
        self.assertEqual(self.client.get(reverse('ventas:mis_comisiones_estado', args=[sin.pk])).status_code, 404)

    def test_grupo_vendedor_recibe_el_permiso_del_menu(self):
        call_command('crear_grupos_permisos')
        grupo = Group.objects.get(name='Vendedor')
        self.assertTrue(grupo.permissions.filter(codename='ver_mis_comisiones').exists())
        self.assertFalse(grupo.permissions.filter(content_type__app_label='ventas').exclude(
            codename='ver_mis_comisiones').exists())


class AvisosTest(TestCase):
    def test_linea_de_direccion_vacia_sin_actividad_y_con_cifras(self):
        from ventas.services_avisos import linea_resumen_direccion
        hoy = timezone.localdate()
        lunes_siguiente = comisiones.lunes_de(hoy) + timedelta(days=7)
        self.assertEqual(linea_resumen_direccion(lunes_siguiente), '')
        ana = vendedora()
        visita_atendida(ana, dias=0)
        linea = linea_resumen_direccion(lunes_siguiente)
        self.assertIn('1 visita(s) atendida(s)', linea)
        self.assertIn('$100.00', linea)

    def test_resumen_a_la_vendedora_solo_con_responsable(self):
        from unittest import mock

        from ventas.services_avisos import enviar_resumen_semanal_ventas
        ana = vendedora(telefono='9991234567')
        con_usuario(ana, 'ana')
        self.assertEqual(enviar_resumen_semanal_ventas(), 0)
        Responsable.objects.create(rol='VENTAS', nombre='Ana', telefono_wa='5219991234567', activo=True)
        with mock.patch('ventas.services_avisos._enviar_aviso') as enviar:
            enviar.return_value = mock.Mock(estado='ENVIADO')
            self.assertEqual(enviar_resumen_semanal_ventas(), 1)
        aviso = enviar.call_args.args[1]
        texto = aviso.texto()
        self.assertIn('Por cobrar', texto)
        self.assertNotIn('Costo', texto)
