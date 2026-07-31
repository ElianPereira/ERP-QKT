"""Tests del módulo legal: versionado, evidencia de consentimiento y ARCO."""

from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from comercial.models import Cliente
from legal.models import (
    AceptacionLegal, DocumentoLegal, EstadoARCO, Finalidad, OrigenAceptacion,
    SolicitudARCO, TipoARCO, TipoDocumento,
)
from legal.services import LegalService


def _doc(tipo, version='1.0', contenido='Contenido de prueba', vigente=True):
    return DocumentoLegal.objects.create(
        tipo=tipo, version=version, titulo=f'Doc {tipo}',
        contenido_md=contenido, vigente_desde=date.today(), vigente=vigente,
    )


def _documentos_obligatorios():
    _doc(TipoDocumento.AVISO_PRIVACIDAD, contenido='Aviso v1')
    _doc(TipoDocumento.TERMINOS, contenido='Terminos v1')
    _doc(TipoDocumento.POLITICA_CANCELACION, contenido='Politica v1')


class DocumentoLegalTest(TestCase):

    def test_hash_se_calcula_solo(self):
        doc = _doc(TipoDocumento.TERMINOS, contenido='Texto exacto')
        self.assertEqual(doc.hash_contenido,
                         DocumentoLegal.calcular_hash('Texto exacto'))
        self.assertEqual(len(doc.hash_contenido), 64)

    def test_editar_contenido_publicado_lanza_error(self):
        doc = _doc(TipoDocumento.TERMINOS)
        doc.contenido_md = 'Otro contenido'
        with self.assertRaises(ValidationError):
            doc.full_clean()

    def test_publicar_version_nueva_desmarca_la_anterior(self):
        v1 = _doc(TipoDocumento.TERMINOS, version='1.0')
        v2 = _doc(TipoDocumento.TERMINOS, version='2.0', contenido='v2')
        v1.refresh_from_db()
        self.assertFalse(v1.vigente)
        self.assertTrue(v2.vigente)

    def test_bd_impide_dos_vigentes_del_mismo_tipo(self):
        _doc(TipoDocumento.TERMINOS, version='1.0')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DocumentoLegal.objects.bulk_create([DocumentoLegal(
                    tipo=TipoDocumento.TERMINOS, version='9.9', titulo='x',
                    contenido_md='y', hash_contenido='z',
                    vigente_desde=date.today(), vigente=True,
                )])

    def test_delete_lanza_error(self):
        doc = _doc(TipoDocumento.TERMINOS)
        with self.assertRaises(ValidationError):
            doc.delete()

    def test_no_publica_con_marcadores_pendientes(self):
        """Un [CONFIRMAR:] sin resolver saldría tal cual al cliente."""
        with self.assertRaises(ValidationError):
            _doc(TipoDocumento.TERMINOS,
                 contenido='Teléfono: [CONFIRMAR: +52 999 XXX XXXX]')

    def test_permite_guardar_sin_publicar_con_marcadores(self):
        doc = _doc(TipoDocumento.TERMINOS,
                   contenido='Correo: [CONFIRMAR: legal@x.com]', vigente=False)
        self.assertEqual(len(doc.marcadores_pendientes()), 1)
        self.assertFalse(doc.vigente)


