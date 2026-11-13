"""Referidos, permiso de medios, reseñas de Google y sugerencias de contenido (Issue #468, fase 7)."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from comercial.models import (
    Cliente,
    Cotizacion,
    Descuento,
    DescuentoAplicado,
    ItemCotizacion,
    Pago,
    Producto,
    ProductoComponente,
    Temporada,
    TestimonioLanding,
)
from comercial.services_descuentos import DescuentoService
from comunicacion import services_encuesta
from comunicacion.models import BajaWhatsApp, EncuestaPostEvento, Pendiente
from legal.models import AceptacionLegal
from marketing.models import AutorizacionMedios, Lead, Referido, ResenaGoogle
from marketing.services import contenido, medios, referidos, resenas
from marketing.tests.test_pipeline import Base, hoy_mas

D = Decimal
TEL_A, TEL_B = '9991110001', '9992220002'


@override_settings(CONTRATO_AUTOMATICO=False)
class ReferidoBase(Base):
    """Un recomendador con beneficio GANADO y un helper para armar cotizaciones por concepto."""

    def setUp(self):
        self.recomendador = Cliente.objects.create(nombre='Rosa Díaz', telefono=TEL_A)
        self.otro = Cliente.objects.create(nombre='Luis Mena', telefono=TEL_B)
        self.renta = Producto.objects.create(nombre='Renta de la Quinta', rol_cotizador='BASE_EVENTO',
                                             precio_venta_fijo=D('3448.28'))
        self.basico = Producto.objects.create(nombre='Pasadía Básico', rol_cotizador='BASE_PASADIA_BASICO',
                                              precio_venta_fijo=D('1724.14'))
        self.habitacion = Producto.objects.create(nombre="Ka'an", rol_cotizador='HABITACION_HOSPEDAJE',
                                                  precio_venta_fijo=D('829.88'))
        self.extra = Producto.objects.create(nombre='Cerveza', precio_venta_fijo=D('1000'))
        self.persona_extra = Producto.objects.create(nombre='Persona extra', rol_cotizador='PERSONA_EXTRA_PASADIA',
                                                     precio_venta_fijo=D('155.17'))

    def referido(self, estado=Referido.GANADO, vence=None):
        ahora = timezone.now()
        return Referido.objects.create(
            recomendador=self.recomendador, estado=estado, ganado_en=ahora,
            vence_en=vence or ahora + timedelta(days=300))

    def cot(self, tipo, items, cliente=None):
        cot = Cotizacion.objects.create(cliente=cliente or self.recomendador, tipo_servicio=tipo,
                                        nombre_evento='X', fecha_evento=hoy_mas(90), num_personas=20,
                                        incluye_refrescos=False, incluye_cerveza=False)
        for producto, cantidad, precio in items:
            ItemCotizacion.objects.create(cotizacion=cot, producto=producto, descripcion=producto.nombre,
                                          cantidad=D(cantidad), precio_unitario=D(precio))
        cot.refresh_from_db()
        return cot


class CalculoDelBeneficioTest(ReferidoBase):
    def test_evento_20_por_ciento_solo_de_la_renta_nunca_de_extras(self):
        ref = self.referido()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28'), (self.extra, 3, '1000')])
        r = referidos.aplicar(cot)
        self.assertTrue(r.aplicado)
        self.assertEqual(r.monto, D('689.66'))  # 3448.28 x 20 % = 689.656 → ROUND_HALF_UP
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, D('689.66'))
        ref.refresh_from_db()
        self.assertEqual((ref.estado, ref.usado_en_cotizacion_id), (Referido.USADO, cot.pk))

    def test_evento_con_paquete_descuenta_la_renta_fija_y_no_el_paquete_ni_la_habitacion(self):
        paquete = Producto.objects.create(nombre='Paquete Fiesta', es_paquete=True, precio_venta_fijo=D('5000'))
        renta_fija = Producto.objects.create(nombre='Renta de la Quinta Ko\'ox', precio_venta_fijo=D('3448.28'))
        hab_fija = Producto.objects.create(nombre='Habitación Ka\'an', rol_cotizador='HABITACION_HOSPEDAJE',
                                           precio_venta_fijo=D('800'))
        ProductoComponente.objects.create(producto_padre=paquete, producto_hijo=renta_fija, cantidad=1,
                                          fijo_por_evento=True)
        ProductoComponente.objects.create(producto_padre=paquete, producto_hijo=hab_fija, cantidad=1,
                                          fijo_por_evento=True)
        self.referido()
        cot = self.cot('EVENTO', [(paquete, 12, '5000'), (renta_fija, 1, '3448.28'), (hab_fija, 1, '800')])
        r = referidos.aplicar(cot)
        self.assertEqual(r.monto, D('689.66'))

    def test_pasadia_10_por_ciento_del_paquete_no_de_la_persona_extra(self):
        self.referido()
        cot = self.cot('PASADIA', [(self.basico, 1, '1724.14'), (self.persona_extra, 5, '155.17')])
        r = referidos.aplicar(cot)
        self.assertEqual(r.monto, D('172.41'))  # 172.414

    def test_hospedaje_10_por_ciento_de_las_noches_de_habitacion(self):
        self.referido()
        cot = self.cot('HOSPEDAJE', [(self.habitacion, 3, '829.88'), (self.extra, 1, '1000')])
        r = referidos.aplicar(cot)
        self.assertEqual(r.monto, D('248.96'))  # 2489.64 x 10 % = 248.964

    def test_el_descuento_pasa_por_descuento_aplicado_y_baja_el_total_con_iva(self):
        self.referido()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        antes = cot.precio_final
        referidos.aplicar(cot)
        cot.refresh_from_db()
        aplicado = DescuentoAplicado.objects.get(cotizacion=cot)
        self.assertEqual((aplicado.modo_aplicacion, aplicado.activo), ('MANUAL', True))
        self.assertLess(cot.precio_final, antes)

    def test_sin_productos_de_renta_no_descuenta_toda_la_cotizacion(self):
        self.referido()
        self.renta.delete()
        cot = self.cot('EVENTO', [(self.extra, 1, '1000')])
        r = referidos.aplicar(cot)
        self.assertFalse(r.aplicado)
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, D('0.00'))

    def test_la_regla_no_aparece_entre_los_descuentos_manuales_del_admin(self):
        self.referido()
        referidos.aplicar(self.cot('EVENTO', [(self.renta, 1, '3448.28')]))
        regla = Descuento.objects.get(nombre='Referido Evento')
        self.assertFalse(regla.activo)
        self.assertEqual(set(regla.productos.values_list('pk', flat=True)), {self.renta.pk})


class NoAcumulableTest(ReferidoBase):
    def promo(self, valor, tipo='MONTO_FIJO'):
        return Descuento.objects.create(nombre='Promo', tipo_valor=tipo, valor=D(valor), modo='MANUAL', activo=True)

    def test_si_otra_promocion_ahorra_mas_el_referido_no_se_aplica_y_se_conserva(self):
        ref = self.referido()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28'), (self.extra, 5, '1000')])
        DescuentoService.aplicar(cot, self.promo('1000'))
        r = referidos.aplicar(cot)
        self.assertFalse(r.aplicado)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.GANADO)
        self.assertEqual(DescuentoAplicado.objects.filter(cotizacion=cot, activo=True).count(), 1)

    def test_si_el_referido_ahorra_mas_reemplaza_la_otra_promocion_sin_acumular(self):
        self.referido()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        DescuentoService.aplicar(cot, self.promo('100'))
        r = referidos.aplicar(cot)
        self.assertTrue(r.aplicado)
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, D('689.66'))
        self.assertEqual(DescuentoAplicado.objects.filter(cotizacion=cot, activo=True).count(), 1)
        self.assertEqual(DescuentoAplicado.objects.filter(cotizacion=cot, activo=False).count(), 1)

    def test_descuento_capturado_a_mano_no_se_pisa(self):
        ref = self.referido()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        Cotizacion.objects.filter(pk=cot.pk).update(descuento=D('50'))
        r = referidos.aplicar(cot)
        self.assertFalse(r.aplicado)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.GANADO)


class VigenciaYUnUsoTest(ReferidoBase):
    def test_vencido_no_se_aplica_y_el_barrido_lo_marca_vencido(self):
        ref = self.referido(vence=timezone.now() - timedelta(days=1))
        r = referidos.aplicar(self.cot('EVENTO', [(self.renta, 1, '3448.28')]))
        self.assertFalse(r.aplicado)
        self.assertEqual(referidos.vencer(), 1)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.VENCIDO)

    def test_ganar_fija_vigencia_de_12_meses(self):
        lead = Lead.objects.create(telefono=TEL_B, nombre='Luis', cliente=self.otro)
        ref = Referido.objects.create(recomendador=self.recomendador, recomendado_lead=lead)
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')], cliente=self.otro)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        cot.refresh_from_db()
        referidos.ganar_por_cotizacion(cot)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.GANADO)
        self.assertAlmostEqual((ref.vence_en - ref.ganado_en).days, 365, delta=1)

    def test_un_solo_uso(self):
        self.referido()
        referidos.aplicar(self.cot('EVENTO', [(self.renta, 1, '3448.28')]))
        r = referidos.aplicar(self.cot('EVENTO', [(self.renta, 1, '3448.28')]))
        self.assertFalse(r.aplicado)
        self.assertEqual(Referido.objects.filter(estado=Referido.USADO).count(), 1)

    def test_si_la_cotizacion_con_el_beneficio_expira_sin_confirmarse_se_libera(self):
        ref = self.referido()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        referidos.aplicar(cot)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='EXPIRADA')
        cot.refresh_from_db()
        self.assertEqual(referidos.liberar_si_no_se_concreto(cot), 1)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.GANADO)

    def test_confirmada_con_beneficio_ya_no_se_libera(self):
        ref = self.referido()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        referidos.aplicar(cot)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        cot.refresh_from_db()
        referidos.confirmar_uso(cot)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CANCELADA')
        cot.refresh_from_db()
        referidos.liberar_si_no_se_concreto(cot)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.USADO)


class AnulacionTest(ReferidoBase):
    def ganado_por(self):
        lead = Lead.objects.create(telefono=TEL_B, nombre='Luis', cliente=self.otro)
        ref = Referido.objects.create(recomendador=self.recomendador, recomendado_lead=lead)
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')], cliente=self.otro)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        cot.refresh_from_db()
        referidos.ganar_por_cotizacion(cot)
        ref.refresh_from_db()
        return ref, cot

    def test_cancelar_la_venta_recomendada_anula_el_beneficio_sin_usar(self):
        ref, cot = self.ganado_por()
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CANCELADA')
        cot.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            cot.save()
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.ANULADO)

    def test_un_reembolso_que_baja_del_anticipo_anula(self):
        ref, cot = self.ganado_por()
        Pago.objects.create(cotizacion=cot, monto=cot.precio_final, metodo='EFECTIVO', tipo='INGRESO')
        Pago.objects.create(cotizacion=cot, monto=cot.precio_final, metodo='EFECTIVO', tipo='REEMBOLSO')
        self.assertEqual(referidos.revisar_reembolso(cot), 1)
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.ANULADO)

    def test_un_beneficio_ya_usado_no_se_revoca(self):
        ref, cot = self.ganado_por()
        propia = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        referidos.aplicar(propia)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CANCELADA')
        referidos.anular_por_cotizacion(cot, 'cancelada')
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.USADO)

    def test_ganar_con_la_senal_al_confirmar_y_avisar(self):
        lead = Lead.objects.create(telefono=TEL_B, nombre='Luis', cliente=self.otro)
        ref = Referido.objects.create(recomendador=self.recomendador, recomendado_lead=lead)
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')], cliente=self.otro)
        cot.estado = 'CONFIRMADA'
        with patch('marketing.services.referidos.avisar_ganado') as aviso, self.captureOnCommitCallbacks(execute=True):
            cot.save()
        ref.refresh_from_db()
        self.assertEqual(ref.estado, Referido.GANADO)
        aviso.assert_called_once()

    def test_no_se_gana_si_el_recomendado_es_el_mismo_recomendador(self):
        lead = Lead.objects.create(telefono=TEL_A, nombre='Rosa', cliente=self.recomendador)
        Referido.objects.create(recomendador=self.recomendador, recomendado_lead=lead)
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        cot.refresh_from_db()
        self.assertEqual(referidos.ganar_por_cotizacion(cot), [])


class CapturaTest(ReferidoBase):
    def test_por_telefono_crea_referido_y_marca_la_fuente(self):
        lead = Lead.objects.create(telefono='9993330003', nombre='Nuevo')
        ref = referidos.registrar(lead, f'mi amiga Rosa, {TEL_A}')
        lead.refresh_from_db()
        self.assertEqual((ref.recomendador, ref.estado), (self.recomendador, Referido.REGISTRADO))
        self.assertEqual((lead.fuente, lead.recomendado_por), ('RECOMENDACION', self.recomendador))

    def test_solo_nombre_no_empareja_pero_se_guarda_lo_que_dijo(self):
        lead = Lead.objects.create(telefono='9993330003', nombre='Nuevo')
        self.assertIsNone(referidos.registrar(lead, 'Rosa Díaz'))
        lead.refresh_from_db()
        self.assertEqual(lead.recomendado_por_texto, 'Rosa Díaz')
        self.assertFalse(Referido.objects.exists())

    def test_nadie_se_recomienda_a_si_mismo(self):
        lead = Lead.objects.create(telefono=TEL_A, nombre='Rosa', cliente=self.recomendador)
        self.assertIsNone(referidos.registrar(lead, TEL_A))
        self.assertFalse(Referido.objects.exists())

    def test_idempotente_por_prospecto(self):
        lead = Lead.objects.create(telefono='9993330003', nombre='Nuevo')
        referidos.registrar(lead, TEL_A)
        referidos.registrar(lead, TEL_B)
        self.assertEqual(Referido.objects.count(), 1)

    def test_desde_la_cotizacion_del_cotizador(self):
        nuevo = Cliente.objects.create(nombre='Nuevo Cliente', telefono='9993330003')
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')], cliente=nuevo)
        with self.captureOnCommitCallbacks(execute=True):
            referidos.registrar_desde_cotizacion(cot, TEL_A)
        self.assertEqual(Referido.objects.get().recomendador, self.recomendador)

    def test_el_formulario_del_cotizador_acepta_el_campo_opcional(self):
        from comercial.forms_cotizador import CotizadorEnviarForm
        form = CotizadorEnviarForm({'quien_recomendo': 'Rosa'})
        form.is_valid()
        self.assertEqual(form.cleaned_data['quien_recomendo'], 'Rosa')


class AvisosTest(ReferidoBase):
    def ganado(self):
        return self.referido()

    def aceptar_marketing(self):
        AceptacionLegal.objects.create(cliente=self.recomendador, finalidades_aceptadas=['MARKETING'],
                                       aceptado_en=timezone.now() - timedelta(days=10))

    @override_settings(META_WA_PLANTILLA_REFERIDO='beneficio_referido')
    def test_sale_por_plantilla_fuera_de_ventana_con_consentimiento(self):
        self.aceptar_marketing()
        ref = self.ganado()
        with patch('comunicacion.services.enviar_whatsapp_template') as plantilla, \
                patch('comunicacion.services_pendientes.ventana_abierta', return_value=False):
            plantilla.return_value = Mock(estado='ENVIADO')
            self.assertTrue(referidos.avisar_ganado(ref))
        self.assertEqual(plantilla.call_args.kwargs['template_name'], 'beneficio_referido')
        ref.refresh_from_db()
        self.assertIsNotNone(ref.notificado_en)

    def test_con_baja_no_se_manda_nada_y_queda_pendiente(self):
        self.aceptar_marketing()
        BajaWhatsApp.objects.create(telefono='529991110001', origen='PALABRA')
        ref = self.ganado()
        with patch('comunicacion.services.enviar_whatsapp') as texto, \
                patch('comunicacion.services.enviar_whatsapp_template') as plantilla:
            self.assertFalse(referidos.avisar_ganado(ref))
        texto.assert_not_called()
        plantilla.assert_not_called()
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'referido:{ref.pk}:avisar').exists())

    def test_sin_consentimiento_de_marketing_tampoco(self):
        ref = self.ganado()
        with patch('comunicacion.services.enviar_whatsapp') as texto:
            self.assertFalse(referidos.avisar_ganado(ref))
        texto.assert_not_called()

    def test_al_crear_cotizacion_de_quien_tiene_beneficio_se_abre_un_pendiente_y_no_se_aplica(self):
        self.ganado()
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        referidos.avisar_beneficio_disponible(cot)
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'referido:disponible:{cot.pk}').exists())
        cot.refresh_from_db()
        self.assertEqual(cot.descuento, D('0.00'))

    def test_la_invitacion_a_recomendar_va_en_la_respuesta_a_la_encuesta_4_o_5(self):
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        enc = EncuestaPostEvento.objects.create(cotizacion=cot, telefono='529991110001')
        texto = services_encuesta._registrar_calificacion(enc, 5)
        self.assertIn('recomiéndanos', texto)
        enc2 = EncuestaPostEvento.objects.create(cotizacion=self.cot('PASADIA', [(self.basico, 1, '1724.14')]),
                                                 telefono='529991110001')
        self.assertNotIn('recomiéndanos', services_encuesta._registrar_calificacion(enc2, 2))


class MediosTest(ReferidoBase):
    def nuevo_testimonio(self, cot, activo=True):
        return TestimonioLanding(nombre='Rosa', evento='Boda', texto='Todo excelente', cotizacion=cot, activo=activo)

    def test_no_se_publica_un_testimonio_ligado_a_un_cliente_sin_autorizacion(self):
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        with self.assertRaises(ValidationError):
            self.nuevo_testimonio(cot).full_clean()
        self.nuevo_testimonio(cot, activo=False).full_clean()  # inactivo sí
        TestimonioLanding(nombre='Anónimo', evento='Boda', texto='Bien').full_clean()  # sin vínculo, sin cambios

    def test_con_autorizacion_vigente_se_puede_publicar(self):
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        medios.registrar(cliente=self.recomendador, cotizacion=cot, alcances=['TESTIMONIO', 'NOMBRE'],
                         origen='ADMIN', texto='Autoriza')
        self.nuevo_testimonio(cot).full_clean()

    def test_alcance_insuficiente_bloquea(self):
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        medios.registrar(cliente=self.recomendador, cotizacion=cot, alcances=['FOTOS'], origen='ADMIN', texto='x')
        with self.assertRaises(ValidationError):
            self.nuevo_testimonio(cot).full_clean()

    def test_revocar_desactiva_lo_publicado_y_no_borra(self):
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        a = medios.registrar(cliente=self.recomendador, cotizacion=cot, alcances=['TESTIMONIO', 'NOMBRE'],
                             origen='ADMIN', texto='x')
        t = self.nuevo_testimonio(cot)
        t.save()
        self.assertEqual(medios.revocar(a, motivo='cambió de opinión'), 1)
        t.refresh_from_db()
        a.refresh_from_db()
        self.assertFalse(t.activo)
        self.assertEqual(a.estado, AutorizacionMedios.REVOCADA)
        self.assertEqual(medios.faltantes(cot.pk, ['TESTIMONIO']), ['Testimonio'])
        with self.assertRaises(ValidationError):
            a.delete()

    def test_la_evidencia_guarda_el_hash_del_texto(self):
        from hashlib import sha256
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        a = medios.registrar(cliente=self.recomendador, cotizacion=cot, origen='ADMIN', texto='Texto exacto')
        self.assertEqual(a.texto_sha256, sha256(b'Texto exacto').hexdigest())

    def test_alcances_invalidos_se_rechazan(self):
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        with self.assertRaises(ValidationError):
            medios.registrar(cliente=self.recomendador, cotizacion=cot, alcances=['TODO'], origen='ADMIN', texto='x')

    def prueba_whatsapp(self, ventana=True):
        AceptacionLegal.objects.create(cliente=self.recomendador, finalidades_aceptadas=['MARKETING'],
                                       aceptado_en=timezone.now() - timedelta(days=10))
        cot = self.cot('EVENTO', [(self.renta, 1, '3448.28')])
        with patch('marketing.services.medios.enviar_whatsapp_botones') as botones, \
                patch('comunicacion.services_pendientes.ventana_abierta', return_value=ventana):
            a = medios.solicitar_por_whatsapp(cot)
        return cot, a, botones

    def test_solicitud_por_whatsapp_con_botones_y_una_sola_vez(self):
        cot, a, botones = self.prueba_whatsapp()
        self.assertEqual(a.estado, AutorizacionMedios.SOLICITADA)
        ids = [b[0] for b in botones.call_args.kwargs['botones']]
        self.assertEqual(ids, [f'med:{a.pk}:si', f'med:{a.pk}:no'])
        with patch('marketing.services.medios.enviar_whatsapp_botones') as otra, \
                patch('comunicacion.services_pendientes.ventana_abierta', return_value=True):
            self.assertIsNone(medios.solicitar_por_whatsapp(cot))
        otra.assert_not_called()

    def test_sin_ventana_no_se_solicita(self):
        _, a, botones = self.prueba_whatsapp(ventana=False)
        self.assertIsNone(a)
        botones.assert_not_called()

    def test_boton_si_autoriza_con_el_wamid_y_otro_numero_se_descarta(self):
        cot, a, _ = self.prueba_whatsapp()
        self.assertIsNone(medios.atender_boton('529990000000', f'med:{a.pk}:si', 'wamid.X'))
        a.refresh_from_db()
        self.assertEqual(a.estado, AutorizacionMedios.SOLICITADA)
        self.assertIn('Gracias', medios.atender_boton('529991110001', f'med:{a.pk}:si', 'wamid.X'))
        a.refresh_from_db()
        self.assertEqual((a.estado, a.referencia_externa), (AutorizacionMedios.AUTORIZADA, 'wamid.X'))
        self.assertEqual(medios.faltantes(cot.pk, ['FOTOS']), [])

    def test_boton_no_rechaza(self):
        _, a, _ = self.prueba_whatsapp()
        medios.atender_boton('529991110001', f'med:{a.pk}:no')
        a.refresh_from_db()
        self.assertEqual(a.estado, AutorizacionMedios.RECHAZADA)


def respuesta(cuerpo, status=200):
    r = Mock()
    r.status_code = status
    r.json.return_value = cuerpo
    return r


GOOGLE = dict(GOOGLE_BUSINESS_CLIENT_ID='id', GOOGLE_BUSINESS_CLIENT_SECRET='secreto',
              GOOGLE_BUSINESS_REFRESH_TOKEN='refresco', GOOGLE_BUSINESS_LOCATION='accounts/1/locations/2')


def reseña(id_, estrellas, dias=1, respondida=False):
    fecha = (timezone.now() - timedelta(days=dias)).isoformat()
    datos = {'reviewId': id_, 'starRating': estrellas, 'createTime': fecha, 'updateTime': fecha,
             'reviewer': {'displayName': 'No se guarda'}, 'comment': 'No se guarda'}
    if respondida:
        datos['reviewReply'] = {'comment': 'gracias'}
    return datos


class ResenasTest(TestCase):
    def test_apagado_sin_credenciales(self):
        with override_settings(GOOGLE_BUSINESS_CLIENT_ID=''), patch('marketing.services.resenas.requests') as red:
            self.assertTrue(resenas.sincronizar()['apagado'])
        red.post.assert_not_called()

    @override_settings(**GOOGLE)
    def test_cliente_rest_pide_token_y_lista(self):
        with patch('marketing.services.resenas.requests') as red:
            red.post.return_value = respuesta({'access_token': 'tk'})
            red.get.return_value = respuesta({'reviews': [reseña('r1', 'ONE')]})
            r = resenas.sincronizar()
        self.assertEqual(r['nuevas'], 1)
        self.assertIn('locations/2/reviews', red.get.call_args.args[0])
        self.assertEqual(red.get.call_args.kwargs['headers'], {'Authorization': 'Bearer tk'})

    @override_settings(**GOOGLE)
    def test_error_de_google_lanza_googleerror_sin_filtrar_el_token(self):
        with patch('marketing.services.resenas.requests') as red:
            red.post.return_value = respuesta({}, status=401)
            with self.assertRaises(resenas.GoogleError) as cm:
                resenas.listar_resenas()
        self.assertNotIn('refresco', str(cm.exception))

    @override_settings(**GOOGLE)
    def test_error_al_listar_incluye_el_motivo_de_google(self):
        with patch('marketing.services.resenas.requests') as red:
            red.post.return_value = respuesta({'access_token': 'tk'})
            red.get.return_value = respuesta(
                {'error': {'status': 'PERMISSION_DENIED', 'message': 'API deshabilitada'}}, status=403)
            with self.assertRaises(resenas.GoogleError) as cm:
                resenas.listar_resenas()
        self.assertIn('HTTP 403', str(cm.exception))
        self.assertIn('PERMISSION_DENIED: API deshabilitada', str(cm.exception))
        self.assertNotIn('tk', str(cm.exception).replace('PERMISSION', ''))

    def test_tres_estrellas_o_menos_abren_pendiente_urgente_idempotente(self):
        datos = [reseña('malo', 'TWO'), reseña('regular', 'THREE'), reseña('bueno', 'FOUR'), reseña('top', 'FIVE')]
        r1 = resenas.sincronizar(resenas=datos)
        r2 = resenas.sincronizar(resenas=datos)
        self.assertEqual((r1['avisos'], r2['avisos']), (2, 0))
        pendientes = Pendiente.objects.filter(clave_idempotencia__startswith='resena_google:')
        self.assertEqual(pendientes.count(), 2)
        self.assertTrue(all(p.severidad == 'URGENTE' and p.rol == 'DIRECCION' for p in pendientes))
        self.assertEqual(ResenaGoogle.objects.count(), 4)

    def test_respondidas_y_viejas_no_avisan(self):
        resenas.sincronizar(resenas=[reseña('a', 'ONE', respondida=True), reseña('b', 'ONE', dias=60)])
        self.assertFalse(Pendiente.objects.filter(clave_idempotencia__startswith='resena_google:').exists())

    def test_no_se_guarda_ni_el_nombre_ni_el_texto(self):
        resenas.sincronizar(resenas=[reseña('a', 'ONE')])
        campos = {f.name for f in ResenaGoogle._meta.get_fields()}
        self.assertFalse(campos & {'nombre', 'texto', 'comentario', 'autor'})
        pendiente = Pendiente.objects.get(clave_idempotencia='resena_google:a')
        self.assertNotIn('No se guarda', pendiente.detalle + pendiente.titulo)


class ContenidoTest(Base):
    def hoy(self):
        # un lunes fijo para que los fines de semana sean predecibles
        hoy = timezone.localdate()
        return hoy + timedelta(days=(7 - hoy.weekday()) % 7 or 7)

    def test_sabados_libres_sugieren_eventos_sociales_los_mas_cercanos_primero(self):
        lunes = self.hoy()
        sugs = contenido.sugerencias(lunes)
        self.assertEqual(len(sugs), 3)
        self.assertTrue(all(s['servicio'] == 'EVENTO' for s in sugs))
        self.assertEqual(sugs[0]['fecha'], lunes + timedelta(days=5))
        self.assertEqual([s['fecha'] for s in sugs], sorted(s['fecha'] for s in sugs))

    def test_un_sabado_ocupado_no_se_sugiere_y_el_domingo_libre_va_a_pasadia(self):
        lunes = self.hoy()
        sabado = lunes + timedelta(days=5)
        cliente = Cliente.objects.create(nombre='Ana', telefono='9990001111')
        cot = Cotizacion.objects.create(cliente=cliente, tipo_servicio='EVENTO', nombre_evento='Boda',
                                        fecha_evento=sabado)
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        sugs = contenido.sugerencias(lunes, maximo=10)
        self.assertNotIn(sabado, [s['fecha'] for s in sugs if s['servicio'] == 'EVENTO'])
        self.assertIn(('PASADIA', sabado + timedelta(days=1)), [(s['servicio'], s['fecha']) for s in sugs])

    def test_temporada_proxima_se_sugiere(self):
        lunes = self.hoy()
        Temporada.objects.create(nombre='Fiestas de fin de año', fecha_inicio=lunes + timedelta(days=10),
                                 fecha_fin=lunes + timedelta(days=30), anio=lunes.year)
        textos = [s['texto'] for s in contenido.sugerencias(lunes, maximo=20)]
        self.assertTrue(any('Fiestas de fin de año' in t for t in textos))

    def test_fuera_del_horizonte_no_hay_sugerencias(self):
        from marketing.services import decisiones
        libres = {'libres': []}
        with patch.object(contenido, 'agenda', return_value=libres):
            self.assertEqual(contenido.sugerencias(self.hoy()), [])
        self.assertTrue(hasattr(decisiones, 'agenda'))

    def test_van_en_el_resumen_del_lunes(self):
        from comunicacion.services_direccion import _bloque_marketing
        bloque = _bloque_marketing(self.hoy())
        self.assertTrue(any(l.startswith('Qué promover:') for l in bloque['lineas']))
        self.assertLessEqual(sum(l.startswith('Qué promover:') for l in bloque['lineas']), 3)
