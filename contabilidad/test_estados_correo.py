"""Estado de cuenta BBVA por correo (Issue #401): bitácora, procesamiento, webhook y avisos.

Nada de red ni de WhatsApp real. Los correos son MIME sintéticos con el PDF de prueba
de `tests_fixtures`; la contraseña del ZIP se inventa al vuelo en cada corrida."""
import hashlib
import hmac
import io
import secrets
import struct
import time
from datetime import date
from decimal import Decimal
from email.message import EmailMessage
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pyzipper
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from comunicacion.models import Pendiente

from . import services_estados_correo as servicio
from .models import CorreoEstadoCuenta, CuentaBancaria, EstadoCuentaBancario, MovimientoEstadoCuenta, Poliza
from .services_estados_cuenta import procesar_estado_cuenta

STORAGE_MEMORIA = {
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
LLAVE = Fernet.generate_key().decode()
SECRETO_WEBHOOK = secrets.token_hex(16)
PDF = Path(__file__).parent / 'tests_fixtures' / 'estado_cuenta_bbva_maestra_pyme_ejemplo.pdf'
NUMERO_CUENTA = '0489570314'  # el que imprime el PDF de prueba (corte 30/04/2026)
AVISO = 'comunicacion.services_pendientes._enviar_aviso'


def correo(adjuntos=(), message_id=None, asunto='Estado de cuenta BBVA', cuerpo='Adjunto su estado de cuenta.'):
    msg = EmailMessage()
    msg['From'] = 'estados@bbva.example'
    msg['To'] = 'estados@quintakooxtanil.com'
    msg['Subject'] = asunto
    msg['Message-ID'] = message_id or f'<{secrets.token_hex(8)}@bbva.example>'
    msg.set_content(cuerpo)
    for nombre, contenido, tipo in adjuntos:
        principal, secundario = tipo.split('/')
        msg.add_attachment(contenido, maintype=principal, subtype=secundario, filename=nombre)
    return msg.as_bytes()


def zip_con_clave(archivos, clave):
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, 'w', compression=pyzipper.ZIP_DEFLATED,
                             encryption=pyzipper.WZ_AES if clave else None) as zf:
        if clave:
            zf.setpassword(clave.encode())
        for nombre, contenido in archivos:
            zf.writestr(nombre, contenido)
    return buf.getvalue()


def pdf_cifrado(clave):
    """PDF mínimo con cifrado estándar RC4 de 40 bits (sin streams que cifrar)."""
    pad = bytes.fromhex('28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A')

    def rc4(k, datos):
        s, j = list(range(256)), 0
        for i in range(256):
            j = (j + s[i] + k[i % len(k)]) % 256
            s[i], s[j] = s[j], s[i]
        i = j = 0
        salida = bytearray()
        for b in datos:
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            salida.append(b ^ s[(s[i] + s[j]) % 256])
        return bytes(salida)

    pw = (clave.encode() + pad)[:32]
    o = rc4(hashlib.md5(pw).digest()[:5], pw)  # noqa: S324 -- el cifrado PDF estándar usa MD5
    p = struct.pack('<i', -4)
    ident = b'0123456789abcdef'
    u = rc4(hashlib.md5(pw + o + p + ident).digest()[:5], pad)  # noqa: S324
    hexa = lambda b: b'<' + b.hex().encode() + b'>'  # noqa: E731
    objetos = [b'<</Type/Catalog/Pages 2 0 R>>', b'<</Type/Pages/Kids[3 0 R]/Count 1>>',
               b'<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>',
               b'<</Filter/Standard/V 1/R 2/O ' + hexa(o) + b'/U ' + hexa(u) + b'/P -4>>']
    out, posiciones = io.BytesIO(), []
    out.write(b'%PDF-1.4\n')
    for n, cuerpo in enumerate(objetos, 1):
        posiciones.append(out.tell())
        out.write(b'%d 0 obj\n' % n + cuerpo + b'\nendobj\n')
    xref = out.tell()
    out.write(b'xref\n0 5\n0000000000 65535 f \n')
    for pos in posiciones:
        out.write(b'%010d 00000 n \n' % pos)
    out.write(b'trailer\n<</Size 5/Root 1 0 R/Encrypt 4 0 R/ID[' + hexa(ident) + hexa(ident) + b']>>\n'
              b'startxref\n%d\n%%%%EOF\n' % xref)
    return out.getvalue()


