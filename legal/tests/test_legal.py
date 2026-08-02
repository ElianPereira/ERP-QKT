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


class SeedTest(TestCase):
    """El seed corre en cada arranque del contenedor: debe ser idempotente y
    no puede degradar una versión publicada desde el admin."""

    def _seed(self, publicar=True):
        from io import StringIO
        from django.core.management import call_command
        salida = StringIO()
        args = ['seed_documentos_legales']
        if publicar:
            args.append('--publicar')
        call_command(*args, stdout=salida)
        return salida.getvalue()

    def test_publica_los_tres_documentos(self):
        self._seed()
        for tipo in (TipoDocumento.AVISO_PRIVACIDAD, TipoDocumento.TERMINOS,
                     TipoDocumento.POLITICA_CANCELACION):
            self.assertTrue(
                DocumentoLegal.objects.filter(tipo=tipo, vigente=True).exists(),
                f'no quedó vigente {tipo}',
            )

    def test_es_idempotente(self):
        self._seed()
        antes = DocumentoLegal.objects.count()
        salida = self._seed()
        self.assertEqual(DocumentoLegal.objects.count(), antes)
        self.assertIn('ya vigente', salida)

    def test_no_degrada_una_version_publicada_desde_el_admin(self):
        """Regresión: al correr en cada deploy, republicar la v2.0 del repo
        habría desmarcado una v3.0 publicada a mano."""
        self._seed()
        v3 = _doc(TipoDocumento.TERMINOS, version='3.0', contenido='Terminos v3')
        self.assertTrue(v3.vigente)

        self._seed()

        v3.refresh_from_db()
        self.assertTrue(v3.vigente, 'el seed degradó la versión más nueva')
        self.assertEqual(
            DocumentoLegal.objects.filter(tipo=TipoDocumento.TERMINOS,
                                          vigente=True).count(), 1)

    def test_publica_una_version_mas_nueva_del_repositorio(self):
        """
        Regresión: el candado anterior solo publicaba cuando NO había versión
        vigente, así que una corrección publicada en el repositorio (v2.1)
        nunca llegaba a producción — se quedaba viva la v2.0 anterior. Fue
        exactamente lo que pasó con el aviso de privacidad.
        """
        vieja = _doc(TipoDocumento.AVISO_PRIVACIDAD, version='1.0',
                     contenido='Aviso viejo con notas internas')
        self.assertTrue(vieja.vigente)

        self._seed()

        vieja.refresh_from_db()
        self.assertFalse(vieja.vigente, 'la versión vieja siguió vigente')
        vigente = DocumentoLegal.objects.get(tipo=TipoDocumento.AVISO_PRIVACIDAD,
                                             vigente=True)
        self.assertEqual(vigente.version, '2.1')

    def test_ordena_las_versiones_numericamente(self):
        """'2.10' es posterior a '2.9'; comparadas como cadenas, no."""
        from legal.management.commands.seed_documentos_legales import Command
        self.assertGreater(Command._orden('2.10'), Command._orden('2.9'))
        self.assertGreater(Command._orden('3.0'), Command._orden('2.1'))

    def test_crea_el_catalogo_de_finalidades(self):
        self._seed(publicar=False)
        self.assertTrue(Finalidad.objects.filter(
            clave='MARKETING', requiere_consentimiento=True).exists())
        self.assertTrue(Finalidad.objects.filter(
            clave='OPERACION', requiere_consentimiento=False).exists())


class RenderMarkdownTest(TestCase):
    """El cliente debe ver el documento formateado, no el Markdown crudo."""

    def test_convierte_encabezados_tablas_y_listas(self):
        doc = _doc(TipoDocumento.TERMINOS, contenido=(
            "# TÍTULO\n\n**Versión 2.0**\n\n---\n\n"
            "## 1. Sección\n\nUn párrafo con **negritas**.\n\n"
            "| Concepto | Dato |\n|---|---|\n| RFC | PECE010202IA0 |\n\n"
            "- primer punto\n- segundo punto\n"
        ))
        html = doc.render_html()
        self.assertIn('<h2>', html)
        self.assertIn('<table>', html)
        self.assertIn('<td>PECE010202IA0</td>', html)
        self.assertIn('<li>primer punto</li>', html)
        self.assertIn('<strong>negritas</strong>', html)
        # Nada de sintaxis cruda a la vista
        self.assertNotIn('## 1.', html)
        self.assertNotIn('|---|', html)
        self.assertNotIn('**negritas**', html)

    def test_omite_el_encabezado_que_ya_muestra_la_portada(self):
        doc = _doc(TipoDocumento.TERMINOS, contenido=(
            "# TÉRMINOS Y CONDICIONES\n\n**Versión 2.0 — Vigente desde hoy**\n\n"
            "---\n\n## 1. Objeto\n\nTexto.\n"
        ))
        html = doc.render_html()
        self.assertNotIn('TÉRMINOS Y CONDICIONES', html)
        self.assertNotIn('Vigente desde hoy', html)
        self.assertIn('1. Objeto', html)

    def test_la_pagina_publica_entrega_html_formateado(self):
        _doc(TipoDocumento.AVISO_PRIVACIDAD, contenido=(
            "# AVISO\n\n## 1. Responsable\n\n| Concepto | Dato |\n|---|---|\n"
            "| RFC | PECE010202IA0 |\n"
        ))
        r = self.client.get(reverse('legal:aviso_privacidad'), secure=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, '<table>')
        self.assertContains(r, 'PECE010202IA0')
        self.assertNotContains(r, '|---|')


class DocumentosSinNotasInternasTest(TestCase):
    """Los archivos que se publican no deben traer instrucciones de redacción."""

    FRASES_INTERNAS = (
        'eliminar antes de publicar',
        'Eliminar si no hay',
        'deben acompañar al formulario',
        'Para señalización física',
        'Para mostrar en formularios',
        'ajustar estos porcentajes',
    )

    def test_ningun_documento_publicable_trae_notas_internas(self):
        from pathlib import Path
        import legal
        directorio = Path(legal.__file__).parent / 'documentos_iniciales'
        for ruta in sorted(directorio.glob('*.md')):
            contenido = ruta.read_text(encoding='utf-8')
            for frase in self.FRASES_INTERNAS:
                self.assertNotIn(
                    frase, contenido,
                    f"{ruta.name} contiene la instrucción interna {frase!r}",
                )
