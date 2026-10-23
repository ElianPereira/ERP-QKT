"""Fase 4 (Issue #431): resumen semanal y «Detecté» para Dirección, alerta de
desviación, aprobación de nómina y CFDI emitido ambiguo con el contador."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion, ItemCotizacion, Pago, Producto
from comunicacion import services_agente, services_contador, services_direccion, services_pendientes
from comunicacion.models import ComunicacionCliente, ConsultaCfdi, Hallazgo, Pendiente, Responsable
from contabilidad.models import CfdiSAT, Poliza
from facturacion.models import SolicitudFactura
from indicadores.models import MetaMensual
from nomina.models import AprobacionNomina, Empleado, ReciboNomina

TEL_DIR = '525555550021'
TEL_CON = '525555550022'
TEL_OTRO = '525555550023'
HOY = date(2026, 10, 20)  # martes
OK = SimpleNamespace(estado='ENVIADO', proveedor_id='')


def _resp(rol, telefono, **kw):
    return Responsable.objects.create(rol=rol, nombre=f'{rol} test', telefono_wa=telefono, **kw)


class MocksWA:
    """Intercepta los transportes de WhatsApp de la bandeja y deja la ventana de 24 h abierta."""

    def setUp(self):
        super().setUp()
        for n in ('enviar_whatsapp', 'enviar_whatsapp_template', 'enviar_whatsapp_botones'):
            p = patch.object(services_pendientes, n, return_value=OK)
            setattr(self, n, p.start())
            self.addCleanup(p.stop)
        self.ventana = patch.object(services_pendientes, 'ventana_abierta', return_value=True)
        self.ventana.start()
        self.addCleanup(self.ventana.stop)
        self.ventana_dir = patch.object(services_direccion, 'ventana_abierta', return_value=True)
        self.ventana_dir.start()
        self.addCleanup(self.ventana_dir.stop)

    def textos(self):
        """Todo lo que salió, en cualquier transporte."""
        salidas = []
        for mock in (self.enviar_whatsapp, self.enviar_whatsapp_template, self.enviar_whatsapp_botones):
            for c in mock.call_args_list:
                salidas.append(c.kwargs.get('mensaje') or ' '.join(c.kwargs.get('parametros') or []))
        return salidas


def _cotizacion(fecha, precio='10000.00', estado='CONFIRMADA', nombre='Cliente F4', created=None):
    cliente = Cliente.objects.create(nombre=nombre, telefono='5555550001')
    cot = Cotizacion.objects.create(cliente=cliente, nombre_evento='Evento', incluye_refrescos=False,
                                    fecha_evento=fecha)
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1,
                                  precio_unitario=Decimal(precio))
    Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
    if created:
        Cotizacion.objects.filter(pk=cot.pk).update(created_at=created)
    cot.refresh_from_db()
    return cot


# ───────────────────────────── Hallazgos ──────────────────────────────────────

class HallazgosTest(MocksWA, TestCase):
    def test_producto_con_precio_por_debajo_de_su_costo_es_hallazgo(self):
        malo = Producto.objects.create(nombre='Barra mala', precio_venta_fijo=Decimal('50.00'))
        bueno = Producto.objects.create(nombre='Barra buena', precio_venta_fijo=Decimal('500.00'))
        sin_receta = Producto.objects.create(nombre='Sin receta', precio_venta_fijo=Decimal('10.00'))
        costos = {malo.pk: Decimal('100.00'), bueno.pk: Decimal('100.00'), sin_receta.pk: Decimal('0.00')}
        with patch.object(Producto, 'calcular_costo', autospec=True, side_effect=lambda p: costos.get(p.pk, Decimal('0.00'))):
            encontrados = list(services_direccion._hallazgos_margen(HOY))
        self.assertEqual([h['clave'] for h in encontrados], [f'margen:{malo.pk}'])
        self.assertIn('$50.00', encontrados[0]['detalle'])
        self.assertIn('$100.00', encontrados[0]['detalle'])

    def test_pago_sin_solicitud_de_factura_es_hallazgo_y_la_condonacion_no(self):
        cot = _cotizacion(HOY + timedelta(days=30))
        pago = Pago.objects.create(cotizacion=cot, monto=Decimal('5800.00'), metodo='TRANSFERENCIA',
                                   fecha_pago=HOY - timedelta(days=2))
        Pago.objects.create(cotizacion=cot, monto=Decimal('100.00'), metodo='CONDONACION', fecha_pago=HOY)
        SolicitudFactura.objects.filter(pago=pago).delete()
        encontrados = list(services_direccion._hallazgos_sin_factura(HOY))
        self.assertEqual([h['clave'] for h in encontrados], [f'sin_factura:{pago.pk}'])
        self.assertIn('$5,800.00', encontrados[0]['titulo'])

    def test_cotizacion_cotizada_sin_seguimiento_ni_pagos(self):
        vieja = timezone.make_aware(datetime(2026, 10, 1, 12, 0))
        cot = _cotizacion(HOY + timedelta(days=40), estado='COTIZADA', created=vieja)
        reciente = _cotizacion(HOY + timedelta(days=40), estado='COTIZADA', nombre='Reciente',
                               created=timezone.make_aware(datetime(2026, 10, 19, 12, 0)))
        encontrados = [h['clave'] for h in services_direccion._hallazgos_sin_seguimiento(HOY)]
        self.assertEqual(encontrados, [f'seguimiento:{cot.pk}'])
        self.assertNotIn(f'seguimiento:{reciente.pk}', encontrados)
        ComunicacionCliente.objects.create(cotizacion=cot, canal='WHATSAPP', tipo='SEGUIMIENTO', estado='ENVIADO',
                                           destinatario='525555550001', trigger='CRON')
        self.assertEqual(list(services_direccion._hallazgos_sin_seguimiento(HOY)), [])

    def test_maximo_tres_uno_por_tipo_y_lo_ignorado_no_vuelve(self):
        def det(tipo, n):
            def f(hoy):
                for i in range(n):
                    yield {'clave': f'{tipo}:{i}', 'tipo': 'MARGEN', 'titulo': f'{tipo} {i}', 'detalle': 'd'}
            return f
        detectores = (det('a', 2), det('b', 1), det('c', 1), det('d', 1))
        primeros = services_direccion.detectar(HOY, detectores)
        self.assertEqual([h.clave for h in primeros], ['a:0', 'b:0', 'c:0'])
        services_direccion.ignorar(primeros[0])
        segundos = services_direccion.detectar(HOY, detectores)
        self.assertEqual([h.clave for h in segundos], ['a:1', 'b:0', 'c:0'])
        self.assertEqual(Hallazgo.objects.count(), 4)

    def test_un_detector_que_falla_no_tumba_a_los_demas(self):
        def roto(hoy):
            raise RuntimeError('boom')
            yield  # pragma: no cover

        def bueno(hoy):
            yield {'clave': 'ok:1', 'tipo': 'MARGEN', 'titulo': 'Bueno', 'detalle': ''}
        self.assertEqual([h.clave for h in services_direccion.detectar(HOY, (roto, bueno))], ['ok:1'])

    def test_crear_tarea_abre_un_pendiente_normal_para_direccion_una_sola_vez(self):
        h = Hallazgo.objects.create(clave='x:1', tipo='MARGEN', titulo='Algo raro', detalle='Detalle')
        pendiente = services_direccion.crear_tarea(h)
        self.assertEqual((pendiente.tipo, pendiente.rol, pendiente.severidad), ('HALLAZGO', 'DIRECCION', 'NORMAL'))
        h.refresh_from_db()
        self.assertEqual((h.estado, h.pendiente_id), ('TAREA', pendiente.pk))
        self.assertIsNone(services_direccion.crear_tarea(h))
        self.assertEqual(Pendiente.objects.count(), 1)


# ───────────────────────────── Resumen semanal ────────────────────────────────

class ResumenSemanalTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        _resp('DIRECCION', TEL_DIR)
        MetaMensual.objects.create(anio=2026, mes=10, meta_venta_neta=Decimal('20000.00'))
        _cotizacion(date(2026, 10, 12))  # venta neta 10,000.00

    def test_resumen_con_venta_contra_meta_ocupacion_caja_y_pendientes(self):
        services_pendientes.registrar_pendiente(titulo='Algo abierto', rol='DIRECCION')
        with patch.object(services_direccion, 'detectar', return_value=[]):
            self.assertEqual(services_direccion.enviar_resumen_semanal(HOY), 1)
        texto = self.textos()[0]
        self.assertIn('$10,000.00 de $20,000.00', texto)
        self.assertIn('Ocupación:', texto)
        self.assertIn('Caja según libros: $0.00', texto)
        self.assertIn('Pendientes abiertos: 1', texto)
        self.assertNotIn('Detecté', texto)

    def test_clave_de_idempotencia_por_semana_y_telefono(self):
        with patch.object(services_direccion, 'detectar', return_value=[]):
            services_direccion.enviar_resumen_semanal(HOY)
        clave = (self.enviar_whatsapp.call_args or self.enviar_whatsapp_template.call_args).kwargs[
            'clave_idempotencia']
        self.assertEqual(clave, f'dir:resumen:{date(2026, 10, 19)}:{TEL_DIR}')

    def test_con_hallazgos_manda_un_mensaje_con_botones_por_cada_uno(self):
        h1 = Hallazgo.objects.create(clave='a', tipo='MARGEN', titulo='Uno', detalle='d1')
        h2 = Hallazgo.objects.create(clave='b', tipo='SIN_FACTURA', titulo='Dos', detalle='d2')
        with patch.object(services_direccion, 'detectar', return_value=[h1, h2]):
            services_direccion.enviar_resumen_semanal(HOY)
        self.assertIn('Detecté: 1) Uno. 2) Dos.', self.textos()[0])
        botones = [c.kwargs['botones'] for c in self.enviar_whatsapp_botones.call_args_list]
        self.assertEqual(botones[0], [(f'hal:{h1.pk}:tarea', 'Crear tarea'), (f'hal:{h1.pk}:ign', 'Ignorar')])
        self.assertEqual(len(botones), 2)
        h1.refresh_from_db()
        self.assertIsNotNone(h1.propuesto_en)

    def test_sin_ventana_abierta_no_manda_mensajes_sueltos_de_hallazgos(self):
        h1 = Hallazgo.objects.create(clave='a', tipo='MARGEN', titulo='Uno', detalle='d1')
        with patch.object(services_direccion, 'detectar', return_value=[h1]), \
                patch.object(services_direccion, 'ventana_abierta', return_value=False):
            services_direccion.enviar_resumen_semanal(HOY)
        self.enviar_whatsapp_botones.assert_not_called()

    def test_sin_responsable_de_direccion_no_envia(self):
        Responsable.objects.all().delete()
        with patch.object(services_direccion, 'detectar', return_value=[]):
            self.assertEqual(services_direccion.enviar_resumen_semanal(HOY), 0)
        self.assertEqual(self.textos(), [])


class BotonesHallazgoTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        _resp('DIRECCION', TEL_DIR)
        self.h = Hallazgo.objects.create(clave='x:1', tipo='MARGEN', titulo='Algo raro', detalle='d')

    def test_es_boton_solo_acepta_acciones_validas(self):
        self.assertTrue(services_direccion.es_boton('hal:3:tarea'))
        self.assertTrue(services_direccion.es_boton('nom:3:ok'))
        self.assertFalse(services_direccion.es_boton('hal:3:ok'))
        self.assertFalse(services_direccion.es_boton('nom:3:tarea'))

    def test_crear_tarea_desde_el_telefono_de_direccion(self):
        respuesta = services_direccion.atender_boton(TEL_DIR, f'hal:{self.h.pk}:tarea')
        self.assertIn('abrí la tarea', respuesta)
        self.assertEqual(Pendiente.objects.filter(tipo='HALLAZGO', rol='DIRECCION').count(), 1)

    def test_ignorar(self):
        services_direccion.atender_boton(TEL_DIR, f'hal:{self.h.pk}:ign')
        self.h.refresh_from_db()
        self.assertEqual(self.h.estado, 'IGNORADO')
        self.assertEqual(Pendiente.objects.count(), 0)

    def test_un_telefono_ajeno_se_descarta(self):
        self.assertIsNone(services_direccion.atender_boton(TEL_OTRO, f'hal:{self.h.pk}:tarea'))
        self.h.refresh_from_db()
        self.assertEqual(self.h.estado, 'PROPUESTO')

    def test_un_numero_pendiente_de_acepto_no_vale(self):
        Responsable.objects.all().delete()
        Responsable.objects.create(rol='DIRECCION', nombre='Nuevo', telefono_pendiente=TEL_OTRO)
        self.assertIsNone(services_direccion.atender_boton(TEL_OTRO, f'hal:{self.h.pk}:tarea'))

    def test_el_agente_no_recibe_el_boton(self):
        from comunicacion.models import ConversacionWhatsApp

        conv = services_agente.recibir_mensaje(telefono=TEL_DIR, nombre='Dir', texto=f'hal:{self.h.pk}:ign',
                                               wamid='wh1')
        self.assertIsInstance(conv, ConversacionWhatsApp)
        with patch.object(services_agente, '_cliente_ia') as ia, \
                patch.object(services_agente, 'enviar_whatsapp', return_value=OK):
            services_agente.procesar_conversacion(conv.pk)
        ia.assert_not_called()
        self.h.refresh_from_db()
        self.assertEqual(self.h.estado, 'IGNORADO')


# ───────────────────────────── Desviación ─────────────────────────────────────

class DesviacionTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        _resp('DIRECCION', TEL_DIR)

    def _mes(self, meta='20000.00', venta='10000.00'):
        MetaMensual.objects.create(anio=2026, mes=10, meta_venta_neta=Decimal(meta))
        if venta:
            _cotizacion(date(2026, 10, 12), precio=venta)

    def test_por_debajo_del_70_abre_un_urgente_una_sola_vez_por_mes(self):
        self._mes(venta='10000.00')  # 50 %
        p = services_direccion.revisar_desviacion(HOY)
        self.assertEqual((p.tipo, p.severidad, p.rol), ('DESVIACION', 'URGENTE', 'DIRECCION'))
        self.assertIn('50', p.titulo)
        self.assertEqual(services_direccion.revisar_desviacion(HOY + timedelta(days=3)).pk, p.pk)
        self.assertEqual(Pendiente.objects.filter(tipo='DESVIACION').count(), 1)
        self.enviar_whatsapp_botones.assert_called_once()  # el aviso urgente salió una sola vez

    def test_antes_del_dia_15_no_alerta(self):
        self._mes(venta='1000.00')
        self.assertIsNone(services_direccion.revisar_desviacion(date(2026, 10, 14)))

    def test_en_el_70_o_mas_no_alerta(self):
        self._mes(venta='14000.00')  # exactamente 70 %
        self.assertIsNone(services_direccion.revisar_desviacion(HOY))
        self.assertEqual(Pendiente.objects.count(), 0)

    def test_sin_meta_calculable_no_alerta(self):
        with patch('indicadores.services.tablero.TableroService.generar',
                   return_value={'mes': {'meta_venta': None, 'pct_meta': None, 'etiqueta': 'x'}}):
            self.assertIsNone(services_direccion.revisar_desviacion(HOY))

    def test_el_mes_siguiente_puede_alertar_de_nuevo(self):
        self._mes(venta='10000.00')
        services_direccion.revisar_desviacion(HOY)
        MetaMensual.objects.create(anio=2026, mes=11, meta_venta_neta=Decimal('20000.00'))
        _cotizacion(date(2026, 11, 5), precio='1000.00')
        self.assertIsNotNone(services_direccion.revisar_desviacion(date(2026, 11, 18)))
        self.assertEqual(Pendiente.objects.filter(tipo='DESVIACION').count(), 2)


# ───────────────────────────── Nómina ─────────────────────────────────────────

class NominaSemanalTest(MocksWA, TestCase):
    VIERNES = date(2026, 10, 23)

    def setUp(self):
        super().setUp()
        _resp('DIRECCION', TEL_DIR)
        emp = Empleado.objects.create(nombre='Ana', tarifa_base=Decimal('100.00'))
        self.r1 = self._recibo(emp, '2026-10-19 al 2026-10-23', '1500.50')
        self.r2 = self._recibo(emp, '2026-10-19 al 2026-10-23', '800.25')
        self._recibo(emp, '2026-10-19 al 2026-10-23', '999.00', estado='CANCELADO')
        self._recibo(emp, '2026-10-12 al 2026-10-16', '5000.00')  # semana anterior

    def _recibo(self, emp, periodo, total, estado='CALCULADO'):
        return ReciboNomina.objects.create(empleado=emp, periodo=periodo, horas_trabajadas=Decimal('10'),
                                           tarifa_aplicada=Decimal('100'), total_pagado=Decimal(total), estado=estado)

    def test_manda_el_total_de_la_semana_sin_cancelados_con_boton_aprobar(self):
        self.assertEqual(services_direccion.enviar_nomina_semanal(self.VIERNES), 1)
        ap = AprobacionNomina.objects.get()
        self.assertEqual((ap.total, ap.recibos, ap.semana_inicio), (Decimal('2300.75'), 2, date(2026, 10, 19)))
        self.assertIn('$2,300.75', self.textos()[0])
        self.assertEqual(self.enviar_whatsapp_botones.call_args.kwargs['botones'], [(f'nom:{ap.pk}:ok', 'Aprobar')])
        self.assertIsNotNone(ap.notificada_en)

    def test_una_semana_ya_avisada_no_se_manda_otra_vez(self):
        services_direccion.enviar_nomina_semanal(self.VIERNES)
        self.enviar_whatsapp_botones.reset_mock()
        self.assertEqual(services_direccion.enviar_nomina_semanal(self.VIERNES), 0)
        self.enviar_whatsapp_botones.assert_not_called()

    def test_sin_recibos_no_manda_nada(self):
        ReciboNomina.objects.all().delete()
        self.assertEqual(services_direccion.enviar_nomina_semanal(self.VIERNES), 0)
        self.assertEqual(AprobacionNomina.objects.count(), 0)

    def test_aprobar_deja_registro_de_quien_y_cuando_sin_polizas_ni_cambios_en_recibos(self):
        services_direccion.enviar_nomina_semanal(self.VIERNES)
        ap = AprobacionNomina.objects.get()
        polizas = Poliza.objects.count()
        respuesta = services_direccion.atender_boton(TEL_DIR, f'nom:{ap.pk}:ok')
        self.assertIn('$2,300.75', respuesta)
        ap.refresh_from_db()
        self.assertTrue(ap.aprobada)
        self.assertEqual((ap.aprobada_por_nombre, ap.aprobada_por_telefono), ('DIRECCION test', TEL_DIR))
        self.assertEqual(Poliza.objects.count(), polizas)
        self.r1.refresh_from_db()
        self.assertEqual(self.r1.estado, 'CALCULADO')
        # un solo uso
        self.assertEqual(services_direccion.atender_boton(TEL_DIR, f'nom:{ap.pk}:ok'), 'Esa nómina ya estaba aprobada.')

    def test_solo_aprueba_el_telefono_de_direccion(self):
        services_direccion.enviar_nomina_semanal(self.VIERNES)
        ap = AprobacionNomina.objects.get()
        self.assertIsNone(services_direccion.atender_boton(TEL_OTRO, f'nom:{ap.pk}:ok'))
        ap.refresh_from_db()
        self.assertFalse(ap.aprobada)


# ───────────────────────────── Contador y CFDI ────────────────────────────────

class ContadorCfdiTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        _resp('DIRECCION', TEL_DIR)
        _resp('CONTADOR', TEL_CON)
        self.cliente = Cliente.objects.create(nombre='Cliente CFDI')
        self.cfdi = CfdiSAT.objects.create(
            uuid='CCCCCCCC-0000-0000-0000-000000000001', direccion='EMITIDO', resultado='SIN_VINCULAR',
            rfc_emisor='PECE010202IA0', rfc_receptor='XEXX010101000', nombre_contraparte='CLIENTE SA',
            total=Decimal('1500.00'), fecha=date(2026, 10, 12))
        self.s1 = self._solicitud()
        self.s2 = self._solicitud()

    def _solicitud(self, monto='1500.00'):
        return SolicitudFactura.objects.create(
            cliente=self.cliente, rfc='XEXX010101000', razon_social='CLIENTE SA', codigo_postal='97390',
            monto=Decimal(monto), estado='ENVIADA', fecha_solicitud=timezone.make_aware(datetime(2026, 10, 10)))

    def test_un_mensaje_agrupado_al_dia_con_boton_por_candidata(self):
        self.assertEqual(services_contador.preguntar_contador(HOY), 1)
        self.assertEqual(self.enviar_whatsapp_botones.call_count, 1)
        llamada = self.enviar_whatsapp_botones.call_args.kwargs
        self.assertEqual(llamada['botones'], [
            (f'cfdi:{self.cfdi.pk}:{self.s1.pk}', f'SOL-{self.s1.pk:04d}'),
            (f'cfdi:{self.cfdi.pk}:{self.s2.pk}', f'SOL-{self.s2.pk:04d}'),
            (f'cfdi:{self.cfdi.pk}:no', 'Ninguna'),
        ])
        self.assertIn('$1,500.00', llamada['mensaje'])
        self.assertEqual(ConsultaCfdi.objects.count(), 1)
        # misma corrida el mismo día: no reenvía
        self.assertEqual(services_contador.preguntar_contador(HOY), 0)
        self.assertEqual(self.enviar_whatsapp_botones.call_count, 1)

    def test_varios_cfdi_van_en_un_solo_mensaje(self):
        otro = CfdiSAT.objects.create(
            uuid='CCCCCCCC-0000-0000-0000-000000000002', direccion='EMITIDO', resultado='SIN_VINCULAR',
            rfc_emisor='PECE010202IA0', rfc_receptor='XEXX010101001', total=Decimal('99.00'), fecha=date(2026, 10, 13))
        self.assertEqual(services_contador.preguntar_contador(HOY), 1)
        self.assertEqual(len(self.textos()), 1)
        self.assertIn('2 CFDI', self.textos()[0])
        self.assertIn('sin solicitud que coincida', self.textos()[0])
        self.assertEqual(ConsultaCfdi.objects.filter(cfdi=otro).count(), 1)

    def test_un_cfdi_con_una_sola_candidata_no_es_ambiguo(self):
        self.s2.delete()
        self.assertEqual(services_contador.preguntar_contador(HOY), 0)
        self.assertEqual(self.textos(), [])

    def test_sin_contador_avisa_a_direccion_y_no_envia(self):
        Responsable.objects.filter(rol='CONTADOR').delete()
        self.assertEqual(services_contador.preguntar_contador(HOY), 0)
        self.assertEqual(Pendiente.objects.filter(tipo='SAT', rol='DIRECCION').count(), 1)
        services_contador.preguntar_contador(HOY)
        self.assertEqual(Pendiente.objects.filter(tipo='SAT', rol='DIRECCION').count(), 1)

    def test_el_boton_liga_con_la_misma_funcion_del_admin(self):
        services_contador.preguntar_contador(HOY)
        with patch('comunicacion.services_contador.services_sat.vincular') as vincular:
            respuesta = services_contador.atender_boton(TEL_CON, f'cfdi:{self.cfdi.pk}:{self.s2.pk}')
        vincular.assert_called_once_with(self.cfdi, self.s2)
        self.assertIn(f'SOL-{self.s2.pk:04d}', respuesta)
        consulta = ConsultaCfdi.objects.get()
        self.assertEqual((consulta.estado, consulta.solicitud_id), ('LIGADA', self.s2.pk))

    def test_liga_de_verdad_la_solicitud_a_facturada(self):
        services_contador.preguntar_contador(HOY)

        def liga(registro, solicitud, xml=None):
            solicitud.estado = 'FACTURADA'
            solicitud.save(update_fields=['estado'])
            registro.resultado = 'VINCULADO'
            registro.save()
        with patch('comunicacion.services_contador.services_sat.vincular', side_effect=liga):
            services_contador.atender_boton(TEL_CON, f'cfdi:{self.cfdi.pk}:{self.s1.pk}')
        self.s1.refresh_from_db()
        self.cfdi.refresh_from_db()
        self.assertEqual((self.s1.estado, self.cfdi.resultado), ('FACTURADA', 'VINCULADO'))

    def test_boton_de_otro_telefono_o_de_una_no_candidata_no_hace_nada(self):
        services_contador.preguntar_contador(HOY)
        ajena = self._solicitud(monto='9999.00')
        with patch('comunicacion.services_contador.services_sat.vincular') as vincular:
            self.assertIsNone(services_contador.atender_boton(TEL_OTRO, f'cfdi:{self.cfdi.pk}:{self.s1.pk}'))
            self.assertIsNone(services_contador.atender_boton(TEL_DIR, f'cfdi:{self.cfdi.pk}:{self.s1.pk}'))
            self.assertIsNone(services_contador.atender_boton(TEL_CON, f'cfdi:{self.cfdi.pk}:{ajena.pk}'))
        vincular.assert_not_called()
        self.assertEqual(ConsultaCfdi.objects.get().estado, 'PREGUNTADA')

    def test_ninguna_deja_el_cfdi_para_el_admin_y_no_se_vuelve_a_preguntar(self):
        services_contador.preguntar_contador(HOY)
        respuesta = services_contador.atender_boton(TEL_CON, f'cfdi:{self.cfdi.pk}:no')
        self.assertIn('admin', respuesta)
        self.assertEqual(ConsultaCfdi.objects.get().estado, 'NINGUNA')
        self.assertEqual(services_contador.preguntar_contador(HOY + timedelta(days=10)), 0)

    def test_tras_ligar_pregunta_el_siguiente_en_el_mismo_mensaje(self):
        cfdi2 = CfdiSAT.objects.create(
            uuid='CCCCCCCC-0000-0000-0000-000000000003', direccion='EMITIDO', resultado='SIN_VINCULAR',
            rfc_emisor='PECE010202IA0', rfc_receptor='XEXX010101000', total=Decimal('1500.00'), fecha=date(2026, 10, 12))
        s3, s4 = self._solicitud(), self._solicitud()
        services_contador.preguntar_contador(timezone.localdate())
        self.enviar_whatsapp_botones.reset_mock()
        with patch('comunicacion.services_contador.services_sat.vincular') as vincular:
            self.assertIsNone(services_contador.atender_boton(TEL_CON, f'cfdi:{self.cfdi.pk}:no'))
        self.assertEqual(vincular.call_count, 0)
        mensaje = self.enviar_whatsapp_botones.call_args.kwargs
        self.assertIn('Siguiente', mensaje['mensaje'])
        self.assertTrue(mensaje['botones'][0][0].startswith(f'cfdi:{cfdi2.pk}:'))
        self.assertGreaterEqual(len({s3.pk, s4.pk}), 2)

    def test_sin_respuesta_se_recuerda_una_vez_y_despues_se_avisa_a_direccion(self):
        services_contador.preguntar_contador(HOY, ahora=timezone.make_aware(datetime(2026, 10, 20, 10, 0)))
        ConsultaCfdi.objects.update(created_at=timezone.make_aware(datetime(2026, 10, 20, 10, 0)))
        ahora = timezone.make_aware(datetime(2026, 10, 22, 10, 0))
        self.assertEqual(services_contador.por_preguntar(ahora), [])  # aún no pasan 3 días
        ahora = timezone.make_aware(datetime(2026, 10, 24, 10, 0))
        self.assertEqual(len(services_contador.por_preguntar(ahora)), 1)
        services_contador.preguntar_contador(date(2026, 10, 24), ahora=ahora)
        self.assertEqual(ConsultaCfdi.objects.count(), 2)
        ConsultaCfdi.objects.update(created_at=timezone.make_aware(datetime(2026, 10, 24, 10, 0)))
        services_contador.preguntar_contador(date(2026, 10, 28), ahora=timezone.make_aware(datetime(2026, 10, 28, 10, 0)))
        self.assertEqual(ConsultaCfdi.objects.count(), 2)  # no hay tercera pregunta
        self.assertEqual(Pendiente.objects.filter(clave_idempotencia=f'cfdi:{self.cfdi.pk}:sin_respuesta').count(), 1)

    def test_el_agente_enruta_el_boton_del_contador(self):
        services_contador.preguntar_contador(HOY)
        conv = services_agente.recibir_mensaje(telefono=TEL_CON, nombre='Cont', texto=f'cfdi:{self.cfdi.pk}:no',
                                               wamid='wc1')
        with patch.object(services_agente, '_cliente_ia') as ia, \
                patch.object(services_agente, 'enviar_whatsapp', return_value=OK):
            services_agente.procesar_conversacion(conv.pk)
        ia.assert_not_called()
        self.assertEqual(ConsultaCfdi.objects.get().estado, 'NINGUNA')