@override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=LLAVE, STORAGES=STORAGE_MEMORIA, ERP_WA_NEGOCIO_NUMERO='529990000001')
class BaseCorreoTest(TestCase):
    def setUp(self):
        self.cuenta = CuentaBancaria.objects.create(
            nombre='BBVA Maestra', banco='BBVA', numero_cuenta=NUMERO_CUENTA, clabe='012345678901234567')
        self.pdf = PDF.read_bytes()
        patcher = patch(AVISO)
        self.aviso = patcher.start()
        self.addCleanup(patcher.stop)

    def procesar(self, mime):
        bitacora = servicio.registrar_correo(mime)
        servicio.procesar_correo(bitacora)
        bitacora.refresh_from_db()
        return bitacora

    def correo_pdf(self, **kw):
        return correo([('estado.pdf', self.pdf, 'application/pdf')], **kw)


class ProcesamientoTest(BaseCorreoTest):
    def test_correo_valido_crea_el_mismo_estado_que_la_carga_manual(self):
        bitacora = self.procesar(self.correo_pdf())
        self.assertEqual(bitacora.resultado, 'PROCESADO', bitacora.detalle)
        estado = bitacora.estado_cuenta
        self.assertEqual((estado.origen, estado.formato, estado.estado), ('CORREO', 'PDF', 'PROCESADO'))
        self.assertEqual((estado.periodo_mes, estado.periodo_anio), (4, 2026))

        manual = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta, periodo_mes=3, periodo_anio=2026, formato='PDF',
            archivo=io_archivo(self.pdf))
        procesar_estado_cuenta(manual)
        campos = ('fecha', 'descripcion', 'referencia', 'cargo', 'abono', 'saldo_parcial')
        de = lambda e: list(MovimientoEstadoCuenta.objects.filter(estado_cuenta=e).order_by('id').values_list(*campos))  # noqa: E731
        self.assertEqual(de(estado), de(manual))
        self.assertEqual(len(de(estado)), 39)
        manual.refresh_from_db()
        self.assertEqual((estado.saldo_inicial_estado, estado.saldo_final_estado, estado.fecha_corte_real),
                         (manual.saldo_inicial_estado, manual.saldo_final_estado, date(2026, 4, 30)))
        self.assertEqual(estado.saldo_inicial_estado, Decimal('6624.34'))

    def test_aviso_de_exito_sin_montos_ni_saldos(self):
        self.procesar(self.correo_pdf())
        textos = [c.args[1] for c in self.aviso.call_args_list]
        self.assertTrue(textos)
        for texto in textos:
            self.assertIn('abril 2026 cargado: 39 movimientos', texto)
            self.assertNotRegex(texto, r'\d[\d,]*\.\d{2}')

    def test_mismo_message_id_dos_veces_un_solo_estado(self):
        mime = self.correo_pdf(message_id='<repetido@bbva.example>')
        primera = self.procesar(mime)
        segunda = servicio.registrar_correo(mime)
        self.assertEqual(segunda.resultado, 'DUPLICADO')
        self.assertEqual(segunda.pk, primera.pk)
        self.assertEqual(CorreoEstadoCuenta.objects.count(), 1)
        self.assertEqual(EstadoCuentaBancario.objects.count(), 1)
        primera.refresh_from_db()
        self.assertEqual(primera.resultado, 'PROCESADO')  # en la base conserva su resultado real

    def test_periodo_cargado_a_mano_se_ignora_y_no_cambia(self):
        manual = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta, periodo_mes=4, periodo_anio=2026, formato='PDF',
            archivo=io_archivo(b'manual'), estado='PROCESADO', saldo_final_estado=Decimal('1.00'))
        bitacora = self.procesar(self.correo_pdf())
        self.assertEqual(bitacora.resultado, 'IGNORADO')
        self.assertIn('ya estaba cargado', bitacora.detalle)
        manual.refresh_from_db()
        self.assertEqual((manual.origen, manual.saldo_final_estado, manual.movimientos.count()),
                         ('MANUAL', Decimal('1.00'), 0))
        self.assertEqual(EstadoCuentaBancario.objects.count(), 1)

    def test_cuenta_desconocida_es_error_sin_estado_ni_polizas(self):
        self.cuenta.numero_cuenta = '9999999999'
        self.cuenta.save()
        polizas = Poliza.objects.count()
        bitacora = self.procesar(self.correo_pdf())
        self.assertEqual(bitacora.resultado, 'ERROR')
        self.assertIn('número de cuenta', bitacora.detalle)
        self.assertNotIn(NUMERO_CUENTA, bitacora.detalle)
        self.assertEqual((EstadoCuentaBancario.objects.count(), Poliza.objects.count()), (0, polizas))
        self.assertTrue(Pendiente.objects.filter(clave_idempotencia=f'estado_correo:{bitacora.pk}').exists())

    def test_fallo_a_medias_deja_el_eml_y_el_comando_lo_completa(self):
        real = procesar_estado_cuenta
        with patch.object(servicio, 'procesar_estado_cuenta', side_effect=RuntimeError('falla')):
            bitacora = self.procesar(self.correo_pdf())
        self.assertEqual((bitacora.resultado, bitacora.intentos), ('ERROR', 1))
        self.assertTrue(bitacora.archivo_eml.name)
        simulacion = StringIO()
        call_command('reprocesar_estados_correo', stdout=simulacion)
        self.assertIn('Simulación', simulacion.getvalue())
        bitacora.refresh_from_db()
        self.assertEqual(bitacora.resultado, 'ERROR')

        with patch.object(servicio, 'procesar_estado_cuenta', real):
            call_command('reprocesar_estados_correo', '--aplicar', stdout=StringIO())
        bitacora.refresh_from_db()
        self.assertEqual((bitacora.resultado, bitacora.intentos), ('PROCESADO', 2), bitacora.detalle)
        self.assertEqual(EstadoCuentaBancario.objects.count(), 1)
        self.assertEqual(bitacora.estado_cuenta.estado, 'PROCESADO')
        abierto = Pendiente.objects.get(clave_idempotencia=f'estado_correo:{bitacora.pk}')
        self.assertEqual(abierto.estado, 'RESUELTO')

    def test_ocho_intentos_ya_no_se_reintenta(self):
        with patch.object(servicio, 'procesar_estado_cuenta', side_effect=RuntimeError('falla')):
            bitacora = self.procesar(self.correo_pdf())
            CorreoEstadoCuenta.objects.filter(pk=bitacora.pk).update(intentos=CorreoEstadoCuenta.MAX_INTENTOS)
            self.assertEqual(servicio.reprocesar_pendientes(aplicar=True), (0, 0))

    def test_saldos_que_no_encadenan_dejan_advertencia(self):
        EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta, periodo_mes=3, periodo_anio=2026, formato='PDF',
            archivo=io_archivo(b'marzo'), estado='PROCESADO', saldo_final_estado=Decimal('1.00'))
        bitacora = self.procesar(self.correo_pdf())
        self.assertEqual(bitacora.resultado, 'PROCESADO')
        self.assertIn('marzo 2026', bitacora.advertencia)

    def test_saldos_que_encadenan_no_dejan_advertencia(self):
        EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta, periodo_mes=3, periodo_anio=2026, formato='PDF',
            archivo=io_archivo(b'marzo'), estado='PROCESADO', saldo_final_estado=Decimal('6624.34'))
        self.assertEqual(self.procesar(self.correo_pdf()).advertencia, '')

    def test_solo_liga_se_ignora_y_abre_pendiente(self):
        bitacora = self.procesar(correo(cuerpo='Consulte su estado de cuenta en https://bbva.example/estado'))
        self.assertEqual(bitacora.resultado, 'IGNORADO')
        pendiente = Pendiente.objects.get(clave_idempotencia=f'estado_correo:{bitacora.pk}')
        self.assertEqual((pendiente.rol, pendiente.severidad), ('DIRECCION', 'URGENTE'))

    def test_adjunto_que_no_es_un_estado_de_cuenta_es_error(self):
        bitacora = self.procesar(correo([('x.pdf', b'%PDF-1.4 no es un estado', 'application/pdf')]))
        self.assertEqual(bitacora.resultado, 'ERROR')
        self.assertEqual(EstadoCuentaBancario.objects.count(), 0)

    def test_xml_sin_parser_cae_al_pdf(self):
        xml = b'<?xml version="1.0"?><estado/>'
        bitacora = self.procesar(correo([('e.xml', xml, 'application/xml'), ('e.pdf', self.pdf, 'application/pdf')]))
        self.assertEqual((bitacora.resultado, bitacora.estado_cuenta.formato), ('PROCESADO', 'PDF'))


