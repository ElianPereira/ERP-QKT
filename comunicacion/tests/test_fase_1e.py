"""Fase 1e (Issue #428): renglones pendientes de la Fase 1 — pendientes urgentes
de choque de fecha, contracargo, terminal por WhatsApp, quejas, FAQ por
WhatsApp, «listo» del personal y SAT."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import Cliente, Contracargo, Cotizacion, ItemCotizacion, Pago
from comunicacion import services_agente, services_pendientes
from comunicacion.models import Pendiente, Responsable
from comunicacion.services_notificaciones import alertar_equipo_contracargo, alertar_equipo_evidencia_automatica

from .test_agente import AGENTE, _cliente_falso, _respuesta, _texto, _uso
from .utils import TEL_CLIENTE, wa_settings

TEL_ENC = '525555550011'
TEL_DIR = '525555550012'
TEL_OTRO = '525555550013'
OK = SimpleNamespace(estado='ENVIADO', proveedor_id='')


def _resp(rol, telefono, **kw):
    return Responsable.objects.create(rol=rol, nombre=f'{rol} test', telefono_wa=telefono, **kw)


class MocksWA:
    """Intercepta los transportes de WhatsApp de la bandeja y del agente."""

    def setUp(self):
        super().setUp()
        mail.outbox = []
        for n in ('enviar_whatsapp', 'enviar_whatsapp_template', 'enviar_whatsapp_botones'):
            p = patch.object(services_pendientes, n, return_value=OK)
            setattr(self, n, p.start())
            self.addCleanup(p.stop)


def _cotizacion(dias=60, fecha=None, precio='10000.00', nombre='Cliente 1e'):
    cliente = Cliente.objects.create(nombre=nombre, telefono='5555550001')
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento='Evento', incluye_refrescos=False,
        fecha_evento=fecha or (timezone.localdate() + timedelta(days=dias)))
    ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio', cantidad=1,
                                  precio_unitario=Decimal(precio))
    cot.refresh_from_db()
    return cot  # 10000 + IVA = 11,600.00


# ───────────────────────────── Punto 0 ────────────────────────────────────────

@wa_settings(**AGENTE)
class MismoDiaTest(MocksWA, TestCase):
    def test_consulta_para_hoy_termina_en_pase_a_humano_urgente_para_el_encargado(self):
        _resp('ENCARGADO', TEL_ENC)
        conv = services_agente.recibir_mensaje(
            telefono=TEL_CLIENTE, nombre='Ana', texto='¿Hay lugar hoy?', wamid='w1')
        hoy = timezone.localdate().isoformat()
        cliente = _cliente_falso(
            _respuesta(_uso('consultar_disponibilidad', {'fecha': hoy}), stop='tool_use'),
            _respuesta(_uso('pasar_a_humano', {'motivo': 'Quiere contratar hoy'}, id_='toolu_2'), stop='tool_use'),
            _respuesta(_texto('Alguien del equipo te confirma por este chat.')),
        )
        with patch.object(services_agente, '_cliente_ia', return_value=cliente), \
                patch.object(services_agente, 'enviar_whatsapp', return_value=OK):
            services_agente.procesar_conversacion(conv.pk)
        p = Pendiente.objects.get(tipo='PASE_HUMANO')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'ENCARGADO'))


# ───────────────────────────── Punto 1 ────────────────────────────────────────

@wa_settings(**AGENTE)
class ChoqueFechaTest(MocksWA, TestCase):
    def _choque(self):
        a = _cotizacion()
        Pago.objects.create(cotizacion=a, monto=Decimal('5800.00'), metodo='TRANSFERENCIA')
        b = _cotizacion(fecha=a.fecha_evento, nombre='Segundo')
        with self.captureOnCommitCallbacks(execute=True):
            pago = Pago.objects.create(cotizacion=b, monto=Decimal('5800.00'), metodo='TRANSFERENCIA')
        return b, pago

    def test_pago_con_fecha_ocupada_deja_un_pendiente_urgente_para_direccion(self):
        b, pago = self._choque()
        p = Pendiente.objects.get(tipo='CHOQUE_FECHA')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'DIRECCION'))
        self.assertEqual(p.titulo, f'Pago con fecha ocupada — COT-{b.pk:03d}')
        self.assertEqual(p.clave_idempotencia, f'pago_fecha_ocupada:{pago.pk}')
        self.assertIn('5,800.00', p.detalle)
        self.assertIn('mover fecha o reembolsar', p.detalle)
        self.assertNotRegex(p.titulo, r'[^\w\s—.,-]')  # sin emojis en el admin

    def test_no_queda_el_pendiente_alerta_normal_duplicado(self):
        self._choque()
        self.assertFalse(Pendiente.objects.filter(tipo='ALERTA', titulo__icontains='fecha ocupada').exists())
        self.assertTrue(any('fecha ocupada' in m.subject for m in mail.outbox))  # el correo sí sale

    def test_repetirlo_no_crea_otro(self):
        b, pago = self._choque()
        with self.captureOnCommitCallbacks(execute=True):
            pago._confirmar_cotizacion()
        self.assertEqual(Pendiente.objects.filter(tipo='CHOQUE_FECHA').count(), 1)


# ───────────────────────────── Punto 2 ────────────────────────────────────────

@wa_settings(**AGENTE)
class ContracargoPendienteTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        self.cot = _cotizacion()

    def _contracargo(self, estado='EN_DISPUTA', **kw):
        return Contracargo.objects.create(
            openpay_id=f'cb_{estado}', cotizacion=self.cot, estado=estado,
            monto=Decimal('1000.00'), payload_crudo={}, **kw)

    def test_en_disputa_deja_un_pendiente_urgente_con_folio_monto_y_limite(self):
        c = self._contracargo(fecha_limite_evidencia=timezone.localdate() + timedelta(days=3))
        alertar_equipo_contracargo(c)
        alertar_equipo_contracargo(c)  # repetirlo no crea otro
        p = Pendiente.objects.get(tipo='CONTRACARGO')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'DIRECCION'))
        self.assertEqual(p.clave_idempotencia, f'contracargo:{c.pk}:EN_DISPUTA')
        self.assertIn(f'COT-{self.cot.pk:03d}', p.titulo)
        self.assertIn('1,000.00', p.detalle)
        self.assertIn('Fecha límite', p.detalle)
        self.assertFalse(Pendiente.objects.filter(tipo='ALERTA', titulo__icontains='contracargo').exists())
        self.assertEqual(len(mail.outbox), 1)

    def test_un_pendiente_por_estado(self):
        c = self._contracargo()
        alertar_equipo_contracargo(c)
        Contracargo.objects.filter(pk=c.pk).update(estado='PERDIDO')
        c.refresh_from_db()
        alertar_equipo_contracargo(c)
        self.assertEqual(Pendiente.objects.filter(tipo='CONTRACARGO').count(), 2)

    def test_la_evidencia_automatica_se_anota_en_el_pendiente(self):
        c = self._contracargo(fecha_limite_evidencia=timezone.localdate() + timedelta(days=1))
        alertar_equipo_contracargo(c)
        alertar_equipo_evidencia_automatica(c)
        p = Pendiente.objects.get(tipo='CONTRACARGO')
        self.assertIn('evidencia automática', p.detalle)


# ───────────────────────────── Punto 3 · dinero ───────────────────────────────

@wa_settings(**AGENTE)
class TerminalPorWhatsAppTest(TestCase):
    def setUp(self):
        _resp('ENCARGADO', TEL_ENC)
        _resp('DIRECCION', TEL_DIR)
        self.cot = _cotizacion()  # 11,600.00
        self.botones = patch.object(services_agente, 'enviar_whatsapp_botones', return_value=OK).start()
        self.texto = patch.object(services_agente, 'enviar_whatsapp', return_value=OK).start()
        self.aviso = patch.object(services_agente, '_enviar_aviso', return_value=OK).start()
        self.addCleanup(patch.stopall)
        self.n = 0

    def _decir(self, telefono, texto, agente=None):
        self.n += 1
        conv = services_agente.recibir_mensaje(
            telefono=telefono, nombre='Alguien', texto=texto, wamid=f'w{self.n}')
        agente = agente or _cliente_falso(_respuesta(_texto('Hola.')))
        with patch.object(services_agente, '_cliente_ia', return_value=agente):
            services_agente._atender_pendientes(conv.pk)
        return agente

    def _proponer(self, telefono=TEL_ENC, texto=None):
        self._decir(telefono, texto or f'terminal 5000 cot {self.cot.pk}')
        return self.botones.call_args.kwargs['botones'][0][0]

    def test_encargado_recibe_resumen_con_boton_y_sin_confirmar_no_hay_pago(self):
        boton = self._proponer()
        kw = self.botones.call_args.kwargs
        self.assertEqual(kw['telefono'], TEL_ENC)
        self.assertIn('Registrar $5,000.00 con terminal a COT-', kw['mensaje'])
        self.assertIn('Saldo después: $6,600.00', kw['mensaje'])
        self.assertEqual(kw['botones'][0][1], 'Confirmar')
        self.assertTrue(boton.startswith('term:') and boton.endswith(':ok'))
        self.assertEqual(Pago.objects.count(), 0)
        from comercial.models import ReporteTerminal
        r = ReporteTerminal.objects.get()
        self.assertEqual((r.estado, r.telefono, r.rol), ('PROPUESTO', TEL_ENC, 'ENCARGADO'))
        self.assertNotIn(boton.split(':')[1], r.token_hash)  # nunca en claro

    def test_confirmado_crea_un_pago_con_sus_signals_y_copia_a_direccion(self):
        from contabilidad.models import Poliza
        from facturacion.models import SolicitudFactura
        boton = self._proponer()
        with self.captureOnCommitCallbacks(execute=True):
            self._decir(TEL_ENC, boton)
        pago = Pago.objects.get()
        from comercial.models import ReporteTerminal
        r = ReporteTerminal.objects.get()
        self.assertEqual((pago.metodo, pago.monto, pago.tipo, pago.concepto),
                         ('TARJETA_DEBITO', Decimal('5000.00'), 'INGRESO', 'VENTA'))
        self.assertEqual(pago.referencia, f'TERMINAL-WA-{r.pk}')
        self.assertEqual(pago.notas, f'Reportado por WhatsApp desde ENCARGADO {TEL_ENC}')
        self.assertFalse(pago.comision_tpv)
        self.assertEqual((r.estado, r.pago_id), ('CONFIRMADO', pago.pk))
        self.assertTrue(Poliza.objects.exists())                     # póliza (signal)
        self.assertTrue(SolicitudFactura.objects.filter(pago=pago).exists())  # solicitud de factura (signal)
        self.assertIn('Saldo pendiente: $6,600.00', self.texto.call_args.kwargs['mensaje'])
        self.assertEqual([c.args[0] for c in self.aviso.call_args_list], [TEL_DIR])  # copia a Dirección

    def test_confirmar_por_encima_del_anticipo_confirma_la_cotizacion(self):
        boton = self._proponer(texto=f'terminal 5800 cot {self.cot.pk}')
        self._decir(TEL_ENC, boton)
        self.cot.refresh_from_db()
        self.assertEqual(self.cot.estado, 'CONFIRMADA')

    def test_confirmado_dos_veces_crea_un_solo_pago(self):
        boton = self._proponer()
        self._decir(TEL_ENC, boton)
        self._decir(TEL_ENC, boton)
        self.assertEqual(Pago.objects.count(), 1)
        self.assertIn('ya se había registrado', self.texto.call_args.kwargs['mensaje'])

    def test_vencido_no_crea_nada(self):
        from comercial.models import ReporteTerminal
        boton = self._proponer()
        ReporteTerminal.objects.update(vence_en=timezone.now() - timedelta(seconds=1))
        self._decir(TEL_ENC, boton)
        self.assertEqual(Pago.objects.count(), 0)
        self.assertEqual(ReporteTerminal.objects.get().estado, 'VENCIDO')
        self.assertIn('10 minutos', self.texto.call_args.kwargs['mensaje'])
        self._decir(TEL_ENC, boton)  # y vencido sigue sin servir
        self.assertEqual(Pago.objects.count(), 0)

    def test_el_token_solo_vale_para_el_mismo_telefono(self):
        boton = self._proponer(TEL_ENC)
        self._decir(TEL_DIR, boton)  # Dirección es del directorio, pero el token no es suyo
        self.assertEqual(Pago.objects.count(), 0)

    def test_una_propuesta_identica_nueva_anula_la_anterior(self):
        primero = self._proponer()
        segundo = self._proponer()
        self._decir(TEL_ENC, primero)
        self.assertEqual(Pago.objects.count(), 0)
        self._decir(TEL_ENC, segundo)
        self.assertEqual(Pago.objects.count(), 1)

    def test_si_el_saldo_cambia_antes_de_confirmar_se_revalida(self):
        boton = self._proponer(texto=f'terminal 9000 cot {self.cot.pk}')
        Pago.objects.create(cotizacion=self.cot, monto=Decimal('5000.00'), metodo='EFECTIVO')  # saldo 6,600
        self._decir(TEL_ENC, boton)
        self.assertEqual(Pago.objects.count(), 1)
        self.assertIn('mayor al saldo', self.texto.call_args.kwargs['mensaje'])

    def test_rechazos_con_motivo_y_sin_pago(self):
        from comercial.models import ReporteTerminal
        casos = {
            f'terminal 20000 cot {self.cot.pk}': 'mayor al saldo',
            'terminal 100 cot 99999': 'No encontré esa cotización',
            f'terminal 0 cot {self.cot.pk}': 'mayor a cero',
        }
        for texto, motivo in casos.items():
            self._decir(TEL_ENC, texto)
            self.assertIn(motivo, self.texto.call_args.kwargs['mensaje'], texto)
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='CANCELADA')
        self._decir(TEL_ENC, f'terminal 100 cot {self.cot.pk}')
        self.assertIn('cancelada', self.texto.call_args.kwargs['mensaje'])
        self.assertEqual((Pago.objects.count(), ReporteTerminal.objects.count()), (0, 0))
        self.botones.assert_not_called()

    def test_formatos_del_mensaje(self):
        for texto in (f'  TERMINAL $1,500.50 COT-{self.cot.pk}  ', f'Terminal 1500.5 cot {self.cot.pk}'):
            self._decir(TEL_ENC, texto)
        self.assertEqual(self.botones.call_count, 2)
        self.assertIn('$1,500.50', self.botones.call_args_list[0].kwargs['mensaje'])
        self.assertIn('$1,500.50', self.botones.call_args_list[1].kwargs['mensaje'])

    def test_desde_un_numero_fuera_del_directorio_sigue_el_flujo_normal_del_agente(self):
        agente = self._decir(TEL_CLIENTE, f'terminal 5000 cot {self.cot.pk}')
        agente.beta.messages.create.assert_called()
        self.botones.assert_not_called()
        self.assertEqual(Pago.objects.count(), 0)

    def test_boton_de_un_numero_ajeno_no_hace_nada_ni_llega_al_agente(self):
        boton = self._proponer()
        agente = self._decir(TEL_CLIENTE, boton)
        agente.beta.messages.create.assert_not_called()
        self.assertEqual(Pago.objects.count(), 0)

    def test_un_numero_pendiente_de_aceptar_no_mueve_dinero(self):
        Responsable.objects.filter(rol='ENCARGADO').update(telefono_pendiente=TEL_OTRO)
        self._decir(TEL_OTRO, f'terminal 5000 cot {self.cot.pk}')
        self.botones.assert_not_called()
        self.assertEqual(Pago.objects.count(), 0)

    def test_el_webhook_conserva_el_id_del_boton(self):
        from comunicacion.views_whatsapp import _texto as texto_de
        boton = self._proponer()
        msg = {'type': 'interactive', 'interactive': {'button_reply': {'id': boton, 'title': 'Confirmar'}}}
        self.assertEqual(texto_de(msg), boton)


# ───────────────────────────── Punto 4 · quejas ───────────────────────────────

@wa_settings(**AGENTE, META_WA_PLANTILLA_OPERACIONES='aviso_operaciones')
class QuejaTest(MocksWA, TestCase):
    def _pasar(self, entrada):
        _resp('ENCARGADO', TEL_ENC)
        _resp('DIRECCION', TEL_DIR, modo_copia='EN_VIVO')
        conv = services_agente.recibir_mensaje(telefono=TEL_CLIENTE, nombre='Ana', texto='Qué mal servicio', wamid='q1')
        cliente = _cliente_falso(
            _respuesta(_uso('pasar_a_humano', entrada), stop='tool_use'),
            _respuesta(_texto('Te comunico con alguien del equipo.')),
        )
        with patch.object(services_agente, '_cliente_ia', return_value=cliente), \
                patch.object(services_agente, 'enviar_whatsapp', return_value=OK):
            services_agente.procesar_conversacion(conv.pk)

    def test_categoria_queja_abre_pendiente_queja_urgente_para_el_encargado_y_copia_a_direccion(self):
        self._pasar({'motivo': 'Alberca sucia', 'categoria': 'queja'})
        p = Pendiente.objects.get(tipo='QUEJA')
        self.assertEqual((p.severidad, p.rol), ('URGENTE', 'ENCARGADO'))
        self.assertFalse(Pendiente.objects.filter(tipo='PASE_HUMANO').exists())
        destinos = [c.kwargs['telefono'] for c in self.enviar_whatsapp_template.call_args_list]
        self.assertEqual(destinos, [TEL_ENC, TEL_DIR])

    def test_sin_categoria_o_con_otra_sigue_siendo_pase_a_humano(self):
        self._pasar({'motivo': 'Quiere negociar', 'categoria': 'venta'})
        self.assertEqual(Pendiente.objects.get().tipo, 'PASE_HUMANO')

    def test_el_esquema_de_la_herramienta_ofrece_la_categoria(self):
        from comunicacion.herramientas_agente import HERRAMIENTAS
        esquema = next(h for h in HERRAMIENTAS if h['name'] == 'pasar_a_humano')['input_schema']
        self.assertEqual(esquema['properties']['categoria']['enum'], ['queja', 'venta', 'otro'])
        self.assertEqual(esquema['required'], ['motivo'])

    def test_el_caso_queja_esta_en_los_evals(self):
        from comunicacion.evaluacion_agente import cargar_casos
        self.assertEqual(cargar_casos(['queja'])[0]['debe_usar'], ['pasar_a_humano'])


# ───────────────────────────── Punto 5 · FAQ por WhatsApp ─────────────────────

@wa_settings(**AGENTE, META_WA_PLANTILLA_OPERACIONES='aviso_operaciones')
class FaqPorWhatsAppTest(MocksWA, TestCase):
    def setUp(self):
        super().setUp()
        from comunicacion import services_preguntas
        from comunicacion.models import PreguntaSinRespuesta
        _resp('ENCARGADO', TEL_ENC)
        _resp('DIRECCION', TEL_DIR)
        self.sp = services_preguntas
        conv = services_agente._conversacion(TEL_CLIENTE)
        PreguntaSinRespuesta.objects.create(conversacion=conv, pregunta='¿Hay estacionamiento?')
        grupos = [{'pregunta': f'Pregunta {i}', 'respuesta_sugerida': f'Respuesta {i}', 'veces': 1}
                  for i in range(7)]
        grupos[1]['respuesta_sugerida'] = '[COMPLETAR]'
        p = patch.object(services_preguntas, '_agrupar', return_value=grupos)
        p.start()
        self.addCleanup(p.stop)
        patch.object(services_agente, 'enviar_whatsapp', return_value=OK).start()
        self.addCleanup(patch.stopall)

    def _aplicar(self):
        with patch.object(services_agente, '_avisar_propietario'):
            self.sp.resumir_preguntas(aplicar=True)

    def _decir(self, telefono, texto):
        conv = services_agente.recibir_mensaje(telefono=telefono, nombre='X', texto=texto, wamid=f'f{texto}{telefono}')
        with patch.object(services_agente, '_cliente_ia', return_value=_cliente_falso()):
            services_agente._atender_pendientes(conv.pk)

    def test_envia_hasta_cinco_propuestas_a_direccion_con_boton_y_enlace(self):
        from comercial.models import PreguntaFrecuente
        self._aplicar()
        self.assertEqual(PreguntaFrecuente.objects.filter(activo=False).count(), 7)
        llamadas = self.enviar_whatsapp_template.call_args_list
        self.assertEqual(len(llamadas), 5)
        self.assertTrue(all(c.kwargs['telefono'] == TEL_DIR for c in llamadas))
        self.assertIn('/admin/comercial/preguntafrecuente/', llamadas[0].kwargs['parametros'][0])
        self.assertIn('activo__exact=0', llamadas[0].kwargs['parametros'][0])

    def test_con_ventana_abierta_lleva_el_boton_aprobar(self):
        from .test_pendientes import _abrir_ventana
        _abrir_ventana(TEL_DIR)
        self._aplicar()
        botones = self.enviar_whatsapp_botones.call_args_list
        self.assertEqual(len(botones), 4)  # la propuesta con [COMPLETAR] no lleva botón
        self.assertTrue(botones[0].kwargs['botones'][0][0].startswith('faq:'))
        self.assertEqual(botones[0].kwargs['botones'][0][1], 'Aprobar')

    def test_aprobar_desde_direccion_la_activa(self):
        from comercial.models import PreguntaFrecuente
        self._aplicar()
        faq = PreguntaFrecuente.objects.get(pregunta='Pregunta 0')
        self._decir(TEL_DIR, f'faq:{faq.pk}:ok')
        faq.refresh_from_db()
        self.assertTrue(faq.activo)

    def test_una_con_completar_no_se_activa_y_se_avisa(self):
        from comercial.models import PreguntaFrecuente
        self._aplicar()
        faq = PreguntaFrecuente.objects.get(pregunta='Pregunta 1')
        with patch.object(services_agente, 'enviar_whatsapp', return_value=OK) as enviar:
            self._decir(TEL_DIR, f'faq:{faq.pk}:ok')
        faq.refresh_from_db()
        self.assertFalse(faq.activo)
        self.assertIn('falta completarla en el admin', enviar.call_args.kwargs['mensaje'])

    def test_desde_otro_numero_no_hace_nada(self):
        from comercial.models import PreguntaFrecuente
        self._aplicar()
        faq = PreguntaFrecuente.objects.get(pregunta='Pregunta 0')
        for tel in (TEL_ENC, TEL_CLIENTE):
            self._decir(tel, f'faq:{faq.pk}:ok')
        faq.refresh_from_db()
        self.assertFalse(faq.activo)


# ───────────────────────────── Punto 6 · «listo» ──────────────────────────────

@wa_settings(**AGENTE, META_WA_PLANTILLA_OPERACIONES='aviso_operaciones')
class ListoDelPersonalTest(MocksWA, TestCase):
    TEL_EMP = '525555550009'

    def setUp(self):
        super().setUp()
        from nomina.models import Empleado
        from operaciones.models import PlantillaChecklist
        _resp('ENCARGADO', TEL_ENC)
        self.hoy = timezone.localdate()
        self.empleado = Empleado.objects.create(nombre='Juan', telefono=self.TEL_EMP)
        self.plantilla = PlantillaChecklist.objects.create(nombre='Preparación', tipo='TURNOVER_EVENTO')
        patch.object(services_agente, 'enviar_whatsapp', return_value=OK).start()
        self.addCleanup(patch.stopall)

    def _tarea(self, limite=time(13, 0), fecha=None, responsable=True):
        from operaciones.models import TareaProgramada
        return TareaProgramada.objects.create(
            plantilla=self.plantilla, responsable=self.empleado if responsable else None,
            fecha=fecha or self.hoy, hora_entrada=time(8, 0), hora_limite=limite)

    def _decir(self, telefono, texto, n=[0]):
        n[0] += 1
        conv = services_agente.recibir_mensaje(telefono=telefono, nombre='X', texto=texto, wamid=f'l{n[0]}')
        agente = _cliente_falso(_respuesta(_texto('Hola.')))
        with patch.object(services_agente, '_cliente_ia', return_value=agente):
            services_agente._atender_pendientes(conv.pk)
        return agente

    def test_listo_completa_la_tarea_de_hoy_del_responsable(self):
        tarea = self._tarea()
        with patch.object(services_agente, 'enviar_whatsapp', return_value=OK) as enviar:
            agente = self._decir(self.TEL_EMP, ' Listo! ')
        tarea.refresh_from_db()
        self.assertIsNotNone(tarea.completada_en)
        self.assertEqual(tarea.completada_por_telefono, self.TEL_EMP)
        self.assertIn('quedó marcada como terminada', enviar.call_args.kwargs['mensaje'])
        agente.beta.messages.create.assert_not_called()

    def test_las_frases_fijas(self):
        for frase in ('ya', 'Terminé', 'LISTO'):
            tarea = self._tarea(fecha=self.hoy)
            self._decir(self.TEL_EMP, frase)
            tarea.refresh_from_db()
            self.assertIsNotNone(tarea.completada_en, frase)
            tarea.delete()

    def test_con_varias_tareas_marca_la_de_hora_limite_mas_proxima(self):
        tarde = self._tarea(limite=time(18, 0))
        self.plantilla.pk = None
        self.plantilla.nombre = 'Otra'
        self.plantilla.save()
        temprano = self._tarea(limite=time(11, 0))
        self._decir(self.TEL_EMP, 'listo')
        tarde.refresh_from_db()
        temprano.refresh_from_db()
        self.assertIsNotNone(temprano.completada_en)
        self.assertIsNone(tarde.completada_en)

    def test_solo_cuenta_tareas_de_hoy_sin_completar(self):
        ayer = self._tarea(fecha=self.hoy - timedelta(days=1))
        agente = self._decir(self.TEL_EMP, 'listo')
        ayer.refresh_from_db()
        self.assertIsNone(ayer.completada_en)
        self.assertIsNotNone(agente)

    def test_desde_un_numero_que_no_es_responsable_no_marca_nada(self):
        tarea = self._tarea()
        self._decir(TEL_CLIENTE, 'listo')
        tarea.refresh_from_db()
        self.assertIsNone(tarea.completada_en)

    def test_sin_listo_a_los_60_minutos_abre_un_pendiente_una_sola_vez(self):
        from operaciones.services import procesar_pendientes
        tarea = self._tarea(limite=time(13, 0))
        antes = datetime.combine(self.hoy, time(13, 59))
        despues = datetime.combine(self.hoy, time(14, 1))
        procesar_pendientes(antes)
        self.assertFalse(Pendiente.objects.filter(tipo='OTRO').exists())
        procesar_pendientes(despues)
        procesar_pendientes(despues + timedelta(minutes=10))
        p = Pendiente.objects.get(tipo='OTRO')
        self.assertEqual((p.severidad, p.rol, p.clave_idempotencia), ('NORMAL', 'ENCARGADO', f'tarea:{tarea.pk}:sin_listo'))
        self.assertIn('Juan', p.detalle)
        self.assertNotRegex(p.titulo, r'[\U0001F300-\U0001FAFF☀-➿]')

    def test_una_tarea_completada_no_abre_pendiente(self):
        from operaciones.services import procesar_pendientes
        self._tarea(limite=time(9, 0))
        self._decir(self.TEL_EMP, 'listo')
        procesar_pendientes(datetime.combine(self.hoy, time(23, 0)))
        self.assertFalse(Pendiente.objects.filter(tipo='OTRO').exists())