class AceptacionTest(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        _documentos_obligatorios()
        Finalidad.objects.create(clave='MARKETING', nombre='Promociones',
                                 requiere_consentimiento=True)
        Finalidad.objects.create(clave='USO_IMAGEN', nombre='Uso de imagen',
                                 requiere_consentimiento=True)
        Finalidad.objects.create(clave='OPERACION', nombre='Operación',
                                 requiere_consentimiento=False)

    def _request(self, **meta):
        req = self.factory.post('/cotizar/enviar/')
        req.META.update(meta)
        return req

    def test_congela_el_hash_de_los_tres_documentos(self):
        ac = LegalService.registrar_aceptacion(
            request=self._request(), correo='a@b.com',
            origen=OrigenAceptacion.FORM_COTIZACION,
        )
        self.assertEqual(len(ac.snapshot_documentos), 3)
        for entrada in ac.snapshot_documentos:
            doc = DocumentoLegal.objects.get(tipo=entrada['tipo'], vigente=True)
            self.assertEqual(entrada['hash'], doc.hash_contenido)

    def test_falta_documento_obligatorio_lanza_error(self):
        DocumentoLegal.objects.filter(tipo=TipoDocumento.TERMINOS).update(vigente=False)
        with self.assertRaises(RuntimeError):
            LegalService.registrar_aceptacion(
                request=self._request(), correo='a@b.com',
                origen=OrigenAceptacion.CHECKOUT,
            )

    def test_rechazadas_es_el_complemento_de_las_aceptadas(self):
        ac = LegalService.registrar_aceptacion(
            request=self._request(), correo='a@b.com',
            origen=OrigenAceptacion.FORM_COTIZACION,
            finalidades_aceptadas=['MARKETING'],
        )
        self.assertEqual(ac.finalidades_aceptadas, ['MARKETING'])
        self.assertEqual(ac.finalidades_rechazadas, ['USO_IMAGEN'])

    def test_finalidad_sin_consentimiento_no_entra_en_ninguna_lista(self):
        ac = LegalService.registrar_aceptacion(
            request=self._request(), correo='a@b.com',
            origen=OrigenAceptacion.FORM_COTIZACION,
            finalidades_aceptadas=['OPERACION', 'MARKETING'],
        )
        self.assertNotIn('OPERACION', ac.finalidades_aceptadas)
        self.assertNotIn('OPERACION', ac.finalidades_rechazadas)

    def test_aceptacion_es_inmutable(self):
        ac = LegalService.registrar_aceptacion(
            request=self._request(), correo='a@b.com',
            origen=OrigenAceptacion.CHECKOUT,
        )
        ac.correo = 'otro@b.com'
        with self.assertRaises(ValidationError):
            ac.save()
        with self.assertRaises(ValidationError):
            ac.delete()

    def test_ip_toma_el_primer_valor_de_x_forwarded_for(self):
        req = self._request(HTTP_X_FORWARDED_FOR='189.1.2.3, 10.0.0.1',
                            REMOTE_ADDR='10.0.0.1')
        self.assertEqual(LegalService.obtener_ip(req), '189.1.2.3')

    def test_publicar_version_nueva_no_altera_snapshot_previo(self):
        ac = LegalService.registrar_aceptacion(
            request=self._request(), correo='a@b.com',
            origen=OrigenAceptacion.FORM_COTIZACION,
        )
        hashes_antes = sorted(e['hash'] for e in ac.snapshot_documentos)

        _doc(TipoDocumento.TERMINOS, version='3.0', contenido='Terminos v3')
        ac.refresh_from_db()
        self.assertEqual(sorted(e['hash'] for e in ac.snapshot_documentos),
                         hashes_antes)

    def test_cliente_acepto(self):
        cliente = Cliente.objects.create(nombre='X', tipo_persona='FISICA')
        LegalService.registrar_aceptacion(
            request=self._request(), correo='a@b.com', cliente=cliente,
            origen=OrigenAceptacion.FORM_COTIZACION,
            finalidades_aceptadas=['MARKETING'],
        )
        self.assertTrue(LegalService.cliente_acepto(cliente, 'MARKETING'))
        self.assertFalse(LegalService.cliente_acepto(cliente, 'USO_IMAGEN'))


class ARCOTest(TestCase):

    def _solicitud(self, **kw):
        datos = dict(tipo=TipoARCO.ACCESO, titular_nombre='Juan',
                     correo='j@x.com', descripcion='Quiero mis datos')
        datos.update(kw)
        return SolicitudARCO.objects.create(**datos)

    def test_plazo_de_20_dias_habiles_no_cae_en_fin_de_semana(self):
        s = self._solicitud()
        self.assertLess(s.fecha_limite.weekday(), 5)
        self.assertGreaterEqual((s.fecha_limite - timezone.localdate()).days, 20)

    def test_dias_restantes_negativo_si_vencio(self):
        s = self._solicitud()
        SolicitudARCO.objects.filter(pk=s.pk).update(
            fecha_limite=timezone.localdate() - timedelta(days=3))
        s.refresh_from_db()
        self.assertEqual(s.dias_restantes, -3)

    def test_folio_unico(self):
        folios = {self._solicitud().folio for _ in range(5)}
        self.assertEqual(len(folios), 5)

    def test_estado_inicial(self):
        self.assertEqual(self._solicitud().estado, EstadoARCO.RECIBIDA)


class VistasPublicasTest(TestCase):

    def test_documento_vigente_se_publica(self):
        _doc(TipoDocumento.AVISO_PRIVACIDAD, contenido='Mi aviso de privacidad')
        r = self.client.get(reverse('legal:aviso_privacidad'), secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Mi aviso de privacidad')

    def test_sin_version_vigente_da_404(self):
        r = self.client.get(reverse('legal:terminos'), secure=True)
        self.assertEqual(r.status_code, 404)

    def test_rutas_publicas_se_conservan(self):
        """No deben romperse los enlaces ya publicados."""
        self.assertEqual(reverse('legal:aviso_privacidad'), '/aviso-de-privacidad/')
        self.assertEqual(reverse('legal:terminos'), '/terminos-y-condiciones/')

    def test_publicar_version_nueva_se_ve_de_inmediato(self):
        """El cache no debe tapar una corrección legal.

        Con `cache_page` la clave es la URL, así que la versión anterior
        seguiría sirviéndose hasta una hora después de publicar la corrección.
        La clave incluye el hash del contenido justamente para evitarlo."""
        _doc(TipoDocumento.AVISO_PRIVACIDAD, version='1.0', contenido='Texto viejo')
        r1 = self.client.get(reverse('legal:aviso_privacidad'), secure=True)
        self.assertContains(r1, 'Texto viejo')

        _doc(TipoDocumento.AVISO_PRIVACIDAD, version='2.0', contenido='Texto corregido')
        r2 = self.client.get(reverse('legal:aviso_privacidad'), secure=True)
        self.assertContains(r2, 'Texto corregido')
        self.assertNotContains(r2, 'Texto viejo')

    def test_despublicar_da_404_aunque_estuviera_cacheado(self):
        doc = _doc(TipoDocumento.TERMINOS, contenido='Terminos vigentes')
        self.assertEqual(
            self.client.get(reverse('legal:terminos'), secure=True).status_code, 200)

        DocumentoLegal.objects.filter(pk=doc.pk).update(vigente=False)
        self.assertEqual(
            self.client.get(reverse('legal:terminos'), secure=True).status_code, 404)
