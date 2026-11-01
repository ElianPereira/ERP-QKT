"""Embudo de prospectos: identidad, etapas, reversas, reapertura y fuente."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from comercial.models import Cliente, Cotizacion
from comunicacion import services_agente
from comunicacion.models import ConversacionWhatsApp, MensajeWhatsApp, Responsable, Visita
from marketing.models import Lead
from marketing.services import pipeline

TEL = '525555550101'
TEL_EQUIPO = '525555550199'


def hoy_mas(dias):
    return (timezone.localdate() + timedelta(days=dias))


class Base(TestCase):
    def mensaje(self, texto='Hola', wamid=None, referral=None, tel=TEL):
        with self.captureOnCommitCallbacks(execute=True):
            return services_agente.recibir_mensaje(
                telefono=tel, nombre='Ana', texto=texto, wamid=wamid or f'w{MensajeWhatsApp.objects.count()}',
                referral=referral)

    def respuesta(self, direccion='AGENTE', texto='Con gusto te ayudo', tel=TEL):
        conv = ConversacionWhatsApp.objects.get(telefono=tel)
        with self.captureOnCommitCallbacks(execute=True):
            return MensajeWhatsApp.objects.create(conversacion=conv, direccion=direccion, texto=texto)

    def cliente(self, telefono=TEL, **kw):
        return Cliente.objects.create(nombre='Ana Pérez', telefono=telefono, **kw)

    def cotizacion(self, cliente, estado='BORRADOR', **kw):
        datos = dict(cliente=cliente, tipo_servicio='EVENTO', nombre_evento='Boda', fecha_evento=hoy_mas(60))
        datos.update(kw)
        with self.captureOnCommitCallbacks(execute=True):
            cot = Cotizacion.objects.create(**datos)
        if estado != 'BORRADOR':
            self.estado(cot, estado)
        return cot

    def estado(self, cot, estado):
        Cotizacion.objects.filter(pk=cot.pk).update(estado=estado)
        cot.refresh_from_db()

    def lead(self):
        return Lead.objects.get()


class CreacionTest(Base):
    def test_primer_mensaje_crea_nuevo_con_fuente_whatsapp(self):
        self.mensaje()
        lead = self.lead()
        self.assertEqual((lead.etapa, lead.fuente), ('NUEVO', 'WHATSAPP'))
        self.assertEqual(lead.eventos.filter(tipo='CREADO').count(), 1)

    def test_anuncio_de_meta_marca_la_fuente(self):
        self.mensaje(referral={'source_id': 'AD1', 'headline': 'Bodas', 'ctwa_clid': 'clid'})
        lead = self.lead()
        self.assertEqual(lead.fuente, 'META_ANUNCIO')
        self.assertEqual((lead.anuncio_id, lead.anuncio_titulo), ('AD1', 'Bodas'))

    def test_anuncio_que_llega_despues_mejora_la_fuente(self):
        self.mensaje()
        conv = ConversacionWhatsApp.objects.get()
        conv.anuncio_id, conv.anuncio_titulo = 'AD2', 'Pasadía'
        with self.captureOnCommitCallbacks(execute=True):
            conv.save(update_fields=['anuncio_id', 'anuncio_titulo', 'updated_at'])
        lead = self.lead()
        self.assertEqual((lead.fuente, lead.anuncio_id), ('META_ANUNCIO', 'AD2'))
        self.assertTrue(lead.eventos.filter(tipo='FUENTE').exists())

    def test_anuncio_de_una_conversacion_vieja_no_cuenta(self):
        self.mensaje(referral={'source_id': 'AD1'})
        ConversacionWhatsApp.objects.update(created_at=timezone.now() - timedelta(days=400))
        Lead.objects.update(etapa='PERDIDO', etapa_cambiada_en=timezone.now() - timedelta(days=200))
        self.mensaje(texto='Hola de nuevo')
        nuevo = Lead.objects.exclude(etapa='PERDIDO').get()
        self.assertEqual(nuevo.fuente, 'WHATSAPP')

    def test_webhook_duplicado_no_duplica(self):
        self.mensaje(wamid='igual')
        self.mensaje(wamid='igual')
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(self.lead().eventos.count(), 1)

    def test_numero_del_directorio_no_genera_prospecto(self):
        Responsable.objects.create(rol='ENCARGADO', nombre='Encargado', telefono_wa=TEL_EQUIPO)
        self.mensaje(tel=TEL_EQUIPO)
        self.assertEqual(Lead.objects.count(), 0)

    def test_cliente_que_vuelve_no_se_duplica(self):
        self.mensaje()
        self.mensaje(texto='Sigo interesada')
        cliente = self.cliente()
        self.cotizacion(cliente)
        self.assertEqual(Lead.objects.count(), 1)
        lead = self.lead()
        self.assertEqual(lead.cliente, cliente)
        self.assertEqual(lead.etapa, 'COTIZADO')

    def test_cotizacion_web_sin_mensajes_crea_prospecto_con_su_fuente(self):
        cot = self.cotizacion(self.cliente(), como_nos_encontro='Visité la quinta')
        lead = self.lead()
        self.assertEqual((lead.etapa, lead.fuente, lead.tipo_servicio), ('COTIZADO', 'VISITA_QUINTA', 'EVENTO'))
        self.assertEqual(lead.cotizacion, cot)

    def test_cotizacion_historica_se_ignora(self):
        cot = Cotizacion(cliente=self.cliente(), nombre_evento='Viejo', fecha_evento=hoy_mas(-300))
        cot.es_historica = True
        with self.captureOnCommitCallbacks(execute=True):
            cot.save()
        self.assertEqual(Lead.objects.count(), 0)

    def test_fuente_generica_se_reemplaza_con_dato_mejor(self):
        self.mensaje()
        self.assertEqual(self.lead().fuente, 'WHATSAPP')
        self.cotizacion(self.cliente(), como_nos_encontro='Instagram')
        self.assertEqual(self.lead().fuente, 'INSTAGRAM')


class EtapasTest(Base):
    def test_avance_completo(self):
        self.mensaje()
        self.assertEqual(self.lead().etapa, 'NUEVO')
        self.respuesta()
        self.assertEqual(self.lead().etapa, 'CONTACTADO')
        conv = ConversacionWhatsApp.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            Visita.objects.create(conversacion=conv, nombre='Ana', telefono=TEL, inicio=timezone.now() + timedelta(days=2))
        self.assertEqual(self.lead().etapa, 'VISITA_AGENDADA')
        cot = self.cotizacion(self.cliente())
        self.assertEqual(self.lead().etapa, 'COTIZADO')
        self.estado(cot, 'CONFIRMADA')
        pipeline.sincronizar(self.lead())
        lead = self.lead()
        self.assertEqual((lead.etapa, lead.cotizacion), ('GANADO', cot))
        self.assertEqual(list(lead.eventos.filter(tipo='ETAPA').values_list('etapa_nueva', flat=True)),
                         ['CONTACTADO', 'VISITA_AGENDADA', 'COTIZADO', 'GANADO'])

    def test_aviso_de_bienvenida_no_cuenta_como_contacto(self):
        self.mensaje()
        self.respuesta(texto=services_agente.AVISO_INICIAL)
        self.assertEqual(self.lead().etapa, 'NUEVO')

    def test_etapa_no_retrocede_si_la_visita_se_cancela(self):
        self.mensaje()
        conv = ConversacionWhatsApp.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            v = Visita.objects.create(conversacion=conv, nombre='Ana', telefono=TEL,
                                      inicio=timezone.now() + timedelta(days=2))
        v.estado = 'CANCELADA'
        with self.captureOnCommitCallbacks(execute=True):
            v.save()
        self.assertEqual(self.lead().etapa, 'VISITA_AGENDADA')

    def test_reversa_por_cancelacion(self):
        cot = self.cotizacion(self.cliente(), estado='CONFIRMADA')
        pipeline.sincronizar(self.lead())
        self.assertEqual(self.lead().etapa, 'GANADO')
        self.estado(cot, 'CANCELADA')
        pipeline.sincronizar(self.lead())
        lead = self.lead()
        self.assertEqual((lead.etapa, lead.motivo_perdida, lead.motivo_perdida_origen),
                         ('PERDIDO', 'CANCELO', 'SISTEMA'))

    def test_perdida_por_cotizacion_expirada(self):
        cot = self.cotizacion(self.cliente())
        self.estado(cot, 'EXPIRADA')
        pipeline.sincronizar(self.lead())
        lead = self.lead()
        self.assertEqual((lead.etapa, lead.motivo_perdida), ('PERDIDO', ''))

    def test_perdida_por_quince_dias_sin_interaccion(self):
        self.mensaje()
        lead = self.lead()
        pipeline.sincronizar(lead, ahora=timezone.now() + timedelta(days=14))
        self.assertEqual(self.lead().etapa, 'NUEVO')
        pipeline.sincronizar(lead, ahora=timezone.now() + timedelta(days=16))
        self.assertEqual(self.lead().etapa, 'PERDIDO')

    def test_visita_futura_evita_la_perdida_por_silencio(self):
        self.mensaje()
        conv = ConversacionWhatsApp.objects.get()
        with self.captureOnCommitCallbacks(execute=True):
            Visita.objects.create(conversacion=conv, nombre='Ana', telefono=TEL,
                                  inicio=timezone.now() + timedelta(days=30))
        pipeline.sincronizar(self.lead(), ahora=timezone.now() + timedelta(days=20))
        self.assertEqual(self.lead().etapa, 'VISITA_AGENDADA')

    def test_sincronizar_es_idempotente(self):
        self.mensaje()
        self.respuesta()
        lead = self.lead()
        total = lead.eventos.count()
        pipeline.sincronizar(lead)
        pipeline.sincronizar(lead)
        self.assertEqual(lead.eventos.count(), total)


class ReaperturaTest(Base):
    def perder(self, dias_atras):
        self.mensaje()
        lead = self.lead()
        Lead.objects.filter(pk=lead.pk).update(
            etapa='PERDIDO', motivo_perdida='PRECIO', motivo_perdida_origen='KOOXI',
            etapa_cambiada_en=timezone.now() - timedelta(days=dias_atras))
        return lead

    def test_menos_de_90_dias_reabre(self):
        lead = self.perder(30)
        self.mensaje(texto='Ya lo pensé mejor')
        self.assertEqual(Lead.objects.count(), 1)
        lead.refresh_from_db()
        self.assertEqual((lead.etapa, lead.motivo_perdida), ('NUEVO', ''))
        evento = lead.eventos.get(tipo='REAPERTURA')
        self.assertEqual(evento.detalle['motivo_anterior'], 'PRECIO')

    def test_cotizaciones_muertas_no_cuentan_al_reabrir(self):
        cot = self.cotizacion(self.cliente())
        self.estado(cot, 'EXPIRADA')
        pipeline.sincronizar(self.lead())
        Lead.objects.update(etapa_cambiada_en=timezone.now() - timedelta(days=10))
        self.mensaje()
        lead = self.lead()
        self.assertEqual(lead.etapa, 'NUEVO')

    def test_mas_de_90_dias_crea_uno_nuevo(self):
        self.perder(120)
        self.mensaje(texto='Hola otra vez')
        self.assertEqual(Lead.objects.count(), 2)
        self.assertEqual(Lead.objects.exclude(etapa='PERDIDO').count(), 1)

    def test_ganado_con_servicio_vivo_no_crea(self):
        cot = self.cotizacion(self.cliente(), estado='CONFIRMADA', fecha_evento=hoy_mas(20))
        pipeline.sincronizar(self.lead())
        self.mensaje(texto='Una duda de mi evento')
        self.assertEqual(Lead.objects.count(), 1)
        self.assertEqual(Lead.objects.get().cotizacion, cot)

    def test_ganado_con_servicio_terminado_hace_mas_de_30_dias_crea_nuevo_recurrente(self):
        self.cotizacion(self.cliente(), estado='EJECUTADA', fecha_evento=hoy_mas(-45))
        pipeline.sincronizar(self.lead())
        self.assertEqual(self.lead().etapa, 'GANADO')
        self.mensaje(texto='Quiero otro evento')
        self.assertEqual(Lead.objects.count(), 2)
        nuevo = Lead.objects.exclude(etapa='GANADO').get()
        self.assertEqual(nuevo.fuente, 'RECURRENTE')

    def test_contacto_anterior_al_cierre_no_reabre(self):
        lead = self.perder(0)
        antes = lead.etapa_cambiada_en
        pipeline.registrar_contacto(telefono=TEL, momento=timezone.now() - timedelta(days=1))
        lead.refresh_from_db()
        self.assertEqual(lead.etapa, 'PERDIDO')
        self.assertEqual(Lead.objects.count(), 1)
        self.assertIsNotNone(antes)
