"""
Guardián del Manual (Issue #507).

Lo que no puede fallar: un rol nunca recibe una sección ajena, ni en pantalla,
ni en el PDF, ni cambiando parámetros; una sección con `[CONFIRMAR:]` solo la ve
el superusuario; y cada archivo del manual es válido (cabecera, roles, imágenes
y enlaces).
"""
import io
import re
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import strip_tags

from core_erp.test_utils import login_superuser_con_totp

from . import services as sv
from .models import DescargaManual


def sec(slug, roles, cuerpo='Texto de la sección.', capitulo='Capítulo', orden=100, titulo=None):
    return sv.Seccion(slug=slug, titulo=titulo or f'Título {slug}', capitulo=capitulo, orden=orden,
                      roles=tuple(roles), cuerpo=cuerpo, pendientes=tuple(sv.MARCADOR.findall(cuerpo)),
                      archivo=f'{slug}.md')


# Una sección por rol, una para todos, una mixta y un borrador.
FALSAS = [
    sec('para-todos', ['TODOS'], 'Regla común zanahoria.', orden=10),
    sec('de-ventas', ['VENTAS', 'DIRECCION'], 'Cómo cotizar jicama.', orden=20),
    sec('de-encargado', ['ENCARGADO', 'DIRECCION'], 'Cómo cobrar papaya.', orden=30),
    sec('de-contador', ['CONTADOR', 'DIRECCION'], 'Cómo conciliar mango.', orden=40),
    sec('de-operacion', ['OPERACION', 'DIRECCION'], 'Cómo limpiar sandia.', orden=50),
    sec('de-direccion', ['DIRECCION'], 'Solo para ti, aguacate.', orden=60),
    sec('mixta', ['TODOS'], 'Parte común.\n\n:::rol CONTADOR\nSolo contador: guayaba.\n:::\n\n:::rol DIRECCION\nSolo dirección: tamarindo.\n:::\n\nCierre común.', orden=70),
    sec('borrador', ['TODOS'], 'Falta decidir. [CONFIRMAR: ¿a quién se avisa?] platano.', orden=80),
]
PALABRAS = {
    'de-ventas': 'jicama', 'de-encargado': 'papaya', 'de-contador': 'mango',
    'de-operacion': 'sandia', 'de-direccion': 'aguacate', 'borrador': 'platano',
}


def usuario(nombre, grupo=None, superusuario=False):
    u = User.objects.create_user(nombre, password='x', is_staff=True, is_superuser=superusuario)
    if grupo:
        u.groups.add(Group.objects.get_or_create(name=grupo)[0])
    return u


class GuardianSeccionesTest(SimpleTestCase):
    """Los archivos reales de `manual/secciones/`."""

    def test_toda_seccion_tiene_cabecera_valida(self):
        secciones = sv.cargar_secciones()
        self.assertTrue(secciones, 'El manual no tiene secciones.')
        for s in secciones:
            with self.subTest(seccion=s.archivo):
                self.assertTrue(set(s.roles) <= set(sv.ROLES_VALIDOS))

    def test_toda_imagen_referenciada_existe(self):
        for s in sv.cargar_secciones():
            for ruta in sv.imagenes_de(s):
                with self.subTest(seccion=s.archivo, imagen=ruta):
                    self.assertTrue((sv.IMAGENES.parent / ruta).is_file(),
                                    'Falta la captura: corre `manage.py generar_capturas_manual`.')

    def test_ningun_enlace_interno_roto(self):
        slugs = {s.slug for s in sv.cargar_secciones()}
        for s in sv.cargar_secciones():
            for destino in sv.enlaces_de(s):
                with self.subTest(seccion=s.archivo, destino=destino):
                    self.assertIn(destino, slugs)

    def test_ninguna_seccion_se_llama_pdf(self):
        self.assertNotIn('pdf', {s.slug for s in sv.cargar_secciones()})

    def test_cabeceras_invalidas_se_rechazan(self):
        casos = {
            'sin cabecera': 'Hola',
            'sin roles': '---\ntitulo: A\ncapitulo: B\norden: 1\n---\nx',
            'rol desconocido': '---\ntitulo: A\ncapitulo: B\norden: 1\nroles: [JEFE]\n---\nx',
            'orden no numérico': '---\ntitulo: A\ncapitulo: B\norden: uno\nroles: [TODOS]\n---\nx',
            'roles sin corchetes': '---\ntitulo: A\ncapitulo: B\norden: 1\nroles: TODOS\n---\nx',
        }
        for nombre, texto in casos.items():
            with self.subTest(caso=nombre), self.assertRaises(sv.ErrorSeccion):
                sv._cabecera(texto, 'x.md') if nombre == 'sin cabecera' else self._leer(texto)

    def _leer(self, texto):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / '100_prueba.md'
            ruta.write_text(texto, encoding='utf-8')
            return sv._leer(ruta)