class ZipYContrasenaTest(BaseCorreoTest):
    def setUp(self):
        super().setUp()
        self.clave = secrets.token_urlsafe(12)  # inventada: nunca la contraseña real

    def guardar_clave(self, clave):
        servicio.guardar_password_estado(self.cuenta, clave)
        self.cuenta.save()

    def test_zip_con_contrasena_se_abre_con_la_de_la_cuenta(self):
        self.guardar_clave(self.clave)
        mime = correo([('estado.zip', zip_con_clave([('estado.pdf', self.pdf)], self.clave), 'application/zip')])
        bitacora = self.procesar(mime)
        self.assertEqual(bitacora.resultado, 'PROCESADO', bitacora.detalle)

    def test_contrasena_incorrecta_es_error_y_pendiente_a_direccion(self):
        self.guardar_clave(secrets.token_urlsafe(12))
        mime = correo([('estado.zip', zip_con_clave([('estado.pdf', self.pdf)], self.clave), 'application/zip')])
        bitacora = self.procesar(mime)
        self.assertEqual(bitacora.resultado, 'ERROR')
        self.assertIn('Contraseña incorrecta', bitacora.detalle)
        self.assertEqual(EstadoCuentaBancario.objects.count(), 0)
        self.assertTrue(Pendiente.objects.filter(rol='DIRECCION', severidad='URGENTE').exists())

    def test_zip_con_contrasena_y_cuenta_sin_clave_es_error(self):
        mime = correo([('estado.zip', zip_con_clave([('estado.pdf', self.pdf)], self.clave), 'application/zip')])
        self.assertIn('no tiene una capturada', self.procesar(mime).detalle)

    def test_zip_sin_contrasena_tambien_vale(self):
        mime = correo([('estado.zip', zip_con_clave([('estado.pdf', self.pdf)], None), 'application/zip')])
        self.assertEqual(self.procesar(mime).resultado, 'PROCESADO')

    def test_pdf_cifrado_se_descifra_con_la_misma_contrasena(self):
        self.guardar_clave(self.clave)
        abierto = servicio._descifrar_pdf(pdf_cifrado(self.clave), [self.clave])
        self.assertNotIn(b'/Encrypt', abierto)
        with self.assertRaisesMessage(servicio.ErrorCorreo, 'Contraseña incorrecta'):
            servicio._descifrar_pdf(pdf_cifrado(self.clave), ['otra-clave'])
        with self.assertRaisesMessage(servicio.ErrorCorreo, 'no tiene una capturada'):
            servicio._descifrar_pdf(pdf_cifrado(self.clave), [])

    def test_la_contrasena_se_guarda_cifrada(self):
        self.guardar_clave(self.clave)
        self.cuenta.refresh_from_db()
        self.assertNotIn(self.clave.encode(), bytes(self.cuenta.password_estado_cifrada))
        self.assertEqual(servicio.password_estado(self.cuenta), self.clave)

    def test_zip_demasiado_grande_se_rechaza(self):
        grande = zip_con_clave([('a.pdf', b'0' * 1024) for _ in range(servicio.MAX_ZIP_MIEMBROS + 1)], None)
        with self.assertRaisesMessage(servicio.ErrorCorreo, 'demasiado grande'):
            servicio._leer_zip(grande, [])


