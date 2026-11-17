"""Expediente, condiciones y documentos de ventas (Issue #505, PR 1A)."""
import html as html_lib
import io
import re
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from comunicacion.models import Pendiente
from core_erp.descargas import url_descarga
from core_erp.test_utils import login_superuser_con_totp
from nomina import services_documentos, services_expediente, validadores
from nomina.models import CondicionesPago, DocumentoEmpleado, Empleado, EscalonComision

PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 32
STORAGE_MEMORIA = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}


def texto_pdf(pdf: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return '\n'.join(p.extract_text() or '' for p in doc.pages)


def empleado_ventas(**extra):
    datos = dict(nombre='Ana', puesto='VENTAS', nombre_legal='Ana María López Pérez',
                 calle_numero='Calle 20 No. 100', colonia='Centro', codigo_postal='97390', municipio='Umán',
                 estado='Yucatán', tipo_identificacion='INE', numero_identificacion='1234567890123')
    datos.update(extra)
    return Empleado.objects.create(**datos)


def condiciones_completas(empleado, **extra):
    datos = dict(empleado=empleado, vigente_desde=date(2026, 12, 1), pago_visita=Decimal('100.00'),
                 porcentaje_extras=Decimal('0.0500'))
    datos.update(extra)
    c = CondicionesPago.objects.create(**datos)
    EscalonComision.objects.create(condiciones=c, desde_contratacion=1, monto=Decimal('350.00'))
    EscalonComision.objects.create(condiciones=c, desde_contratacion=13, monto=Decimal('500.00'))
    return c


class ValidadoresTest(TestCase):

    def test_clabe_con_digito_verificador(self):
        validadores.validar_clabe('012910004895703146')  # la de la Quinta
        with self.assertRaises(ValidationError) as ctx:
            validadores.validar_clabe('012910004895703147')
        self.assertNotIn('0129', ' '.join(ctx.exception.messages))  # nunca eco del dato

    def test_curp_rfc_nss_cp(self):
        validadores.validar_curp('HEGG560427MVZRRL04')
        validadores.validar_rfc('HEGG560427MV1')
        validadores.validar_nss('12345678901')
        validadores.validar_codigo_postal('97390')
        for funcion, malo in ((validadores.validar_curp, 'ABC'), (validadores.validar_rfc, 'ABC'),
                              (validadores.validar_nss, '123'), (validadores.validar_codigo_postal, '9739')):
            with self.assertRaises(ValidationError):
                funcion(malo)


class EmpleadoCleanTest(TestCase):

    def test_normaliza_y_valida_solo_lo_capturado(self):
        e = Empleado(nombre='Ana', puesto='VENTAS', curp=' hegg 560427 mvzrrl04 ', clabe='0129 1000 4895 7031 46',
                     codigo_postal='97390')
        e.full_clean()
        self.assertEqual(e.curp, 'HEGG560427MVZRRL04')
        self.assertEqual(e.clabe, '012910004895703146')
        self.assertEqual(e.forma_pago, 'TRANSFERENCIA')

    def test_sin_datos_opcionales_es_valido(self):
        Empleado(nombre='Luis', puesto='MESERO').full_clean()

    def test_error_no_incluye_el_dato(self):
        e = Empleado(nombre='Ana', puesto='VENTAS', nss='99')
        with self.assertRaises(ValidationError) as ctx:
            e.full_clean()
        self.assertNotIn('99', str(ctx.exception.message_dict.get('nss')))

    def test_telefono_emergencia_invalido(self):
        e = Empleado(nombre='Ana', puesto='VENTAS', emergencia_telefono='123')
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_propiedades_para_documentos(self):
        e = empleado_ventas()
        self.assertEqual(e.nombre_para_documentos, 'Ana María López Pérez')
        self.assertEqual(e.domicilio_completo, 'Calle 20 No. 100, Col. Centro, C.P. 97390, Umán, Yucatán')
        self.assertEqual(e.identificacion_texto, 'INE / IFE número 1234567890123')
        self.assertEqual(Empleado(nombre='X').domicilio_completo, '')
        self.assertEqual(Empleado(nombre='X').nombre_para_documentos, 'X')

    def test_carga_por_get_or_create_sigue_funcionando(self):
        e, creado = Empleado.objects.get_or_create(nombre='Persona Jibble')
        self.assertTrue(creado)
        self.assertEqual(e.puesto, 'MESERO')


class ExpedienteFaltantesTest(TestCase):

    def test_otros_puestos_no_exigen_nada(self):
        self.assertEqual(services_expediente.expediente_faltantes(Empleado.objects.create(nombre='Luis')), [])

    def test_ventas_exige_todo(self):
        e = Empleado.objects.create(nombre='Ana', puesto='VENTAS')
        faltan = services_expediente.expediente_faltantes(e)
        self.assertEqual(len(faltan), 6)
        self.assertIn('Acuerdo de confidencialidad firmado', faltan)

    @override_settings(STORAGES=STORAGE_MEMORIA)
    def test_se_completa_con_documentos(self):
        e = empleado_ventas()
        for tipo in ('INE_FRENTE', 'INE_REVERSO', 'ACUERDO_FIRMADO'):
            DocumentoEmpleado.objects.create(empleado=e, tipo=tipo, archivo=ContentFile(PNG, name=f'{tipo}.png'))
        self.assertEqual(services_expediente.expediente_faltantes(e), [])
        DocumentoEmpleado.objects.filter(tipo='INE_FRENTE').update(activo=False)
        self.assertEqual(services_expediente.expediente_faltantes(e), ['INE (frente)'])


@override_settings(STORAGES=STORAGE_MEMORIA)
class CandadoUsuarioTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user('ana', password='clave-de-prueba-123')

    def test_sin_acuerdo_no_hay_usuario(self):
        e = empleado_ventas()
        e.usuario = self.user
        with self.assertRaises(ValidationError):
            e.save()
        with self.assertRaises(ValidationError):
            e.full_clean()

    def test_con_acuerdo_firmado_activo_si(self):
        e = empleado_ventas()
        DocumentoEmpleado.objects.create(empleado=e, tipo='ACUERDO_FIRMADO',
                                         archivo=ContentFile(PNG, name='a.png'))
        e.usuario = self.user
        e.save()
        e.refresh_from_db()
        self.assertEqual(e.usuario, self.user)

    def test_acuerdo_inactivo_no_cuenta(self):
        e = empleado_ventas()
        DocumentoEmpleado.objects.create(empleado=e, tipo='ACUERDO_FIRMADO', activo=False,
                                         archivo=ContentFile(PNG, name='a.png'))
        e.usuario = self.user
        with self.assertRaises(ValidationError):
            e.save()

    def test_otros_puestos_no_tienen_candado(self):
        e = Empleado.objects.create(nombre='Luis', puesto='STAFF', usuario=self.user)
        self.assertEqual(e.usuario, self.user)


class BajaTest(TestCase):

    def test_registrar_baja_abre_pendiente_a_direccion(self):
        e = empleado_ventas()
        with self.captureOnCommitCallbacks(execute=True):
            e.fecha_baja = date(2026, 12, 31)
            e.save()
        p = Pendiente.objects.get()
        self.assertEqual(p.rol, 'DIRECCION')
        self.assertIn('Ana', p.titulo)

    def test_guardar_otra_vez_no_duplica(self):
        e = empleado_ventas()
        with self.captureOnCommitCallbacks(execute=True):
            e.fecha_baja = date(2026, 12, 31)
            e.save()
        with self.captureOnCommitCallbacks(execute=True):
            e.save()
        self.assertEqual(Pendiente.objects.count(), 1)


class CondicionesTest(TestCase):

    def setUp(self):
        self.e = empleado_ventas()

    def test_escalon_por_numero(self):
        c = condiciones_completas(self.e)
        self.assertEqual(c.monto_de_contratacion(1), Decimal('350.00'))
        self.assertEqual(c.monto_de_contratacion(12), Decimal('350.00'))
        self.assertEqual(c.monto_de_contratacion(13), Decimal('500.00'))
        self.assertEqual(c.monto_de_contratacion(40), Decimal('500.00'))

    def test_aceptar_sustituye_la_vigente(self):
        c1 = condiciones_completas(self.e, vigente_desde=date(2026, 1, 1))
        services_expediente.aceptar_condiciones(c1)
        c2 = condiciones_completas(self.e, vigente_desde=date(2026, 7, 1))
        services_expediente.aceptar_condiciones(c2)
        c1.refresh_from_db()
        c2.refresh_from_db()
        self.assertEqual((c1.estado, c1.vigente_hasta), ('SUSTITUIDA', date(2026, 6, 30)))
        self.assertEqual(c2.estado, 'ACEPTADA')
        self.assertEqual(services_expediente.condiciones_vigentes(self.e, date(2026, 3, 1)), c1)
        self.assertEqual(services_expediente.condiciones_vigentes(self.e, date(2026, 8, 1)), c2)
        self.assertIsNone(services_expediente.condiciones_vigentes(self.e, date(2025, 12, 31)))

    def test_una_aceptada_no_se_edita(self):
        c = condiciones_completas(self.e)
        services_expediente.aceptar_condiciones(c)
        c.refresh_from_db()
        c.pago_visita = Decimal('999.00')
        with self.assertRaises(ValidationError):
            c.full_clean()
        escalon = c.escalones.first()
        escalon.monto = Decimal('1.00')
        with self.assertRaises(ValidationError):
            escalon.full_clean()

    def test_no_se_acepta_vacia_ni_con_fecha_anterior(self):
        vacia = CondicionesPago.objects.create(empleado=self.e, vigente_desde=date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            services_expediente.aceptar_condiciones(vacia)
        c1 = condiciones_completas(self.e, vigente_desde=date(2026, 5, 1))
        services_expediente.aceptar_condiciones(c1)
        c2 = condiciones_completas(self.e, vigente_desde=date(2026, 5, 1))
        with self.assertRaises(ValidationError):
            services_expediente.aceptar_condiciones(c2)

    def test_porcentaje_como_fraccion(self):
        c = CondicionesPago(empleado=self.e, vigente_desde=date(2026, 1, 1), porcentaje_extras=Decimal('5'))
        with self.assertRaises(ValidationError):
            c.full_clean()


class PropuestaPdfTest(TestCase):

    def setUp(self):
        self.e = empleado_ventas()

    def test_completa_con_las_cifras_del_registro(self):
        c = condiciones_completas(self.e)
        pdf, nombre = services_documentos.pdf_propuesta(c)
        texto = texto_pdf(pdf)
        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertTrue(nombre.startswith('QKT_Propuesta_Ventas_'))
        for esperado in ('$100.00', '$350.00', '$500.00', '5 %', 'De la 1 a la 12', 'De la 13 en adelante',
                         '$20,000.00', '$150.00', '$250.00', 'Ana María López Pérez', '30 %'):
            self.assertIn(esperado, texto)

    def test_cifras_distintas_cambian_el_documento(self):
        c = condiciones_completas(self.e, pago_visita=Decimal('120.00'), porcentaje_extras=Decimal('0.0700'),
                                  tope_visitas_semana=3, tope_compensacion=Decimal('0.2000'))
        c.escalones.filter(desde_contratacion=1).update(monto=Decimal('400.00'))
        texto = texto_pdf(services_documentos.pdf_propuesta(c)[0])
        for esperado in ('$120.00', '7 %', '$400.00', '$360.00', '20 %'):
            self.assertIn(esperado, texto)
        self.assertNotIn('$350.00', texto)

    def test_solo_los_componentes_capturados(self):
        c = CondicionesPago.objects.create(empleado=self.e, vigente_desde=date(2026, 12, 1),
                                           pago_fijo=Decimal('1500.00'), pago_fijo_periodo='SEMANAL')
        texto = texto_pdf(services_documentos.pdf_propuesta(c)[0])
        self.assertIn('$1,500.00', texto)
        self.assertNotIn('Comisión por contratación', texto)
        self.assertNotIn('Ejemplo:', texto)

    def test_se_genera_sin_nombre_legal(self):
        e = Empleado.objects.create(nombre='Sin Datos', puesto='VENTAS')
        c = condiciones_completas(e)
        pdf, _ = services_documentos.pdf_propuesta(c)
        self.assertTrue(pdf.startswith(b'%PDF'))


class AcuerdoPdfTest(TestCase):

    def test_completo_lleva_los_datos_del_empleado(self):
        e = empleado_ventas()
        pdf, nombre = services_documentos.pdf_acuerdo(e)
        texto = ' '.join(texto_pdf(pdf).split())
        self.assertTrue(nombre.startswith('QKT_Acuerdo_Confidencialidad_'))
        for esperado in ('ELIAN DE JESÚS PEREIRA CEH', 'Ana María López Pérez', 'Calle 20 No. 100',
                         'INE / IFE número 1234567890123', 'Décima. Jurisdicción'):
            self.assertIn(esperado, texto)

    def test_incompleto_no_genera_y_dice_que_falta(self):
        e = Empleado.objects.create(nombre='Ana', puesto='VENTAS', nombre_legal='Ana López')
        with self.assertRaises(services_documentos.FaltanDatos) as ctx:
            services_documentos.pdf_acuerdo(e)
        self.assertEqual(len(ctx.exception.faltantes), 2)
        self.assertTrue(any('Domicilio' in f for f in ctx.exception.faltantes))

    def test_texto_legal_identico_al_aprobado(self):
        """El cuerpo (de «Acuerdo que celebran» a la jurisdicción) es el de la referencia, palabra por palabra."""
        from pathlib import Path

        def plano(html):
            html = re.sub(r'<style.*?</style>', '', html, flags=re.S)
            return ' '.join(html_lib.unescape(re.sub(r'<[^>]+>', ' ', html)).replace('\xa0', ' ').split())

        base = Path(__file__).resolve().parent.parent
        ref = plano((base / 'docs' / 'ventas' / 'acuerdo_confidencialidad_referencia.html').read_text(encoding='utf-8'))
        generado = plano(render_to_string('nomina/pdf_acuerdo.html', {
            'titulo': 't', 'titular': '', 'nombre': '', 'domicilio': '', 'identificacion': ''}))

        def cuerpo(t):
            ini = t.index('Acuerdo que celebran')
            fin = t.index('Mérida, Yucatán.', t.index('Décima. Jurisdicción')) + len('Mérida, Yucatán.')
            return ' '.join(t[ini:fin].split())

        self.assertEqual(cuerpo(generado), cuerpo(ref))


@override_settings(STORAGES=STORAGE_MEMORIA)
class PermisosExpedienteTest(TestCase):

    def setUp(self):
        self.e = empleado_ventas(curp='HEGG560427MVZRRL04', clabe='012910004895703146')
        self.doc = DocumentoEmpleado.objects.create(empleado=self.e, tipo='INE_FRENTE',
                                                    archivo=ContentFile(PNG, name='ine.png'))
        self.nomina = User.objects.create_user('nom', password='clave-de-prueba-123', is_staff=True)
        permisos = Permission.objects.filter(content_type__app_label='nomina')
        self.nomina.user_permissions.set(permisos.exclude(codename='ver_expediente_empleado'))
        self.url = reverse('admin:nomina_empleado_change', args=[self.e.pk])

    def test_sin_el_permiso_solo_ve_lo_basico(self):
        self.client.force_login(self.nomina)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('name="tarifa_base"', html)
        for oculto in ('name="curp"', 'name="clabe"', 'name="nss"', 'name="calle_numero"',
                       'name="emergencia_nombre"', 'documentos-TOTAL_FORMS', 'HEGG560427', '012910004895'):
            self.assertNotIn(oculto, html)

    def test_post_directo_no_toca_el_expediente(self):
        self.client.force_login(self.nomina)
        r = self.client.post(self.url, {'nombre': 'Ana', 'puesto': 'VENTAS', 'telefono': '', 'tarifa_base': '50',
                                        'activo': 'on', 'curp': 'XXXX', 'clabe': '1'})
        self.assertEqual(r.status_code, 302)
        self.e.refresh_from_db()
        self.assertEqual(self.e.curp, 'HEGG560427MVZRRL04')

    def test_lista_sin_permiso_no_muestra_expediente(self):
        self.client.force_login(self.nomina)
        html = self.client.get(reverse('admin:nomina_empleado_changelist')).content.decode()
        self.assertNotIn('Expediente', html.split('<tbody>')[0].split('result_list')[-1])

    def test_descarga_y_url_directa_bloqueadas(self):
        self.client.force_login(self.nomina)
        url = url_descarga(self.doc, 'archivo')
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse('admin:nomina_empleado_acuerdo', args=[self.e.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('admin:nomina_documentoempleado_changelist')).status_code, 403)

    def test_con_el_permiso_ve_y_descarga(self):
        self.nomina.user_permissions.add(Permission.objects.get(codename='ver_expediente_empleado'))
        self.nomina.user_permissions.add(Permission.objects.get(codename='view_documentoempleado'))
        self.client.force_login(self.nomina)
        html = self.client.get(self.url).content.decode()
        self.assertIn('name="curp"', html)
        self.assertEqual(self.client.get(url_descarga(self.doc, 'archivo')).status_code, 200)

    def test_condiciones_solo_superusuario(self):
        self.nomina.user_permissions.set(Permission.objects.filter(content_type__app_label='nomina'))
        self.client.force_login(self.nomina)
        self.assertIn(self.client.get(reverse('admin:nomina_condicionespago_changelist')).status_code, (302, 403))
        c = condiciones_completas(self.e)
        self.assertEqual(self.client.get(reverse('admin:nomina_condicionespago_propuesta', args=[c.pk])).status_code, 404)

    def test_superusuario_genera_los_dos_pdf(self):
        su = User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123')
        login_superuser_con_totp(self.client, su)
        r = self.client.get(reverse('admin:nomina_empleado_acuerdo', args=[self.e.pk]))
        self.assertEqual(r['Content-Type'], 'application/pdf')
        c = condiciones_completas(self.e)
        r = self.client.get(reverse('admin:nomina_condicionespago_propuesta', args=[c.pk]))
        self.assertEqual(r['Content-Type'], 'application/pdf')

    def test_acuerdo_incompleto_redirige_con_aviso(self):
        su = User.objects.create_superuser('dir', 'd@x.mx', 'clave-de-prueba-123')
        login_superuser_con_totp(self.client, su)
        vacio = Empleado.objects.create(nombre='Vacía', puesto='VENTAS')
        r = self.client.get(reverse('admin:nomina_empleado_acuerdo', args=[vacio.pk]))
        self.assertEqual(r.status_code, 302)

    def test_validacion_del_archivo_subido(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        d = DocumentoEmpleado(empleado=self.e, tipo='OTRO',
                              archivo=SimpleUploadedFile('malo.pdf', b'<html>no soy pdf</html>'))
        with self.assertRaises(ValidationError):
            d.full_clean()
        bueno = DocumentoEmpleado(empleado=self.e, tipo='OTRO', archivo=SimpleUploadedFile('ok.png', PNG))
        bueno.full_clean()