class RolesTest(TestCase):
    def test_roles_se_derivan_del_usuario(self):
        self.assertEqual(sv.roles_de(usuario('ven', 'Vendedor')), {'TODOS', 'VENTAS'})
        self.assertEqual(sv.roles_de(usuario('enc', 'Ventas')), {'TODOS', 'ENCARGADO'})
        self.assertEqual(sv.roles_de(usuario('con', 'Contabilidad')), {'TODOS', 'CONTADOR'})
        self.assertEqual(sv.roles_de(usuario('nom', 'Nómina')), {'TODOS'})
        self.assertEqual(sv.roles_de(usuario('sin')), {'TODOS'})
        self.assertEqual(sv.roles_de(usuario('dir', superusuario=True)), set(sv.ROLES_VALIDOS))

    def test_quien_no_es_staff_o_esta_inactivo_no_tiene_roles(self):
        no_staff = User.objects.create_user('cli', password='x')
        inactivo = usuario('baja', 'Ventas')
        inactivo.is_active = False
        inactivo.save()
        self.assertEqual(sv.roles_de(no_staff), frozenset())
        self.assertEqual(sv.roles_de(inactivo), frozenset())


@patch('manual.services.cargar_secciones', return_value=FALSAS)
class AislamientoPorRolTest(TestCase):
    """Un rol nunca recibe una sección ajena: pantalla, URL directa, búsqueda y PDF."""

    @classmethod
    def setUpTestData(cls):
        cls.casos = {   # usuario -> slugs que SÍ le corresponden (sin borradores)
            'ven': (usuario('ven', 'Vendedor'), {'para-todos', 'de-ventas', 'mixta'}),
            'enc': (usuario('enc', 'Ventas'), {'para-todos', 'de-encargado', 'mixta'}),
            'con': (usuario('con', 'Contabilidad'), {'para-todos', 'de-contador', 'mixta'}),
            'sin': (usuario('sin'), {'para-todos', 'mixta'}),
        }

    def _entrar(self, nombre):
        u, permitidas = self.casos[nombre]
        self.client.force_login(u)
        return permitidas

    def test_el_indice_solo_lista_lo_propio(self, _):
        for nombre in self.casos:
            with self.subTest(usuario=nombre):
                permitidas = self._entrar(nombre)
                html = self.client.get(reverse('manual:indice')).content.decode()
                for slug in {s.slug for s in FALSAS}:
                    enlace = reverse('manual:seccion', args=[slug])
                    self.assertEqual(enlace in html, slug in permitidas, slug)

    def test_url_directa_de_una_seccion_ajena_es_404(self, _):
        for nombre in self.casos:
            permitidas = self._entrar(nombre)
            for s in FALSAS:
                with self.subTest(usuario=nombre, seccion=s.slug):
                    respuesta = self.client.get(reverse('manual:seccion', args=[s.slug]))
                    self.assertEqual(respuesta.status_code, 200 if s.slug in permitidas else 404)

    def test_los_bloques_por_rol_se_filtran_en_pantalla(self, _):
        pagina = reverse('manual:seccion', args=['mixta'])
        self._entrar('con')
        html = self.client.get(pagina).content.decode()
        self.assertIn('guayaba', html)
        self.assertNotIn('tamarindo', html)
        self._entrar('ven')
        html = self.client.get(pagina).content.decode()
        self.assertNotIn('guayaba', html)
        self.assertNotIn('tamarindo', html)
        self.assertIn('Cierre común', html)

    def test_el_buscador_no_encuentra_lo_ajeno(self, _):
        self._entrar('ven')
        for palabra in ('papaya', 'mango', 'aguacate', 'guayaba', 'platano'):
            html = self.client.get(reverse('manual:indice'), {'q': palabra}).content.decode()
            self.assertIn('No encontramos nada', html, palabra)
        html = self.client.get(reverse('manual:indice'), {'q': 'jicama'}).content.decode()
        self.assertIn('Título de-ventas', html)

    def test_cambiar_parametros_no_amplia_lo_visible(self, _):
        self._entrar('ven')
        for params in ({'rol': 'DIRECCION'}, {'rol': 'COMPLETO'}, {'roles': 'CONTADOR'}, {'borradores': '1'}):
            with self.subTest(params=params):
                html = self.client.get(reverse('manual:indice'), params).content.decode()
                self.assertNotIn(reverse('manual:seccion', args=['de-contador']), html)
                self.assertNotIn(reverse('manual:seccion', args=['borrador']), html)

    def test_el_pdf_trae_solo_lo_propio_aunque_se_pida_otro(self, _):
        for nombre in self.casos:
            permitidas = self._entrar(nombre)
            for params in ({}, {'rol': 'COMPLETO'}, {'rol': 'DIRECCION'}, {'rol': 'CONTADOR'}):
                with self.subTest(usuario=nombre, params=params), \
                        patch('manual.views.respuesta_pdf') as pdf:
                    pdf.return_value = self.client.get(reverse('manual:indice'))   # cualquier respuesta
                    self.client.get(reverse('manual:pdf'), params)
                    exportadas = {s.slug for _cap, lista in pdf.call_args.args[1]['capitulos'] for s, _h in lista}
                    self.assertEqual(exportadas, permitidas)

    def test_el_texto_del_pdf_real_no_filtra_secciones_ajenas(self, _):
        import pdfplumber
        self._entrar('ven')
        respuesta = self.client.get(reverse('manual:pdf'), {'rol': 'DIRECCION'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.content.startswith(b'%PDF'))
        with pdfplumber.open(io.BytesIO(respuesta.content)) as doc:
            texto = ' '.join(p.extract_text() or '' for p in doc.pages)
        self.assertIn('jicama', texto)
        for ajena in ('papaya', 'mango', 'sandia', 'aguacate', 'platano', 'guayaba', 'tamarindo'):
            self.assertNotIn(ajena, texto)
        self.assertIn('Descargado por ven', texto)   # portada y pie de página

    def test_anonimo_y_no_staff_van_al_login(self, _):
        for ruta in (reverse('manual:indice'), reverse('manual:seccion', args=['para-todos']), reverse('manual:pdf')):
            self.assertEqual(self.client.get(ruta).status_code, 302)
        User.objects.create_user('cli', password='x')
        self.client.login(username='cli', password='x')
        self.assertEqual(self.client.get(reverse('manual:indice')).status_code, 302)


@patch('manual.services.cargar_secciones', return_value=FALSAS)
class BorradoresTest(TestCase):
    """`[CONFIRMAR:]` impide publicar: solo el superusuario ve la sección, señalada."""

    def test_la_seccion_con_marcador_es_borrador(self, _):
        self.assertTrue(next(s for s in FALSAS if s.slug == 'borrador').borrador)
        self.assertFalse(next(s for s in FALSAS if s.slug == 'para-todos').borrador)

    def test_nadie_mas_la_recibe_en_pantalla_ni_en_pdf(self, _):
        for u in (usuario('ven', 'Vendedor'), usuario('enc', 'Ventas'), usuario('con', 'Contabilidad'), usuario('sin')):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse('manual:seccion', args=['borrador'])).status_code, 404, u.username)
            enlace = reverse('manual:seccion', args=['borrador'])
            self.assertNotIn(enlace, self.client.get(reverse('manual:indice')).content.decode())
            self.assertNotIn(enlace, self.client.get(reverse('manual:indice'), {'q': 'platano'}).content.decode())

    def test_el_superusuario_la_ve_senalada(self, _):
        login_superuser_con_totp(self.client, usuario('dir', superusuario=True))
        html = self.client.get(reverse('manual:seccion', args=['borrador'])).content.decode()
        self.assertIn('Borrador', html)
        self.assertIn('manual-pendiente', html)
        self.assertIn('¿a quién se avisa?', html)

    def test_pdf_completo_incluye_borradores_pero_el_de_un_rol_no(self, _):
        login_superuser_con_totp(self.client, usuario('dir', superusuario=True))
        with patch('manual.views.respuesta_pdf') as pdf:
            pdf.return_value = self.client.get(reverse('manual:indice'))
            self.client.get(reverse('manual:pdf'))
            todas = {s.slug for _c, lista in pdf.call_args.args[1]['capitulos'] for s, _h in lista}
            self.assertEqual(todas, {s.slug for s in FALSAS})
            self.client.get(reverse('manual:pdf'), {'rol': 'CONTADOR'})
            contador = {s.slug for _c, lista in pdf.call_args.args[1]['capitulos'] for s, _h in lista}
            self.assertEqual(contador, {'para-todos', 'de-contador', 'mixta'})