class CuentaBancariaAdminTest(BaseCorreoTest):
    def setUp(self):
        super().setUp()
        self.super = User.objects.create_superuser('dir', 'd@example.com', secrets.token_hex(8))
        self.staff = User.objects.create_user('ayu', 'a@example.com', secrets.token_hex(8), is_staff=True)
        self.staff.user_permissions.add(*__import__('django.contrib.auth.models', fromlist=['Permission'])
                                        .Permission.objects.filter(content_type__app_label='contabilidad',
                                                                   content_type__model='cuentabancaria'))

    def test_solo_superusuario_ve_el_campo_y_nunca_se_muestra_el_valor(self):
        from core_erp.test_utils import login_superuser_con_totp
        login_superuser_con_totp(self.client, self.super)
        url = f'/admin/contabilidad/cuentabancaria/{self.cuenta.pk}/change/'
        clave = secrets.token_urlsafe(10)
        respuesta = self.client.get(url)
        self.assertContains(respuesta, 'password_estado')
        datos = {c: respuesta.context['adminform'].form.initial.get(c, '') or ''
                 for c in respuesta.context['adminform'].form.fields}
        datos.update(nombre='BBVA Maestra', banco='BBVA', numero_cuenta=NUMERO_CUENTA, clabe='012345678901234567',
                     saldo_inicial='0.00', activa='on', password_estado=clave)
        datos = {k: v for k, v in datos.items() if v is not None and v is not False}
        self.client.post(url, datos)
        self.cuenta.refresh_from_db()
        self.assertEqual(servicio.password_estado(self.cuenta), clave)
        self.assertNotContains(self.client.get(url), clave)

    def test_staff_sin_superusuario_no_ve_el_campo(self):
        self.client.force_login(self.staff)
        respuesta = self.client.get(f'/admin/contabilidad/cuentabancaria/{self.cuenta.pk}/change/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertNotContains(respuesta, 'password_estado')
        # y un POST armado a mano con el campo tampoco cambia la contraseña
        self.client.post(f'/admin/contabilidad/cuentabancaria/{self.cuenta.pk}/change/',
                         {'nombre': 'x', 'banco': 'BBVA', 'clabe': '012345678901234567', 'saldo_inicial': '0',
                          'activa': 'on', 'password_estado': 'intruso'})
        self.cuenta.refresh_from_db()
        self.assertFalse(self.cuenta.password_estado_cifrada)


@override_settings(ERP_SAT_FIEL_CIFRADO_SECRET=LLAVE, STORAGES=STORAGE_MEMORIA, ERP_WA_NEGOCIO_NUMERO='529990000001',
                   ERP_ESTADOS_CORREO_SECRET=SECRETO_WEBHOOK)
class WebhookTest(BaseCorreoTest):
    URL = '/api/contabilidad/estados-cuenta/correo/'

    def post(self, cuerpo=None, secreto=SECRETO_WEBHOOK, marca=None, firma=None):
        cuerpo = cuerpo if cuerpo is not None else self.correo_pdf()
        marca = str(int(time.time()) if marca is None else marca)
        firma = firma or hmac.new((secreto or 'x').encode(), marca.encode() + b'.' + cuerpo,
                                  hashlib.sha256).hexdigest()
        return self.client.post(self.URL, data=cuerpo, content_type='message/rfc822',
                                headers={'X-QKT-Timestamp': marca, 'X-QKT-Firma': firma})

    def test_firma_correcta_responde_202_y_guarda_el_correo(self):
        with patch.object(servicio, 'lanzar_procesamiento') as lanzar, self.captureOnCommitCallbacks(execute=True):
            respuesta = self.post()
        self.assertEqual(respuesta.status_code, 202)
        self.assertEqual(CorreoEstadoCuenta.objects.count(), 1)
        lanzar.assert_called_once_with(CorreoEstadoCuenta.objects.get().pk)

    def test_mismo_correo_no_vuelve_a_procesarse(self):
        cuerpo = self.correo_pdf()
        with patch.object(servicio, 'lanzar_procesamiento') as lanzar, self.captureOnCommitCallbacks(execute=True):
            self.post(cuerpo)
            self.post(cuerpo)
        self.assertEqual(lanzar.call_count, 1)
        self.assertEqual(CorreoEstadoCuenta.objects.count(), 1)

    def test_rechazos_con_403_y_nada_guardado(self):
        casos = {
            'firma mala': dict(firma='0' * 64),
            'timestamp viejo': dict(marca=int(time.time()) - 600),
            'timestamp futuro': dict(marca=int(time.time()) + 600),
            'secreto distinto': dict(secreto='otro-secreto'),
        }
        for nombre, kw in casos.items():
            with self.subTest(nombre):
                self.assertEqual(self.post(**kw).status_code, 403)
        sin_cabeceras = self.client.post(self.URL, data=b'x', content_type='message/rfc822')
        self.assertEqual(sin_cabeceras.status_code, 403)
        self.assertEqual(CorreoEstadoCuenta.objects.count(), 0)

    def test_sin_secreto_configurado_todo_es_403(self):
        with override_settings(ERP_ESTADOS_CORREO_SECRET=''):
            self.assertEqual(self.post(secreto='').status_code, 403)
        self.assertEqual(CorreoEstadoCuenta.objects.count(), 0)

    def test_solo_post(self):
        self.assertEqual(self.client.get(self.URL).status_code, 405)

    def test_cuerpo_de_mas_de_15_mb_es_413(self):
        with patch('contabilidad.views_estados_correo.MAX_CUERPO', 100):
            self.assertEqual(self.post(cuerpo=b'x' * 101).status_code, 413)
        self.assertEqual(CorreoEstadoCuenta.objects.count(), 0)


class NoLlegoTest(BaseCorreoTest):
    def test_antes_del_dia_7_no_avisa(self):
        self.assertEqual(servicio.avisar_si_no_llego(date(2026, 11, 6)), 0)
        self.assertFalse(Pendiente.objects.exists())

    def test_avisa_una_sola_vez_por_mes(self):
        for dia in (7, 8, 20):
            servicio.avisar_si_no_llego(date(2026, 11, dia))
        pendientes = Pendiente.objects.filter(clave_idempotencia__startswith='estado_no_llego:')
        self.assertEqual(pendientes.count(), 1)
        self.assertIn('octubre 2026', pendientes.get().titulo)
        servicio.avisar_si_no_llego(date(2026, 12, 7))
        self.assertEqual(pendientes.count(), 2)

    def test_si_ya_hay_estado_del_mes_no_avisa(self):
        EstadoCuentaBancario.objects.create(
            cuenta_bancaria=self.cuenta, periodo_mes=10, periodo_anio=2026, formato='PDF',
            archivo=io_archivo(b'octubre'), estado='PROCESADO')
        self.assertEqual(servicio.avisar_si_no_llego(date(2026, 11, 9)), 0)

    def test_el_comando_con_aplicar_avisa_y_enero_mira_diciembre(self):
        with patch('contabilidad.services_estados_correo.timezone.localdate', return_value=date(2027, 1, 8)):
            salida = StringIO()
            call_command('reprocesar_estados_correo', '--aplicar', stdout=salida)
        self.assertIn('1 aviso', salida.getvalue())
        self.assertIn('diciembre 2026', Pendiente.objects.get().titulo)


class ProgramadorTest(TestCase):
    def test_el_reproceso_esta_en_la_tabla_del_programador(self):
        from core_erp.programador import POR_NOMBRE
        tarea = POR_NOMBRE['reprocesar_estados_correo']
        self.assertEqual((tarea.comando, tarea.args), ('reprocesar_estados_correo', ('--aplicar',)))


def io_archivo(contenido):
    from django.core.files.base import ContentFile
    return ContentFile(contenido, name='estado.pdf')