@patch('manual.services.cargar_secciones', return_value=FALSAS)
class PdfTest(TestCase):
    def test_el_superusuario_elige_el_manual_y_queda_en_la_bitacora(self, _):
        dir_ = usuario('dir', superusuario=True)
        login_superuser_con_totp(self.client, dir_)
        respuesta = self.client.get(reverse('manual:pdf'), {'rol': 'CONTADOR'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn('QKT_Manual_CONTADOR_', respuesta['Content-Disposition'])
        registro = DescargaManual.objects.get()
        self.assertEqual((registro.usuario, registro.rol_exportado, registro.secciones), (dir_, 'CONTADOR', 3))

    def test_selector_invalido_es_400(self, _):
        login_superuser_con_totp(self.client, usuario('dir', superusuario=True))
        self.assertEqual(self.client.get(reverse('manual:pdf'), {'rol': 'JEFE'}).status_code, 400)
        self.assertFalse(DescargaManual.objects.exists())

    def test_un_usuario_normal_descarga_el_suyo(self, _):
        self.client.force_login(usuario('ven', 'Vendedor'))
        respuesta = self.client.get(reverse('manual:pdf'), {'rol': 'DIRECCION'})
        self.assertIn('QKT_Manual_VENTAS_', respuesta['Content-Disposition'])
        self.assertEqual(DescargaManual.objects.get().rol_exportado, 'VENTAS')

    def test_nombre_de_quien_descarga_con_caracteres_raros_no_rompe_el_pdf(self, _):
        u = usuario('raro')
        u.first_name = 'Ana "<b>\\'
        u.save()
        self.client.force_login(u)
        self.assertTrue(self.client.get(reverse('manual:pdf')).content.startswith(b'%PDF'))

    def test_bitacora_es_solo_lectura_y_de_superusuario(self, _):
        from django.contrib import admin
        modelo_admin = admin.site._registry[DescargaManual]
        peticion = self.client.get('/').wsgi_request
        peticion.user = usuario('ven', 'Vendedor')
        self.assertFalse(modelo_admin.has_module_permission(peticion))
        peticion.user = usuario('dir', superusuario=True)
        self.assertTrue(modelo_admin.has_view_permission(peticion))
        self.assertFalse(modelo_admin.has_add_permission(peticion))
        self.assertFalse(modelo_admin.has_change_permission(peticion))
        self.assertFalse(modelo_admin.has_delete_permission(peticion))


class RenderTest(TestCase):
    def test_los_valores_del_negocio_se_leen_de_la_fuente(self):
        from comercial.models import Cotizacion
        s = sec('x', ['TODOS'], 'Evento: {{ dias_pago_total.EVENTO }} días. Sesión: {{ sesion_minutos }} min.')
        html = sv.render_cuerpo(s, {'TODOS'})
        self.assertIn(f'{Cotizacion.DIAS_PAGO_TOTAL["EVENTO"]} días', html)
        self.assertNotIn('{{', html)

    def test_saneado_quita_scripts_y_manejadores(self):
        s = sec('x', ['TODOS'], 'Hola <script>alert(1)</script> [a](javascript:alert(2)) <img src="img/x.webp" onerror="alert(3)">')
        html = sv.render_cuerpo(s, {'TODOS'})
        for peligro in ('<script', 'javascript:', 'onerror'):
            self.assertNotIn(peligro, html)

    def test_componentes(self):
        s = sec('x', ['TODOS'], ':::correcto\nBien.\n:::\n\n:::quehacer Si falla\nLlama.\n:::')
        html = sv.render_cuerpo(s, {'TODOS'})
        self.assertIn('manual-bloque--correcto', html)
        self.assertIn('Si falla', html)

    def test_bloques_sin_cerrar_o_con_rol_invalido_fallan(self):
        for cuerpo in (':::nota\nsin cerrar', ':::rol JEFE\nx\n:::', ':::'):
            with self.subTest(cuerpo=cuerpo), self.assertRaises(sv.ErrorSeccion):
                sv.render_cuerpo(sec('x', ['TODOS'], cuerpo), {'TODOS'})

    def test_enlace_interno_en_pdf_salta_a_su_ancla(self):
        s = sec('x', ['TODOS'], 'Ver [otra](../otra-seccion/).')
        self.assertIn('href="../otra-seccion/"', sv.render_cuerpo(s, {'TODOS'}))
        self.assertIn('href="#s-otra-seccion"', sv.render_cuerpo(s, {'TODOS'}, para_pdf=True))


class SeccionesRealesTest(TestCase):
    """Las secciones de muestra se leen completas y exportan sin error."""

    def test_el_superusuario_las_ve_todas_y_el_pdf_completo_se_genera(self):
        login_superuser_con_totp(self.client, usuario('dir', superusuario=True))
        for s in sv.cargar_secciones():
            with self.subTest(seccion=s.slug):
                self.assertEqual(self.client.get(reverse('manual:seccion', args=[s.slug])).status_code, 200)
        self.assertTrue(self.client.get(reverse('manual:pdf')).content.startswith(b'%PDF'))

    def test_las_secciones_publicas_no_traen_etiquetas_sin_resolver(self):
        for s in sv.cargar_secciones():
            html = sv.render_cuerpo(s, set(sv.ROLES_VALIDOS))
            self.assertNotIn('{{', html, s.slug)
            self.assertNotIn(':::', html, s.slug)


class SinInformacionReservadaTest(TestCase):
    """Ningún rol distinto de Dirección recibe costos, márgenes, configuración ni comisiones ajenas (Issue #507)."""

    PROHIBIDO = re.compile(
        r'\b(costos?|márgenes?|margen|utilidad|contabilidad →|nómina → (condiciones|escalones)|indicadores →|'
        r'variables de entorno|programador|supuestos|runbook)\b', re.IGNORECASE)

    def test_las_secciones_de_los_roles_no_traen_lo_reservado(self):
        for rol in ('VENTAS', 'ENCARGADO', 'OPERACION', 'CONTADOR', 'TODOS'):
            for s in sv.secciones_para({rol, sv.TODOS}, borradores=True):
                html = sv.render_cuerpo(s, {rol, sv.TODOS})
                texto = strip_tags(html)
                for m in self.PROHIBIDO.finditer(texto):
                    if rol == 'CONTADOR' and m.group(0).lower().startswith(('contabilidad', 'costo')):
                        continue   # el contador trabaja en Contabilidad y en su catálogo hay cuentas de costos
                    self.fail(f'{s.slug} ({rol}) menciona «{m.group(0)}»')


class TablasDinamicasTest(TestCase):
    """Las tablas que salen del sistema (cancelación, horarios de visita) se llenan de verdad."""

    def test_cancelacion_y_horarios_se_leen_de_la_fuente(self):
        from comercial import reglas_contrato
        from comunicacion.models import HorarioVisita
        HorarioVisita.objects.create(dia_semana=2, hora_inicio='10:00', hora_fin='12:00', duracion_minutos=60)
        por_slug = {s.slug: s for s in sv.cargar_secciones()}
        canc = strip_tags(sv.render_cuerpo(por_slug['cambios-y-cancelaciones'], {'VENTAS'}))
        for tramo, *_ in reglas_contrato.TABLA_CANCELACION['SERVICIO'] + reglas_contrato.TABLA_CANCELACION['HOSPEDAJE']:
            self.assertIn(tramo, canc)
        visitas = strip_tags(sv.render_cuerpo(por_slug['agendar-una-visita'], {'VENTAS'}))
        self.assertIn('10:00 a 12:00', visitas)
