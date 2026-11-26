"""Hoja de operación del evento: servicio, vista, acción de admin y comando."""
import shutil
import tempfile
from datetime import date, time, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.contrib.auth.models import Group, Permission, User
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from comercial.models import (
    Cliente,
    ContratoServicio,
    Cotizacion,
    DepositoGarantia,
    FirmaContrato,
    Insumo,
    ItemCotizacion,
    MovimientoDeposito,
    Pago,
    Producto,
)
from core_erp.documentos import contexto_documento
from core_erp.test_utils import login_superuser_con_totp
from nomina.models import Empleado

from .models import ItemChecklist, PlantillaChecklist, TareaProgramada
from .services_hoja import (
    MAX_HOJAS,
    armar_contexto_hoja,
    contexto_documento_hoja,
    cotizaciones_operables,
    generar_hoja_pdf,
)

PRECIO_CENTINELA = Decimal('98765.43')
RFC_CENTINELA = 'XAXX010101ZZZ'
EMAIL_CENTINELA = 'secreto.cliente@example.com'
PROVEEDOR_CENTINELA = 'Proveedor Reservado SA'
COSTO_CENTINELA = Decimal('7654.32')
FECHA = date(2026, 11, 27)  # viernes


def crear_cotizacion(nombre='Boda de Ana', fecha=FECHA, estado='CONFIRMADA', tipo='EVENTO',
                     precio=Decimal('1000.00'), hora_inicio=time(14, 0), hora_fin=time(22, 0), **extra):
    cliente = Cliente.objects.create(
        nombre=f'Cliente {nombre}', tipo_persona='FISICA', telefono='9991234567',
        email=EMAIL_CENTINELA, rfc=RFC_CENTINELA, razon_social='Razón Fiscal Secreta',
    )
    cot = Cotizacion.objects.create(
        cliente=cliente, nombre_evento=nombre, tipo_servicio=tipo, fecha_evento=fecha,
        hora_inicio=hora_inicio, hora_fin=hora_fin, num_personas=80, **extra,
    )
    # `update()`: sin signals (contrato, tareas, pólizas) y con el precio exacto que cada prueba necesita.
    Cotizacion.objects.filter(pk=cot.pk).update(estado=estado, precio_final=precio)
    return Cotizacion.objects.get(pk=cot.pk)


def agregar_item(cot, grupo='MOBILIARIO', nombre='Mesa redonda', cantidad=10, precio=PRECIO_CENTINELA):
    producto = Producto.objects.create(nombre=nombre, grupo_cotizador=grupo, precio_venta_fijo=Decimal('100.00'))
    item = ItemCotizacion.objects.create(cotizacion=cot, producto=producto, cantidad=cantidad, precio_unitario=precio)
    Cotizacion.objects.filter(pk=cot.pk).update(precio_final=cot.precio_final)
    return item


def agregar_pago(cot, monto, tipo='INGRESO', concepto='VENTA'):
    # `bulk_create`: sin validación de saldo ni signals de póliza/factura.
    Pago.objects.bulk_create([Pago(cotizacion=cot, monto=Decimal(monto), metodo='EFECTIVO', tipo=tipo,
                                   concepto=concepto)])


def recargar(cot):
    return cotizaciones_operables().get(pk=cot.pk)


def html_de(cotizaciones):
    return render_to_string(
        'operaciones/pdf_hoja_operacion.html',
        {**contexto_documento(), **contexto_documento_hoja(cotizaciones)})


class ContextoHojaTest(TestCase):
    def test_contexto_completo(self):
        cot = crear_cotizacion(precio=Decimal('5000.00'), notas_cliente='Alergia a los cacahuates',
                               incluye_cerveza=True, horas_servicio=6)
        agregar_item(cot)
        cot = Cotizacion.objects.get(pk=cot.pk)
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('5000.00'))
        agregar_pago(cot, '2000.00')
        barman = Insumo.objects.create(nombre='Bartender Pedro', unidad_medida='servicio', costo_unitario=COSTO_CENTINELA)
        Cotizacion.objects.filter(pk=cot.pk).update(insumo_barman=barman)

        ctx = armar_contexto_hoja(recargar(cot))

        self.assertEqual(ctx['folio'], f'COT-{cot.pk:03d}')
        self.assertEqual(ctx['fecha_larga'], 'viernes 27 de noviembre de 2026')
        self.assertEqual(ctx['hora_inicio'], '2:00 p.m.')
        self.assertEqual(ctx['horario'], '2:00 p.m. a 10:00 p.m.')
        self.assertEqual(ctx['cliente_telefono'], '9991234567')
        self.assertEqual(ctx['servicios'][0], {'grupo': 'Mobiliario', 'lineas': [
            {'descripcion': 'Mesa redonda', 'cantidad': '10'}]})
        self.assertEqual(ctx['barra']['barman'], 'Bartender Pedro')
        self.assertEqual(ctx['barra']['horas'], 6)
        self.assertTrue(ctx['barra']['insumos'])
        self.assertEqual(ctx['notas_cliente'], 'Alergia a los cacahuates')
        self.assertEqual(ctx['saldo'], Decimal('3000.00'))
        self.assertTrue(ctx['por_cobrar'])
        self.assertEqual(ctx['aforo_maximo'], 150)
        self.assertEqual([h for _, h in ctx['cronograma']],
                         ['11:00 a.m.', '12:00 p.m.', '2:00 p.m.', '10:00 p.m.', '11:00 p.m.'])

    def test_sin_hora_fin_deja_renglones_en_blanco(self):
        cot = crear_cotizacion(hora_fin=None)
        ctx = armar_contexto_hoja(recargar(cot))
        self.assertEqual(ctx['horario'], '2:00 p.m. a ____')
        self.assertEqual(dict(ctx['cronograma'])['Cierre del servicio'], '____')
        self.assertEqual(dict(ctx['cronograma'])['Desmontaje'], '____')
        self.assertIn('____', html_de([recargar(cot)]))

    def test_sin_hora_inicio_deja_en_blanco_las_llegadas(self):
        cot = crear_cotizacion(hora_inicio=None)
        crono = dict(armar_contexto_hoja(recargar(cot))['cronograma'])
        self.assertEqual(crono['Llegada del equipo'], '____')
        self.assertEqual(crono['Inicio del servicio'], '____')

    def test_sin_items_ni_pagos(self):
        cot = crear_cotizacion(precio=Decimal('1000.00'))
        ctx = armar_contexto_hoja(recargar(cot))
        self.assertEqual(ctx['servicios'], [])
        self.assertEqual(ctx['saldo'], Decimal('1000.00'))
        self.assertIn('Sin servicios capturados', html_de([recargar(cot)]))

    def test_item_sin_producto_va_a_otros(self):
        cot = crear_cotizacion()
        ItemCotizacion.objects.create(cotizacion=cot, descripcion='Servicio suelto', cantidad=2,
                                      precio_unitario=Decimal('10.00'))
        ctx = armar_contexto_hoja(recargar(cot))
        self.assertEqual(ctx['servicios'][0]['grupo'], 'Otros')

    def test_saldo_en_el_borde_de_la_tolerancia(self):
        casos = {'0.00': False, '0.49': False, '0.50': False, '0.51': True}
        for saldo, por_cobrar in casos.items():
            with self.subTest(saldo=saldo):
                cot = crear_cotizacion(nombre=f'Saldo {saldo}', precio=Decimal('1000.00'))
                agregar_pago(cot, Decimal('1000.00') - Decimal(saldo))
                ctx = armar_contexto_hoja(recargar(cot))
                self.assertEqual(ctx['saldo'], Decimal(saldo))
                self.assertEqual(ctx['por_cobrar'], por_cobrar)
                html = html_de([recargar(cot)])
                if por_cobrar:
                    self.assertIn('Cobrar antes de iniciar el servicio', html)
                    self.assertNotIn('Pagado en su totalidad', html)
                else:
                    self.assertIn('Pagado en su totalidad', html)
                    self.assertNotIn('Cobrar antes de iniciar el servicio', html)

    def test_saldo_coincide_con_saldo_pendiente_del_modelo(self):
        cot = crear_cotizacion(precio=Decimal('1000.00'))
        agregar_pago(cot, '700.00')
        agregar_pago(cot, '100.00', tipo='REEMBOLSO')
        agregar_pago(cot, '50.00', concepto='EXTRA')  # propina: no cuenta
        self.assertEqual(armar_contexto_hoja(recargar(cot))['saldo'], cot.saldo_pendiente())
        self.assertEqual(cot.saldo_pendiente(), Decimal('400.00'))

    def test_deposito_con_y_sin(self):
        sin = crear_cotizacion(nombre='Sin deposito')
        self.assertIsNone(armar_contexto_hoja(recargar(sin))['deposito'])
        self.assertIn('No aplica', html_de([recargar(sin)]))

        con = crear_cotizacion(nombre='Con deposito')
        dep = DepositoGarantia.objects.create(cotizacion=con, monto=Decimal('2500.00'))
        ctx = armar_contexto_hoja(recargar(con))
        self.assertEqual(ctx['deposito']['monto'], Decimal('2500.00'))
        self.assertEqual(ctx['deposito']['estado'], 'Pendiente de recibir')
        self.assertIn('Pendiente de recibir', html_de([recargar(con)]))
        self.assertEqual(dep.pk, DepositoGarantia.objects.get(cotizacion=con).pk)

    def test_contrato_firmado_sin_y_con_firma(self):
        cot = crear_cotizacion()
        self.assertFalse(armar_contexto_hoja(recargar(cot))['contrato_firmado'])
        contrato = ContratoServicio.objects.create(cotizacion=cot, numero='CONT-TEST-1', archivo='contratos_pdf/x.pdf')
        self.assertFalse(armar_contexto_hoja(recargar(cot))['contrato_firmado'])
        firma = FirmaContrato.objects.create(contrato=contrato)
        self.assertFalse(armar_contexto_hoja(recargar(cot))['contrato_firmado'])
        firma.firmado_en = timezone.now()
        firma.save()
        self.assertTrue(armar_contexto_hoja(recargar(cot))['contrato_firmado'])

    def test_arrendamiento_no_tiene_aforo(self):
        cot = crear_cotizacion(tipo='ARRENDAMIENTO')
        ctx = armar_contexto_hoja(recargar(cot))
        self.assertIsNone(ctx['aforo_maximo'])
        self.assertIn('Aforo máximo: No aplica', html_de([recargar(cot)]))

    def test_sin_barra(self):
        cot = crear_cotizacion()
        self.assertIsNone(armar_contexto_hoja(recargar(cot))['barra'])
        self.assertIn('Sin barra contratada', html_de([recargar(cot)]))

    def test_checklist_con_hora_de_entrada(self):
        empleado = Empleado.objects.create(nombre='Juan', telefono='5555550001')
        plantilla = PlantillaChecklist.objects.create(
            nombre='Preparación — Evento', tipo='TURNOVER_EVENTO', responsable_default=empleado)
        ItemChecklist.objects.create(plantilla=plantilla, orden=2, texto='Barrer el salón')
        ItemChecklist.objects.create(plantilla=plantilla, orden=1, texto='Acomodar mesas')
        cot = crear_cotizacion()
        TareaProgramada.objects.create(plantilla=plantilla, cotizacion=cot, fecha=cot.fecha_evento,
                                       hora_entrada=time(8, 0), hora_limite=time(14, 0))
        ctx = armar_contexto_hoja(recargar(cot))
        self.assertEqual(ctx['checklist']['items'], ['Acomodar mesas', 'Barrer el salón'])
        self.assertEqual(ctx['checklist']['hora_entrada'], '8:00 a.m.')
        self.assertIn('Entrada del personal: 8:00 a.m.', html_de([recargar(cot)]))

    def test_sin_plantilla_no_hay_checklist(self):
        cot = crear_cotizacion()
        self.assertIsNone(armar_contexto_hoja(recargar(cot))['checklist'])


class InformacionReservadaTest(TestCase):
    """Precios, costos, proveedores y datos fiscales del cliente nunca salen en la hoja."""

    def test_centinelas_ausentes_del_contexto_y_del_html(self):
        cot = crear_cotizacion(incluye_cerveza=True, incluye_licor_nacional=True, precio=Decimal('4321.00'))
        agregar_item(cot, precio=PRECIO_CENTINELA)
        insumo = Insumo.objects.create(nombre='Bartender Pedro', unidad_medida='servicio',
                                       costo_unitario=COSTO_CENTINELA)
        Cotizacion.objects.filter(pk=cot.pk).update(insumo_barman=insumo, precio_final=Decimal('4321.00'))
        cot = recargar(cot)

        contexto = armar_contexto_hoja(cot)
        html = html_de([cot])
        for centinela in ('98765.43', '98,765.43', RFC_CENTINELA, EMAIL_CENTINELA, 'Razón Fiscal Secreta',
                          '7654.32', '7,654.32', PROVEEDOR_CENTINELA, 'Sin proveedor asignado',
                          'Configurar en Plantilla de Barra'):
            with self.subTest(centinela=centinela):
                self.assertNotIn(centinela, repr(contexto))
                self.assertNotIn(centinela, html)
        self.assertNotIn('costo', repr(contexto['barra']).lower())
        self.assertNotIn('proveedor', repr(contexto['barra']).lower())

    def test_html_escapa_texto_libre(self):
        cot = crear_cotizacion(nombre='<script>alert(1)</script>', notas_cliente='<b>nota</b>')
        html = html_de([recargar(cot)])
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&lt;b&gt;nota&lt;/b&gt;', html)


class OperablesTest(TestCase):
    def test_incluye_y_excluye_y_ordena(self):
        tarde = crear_cotizacion(nombre='Tarde', fecha=FECHA, hora_inicio=time(18, 0))
        temprano = crear_cotizacion(nombre='Temprano', fecha=FECHA, hora_inicio=time(9, 0))
        manana = crear_cotizacion(nombre='Mañana', fecha=FECHA + timedelta(days=1), estado='EJECUTADA',
                                  tipo='PASADIA')
        crear_cotizacion(nombre='Borrador', estado='BORRADOR')
        crear_cotizacion(nombre='Cancelada', estado='CANCELADA')
        crear_cotizacion(nombre='Cerrada', estado='CERRADA')
        crear_cotizacion(nombre='Hospedaje borrador', tipo='HOSPEDAJE', estado='BORRADOR')

        self.assertEqual([c.pk for c in cotizaciones_operables()], [temprano.pk, tarde.pk, manana.pk])
        self.assertEqual([c.pk for c in cotizaciones_operables([FECHA])], [temprano.pk, tarde.pk])
        self.assertEqual(list(cotizaciones_operables([date(2030, 1, 1)])), [])


class HojaHospedajeTest(TestCase):
    def test_hospedaje_confirmado_tiene_hoja_con_entrada_y_salida(self):
        cot = crear_cotizacion(nombre='Estancia', tipo='HOSPEDAJE', hora_inicio=time(14, 0), hora_fin=time(10, 0),
                               fecha_salida=FECHA + timedelta(days=2))
        self.assertIn(cot.pk, {c.pk for c in cotizaciones_operables()})
        html = html_de(cotizaciones_operables().filter(pk=cot.pk))
        self.assertIn('Entrada 2:00 p.m. · Salida 10:00 a.m.', html)
        self.assertIn('al domingo 29 de noviembre de 2026', html)
        self.assertIn('Revisión de habitaciones', html)
        self.assertNotIn('Llegada de proveedores', html)


class PdfHojaTest(TestCase):
    def test_pdf_real_de_una_hoja(self):
        cot = crear_cotizacion(incluye_cerveza=True)
        agregar_item(cot)
        pdf = generar_hoja_pdf(cotizaciones_operables().filter(pk=cot.pk), usuario=User(username='encargada'))
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_varias_hojas_llevan_portada_indice(self):
        a = crear_cotizacion(nombre='Evento A')
        b = crear_cotizacion(nombre='Evento B', fecha=FECHA + timedelta(days=1))
        html = html_de(cotizaciones_operables())
        self.assertIn('Agenda de operación', html)
        self.assertIn('Evento A', html)
        self.assertIn('Evento B', html)
        self.assertIn('Sábado 28 de noviembre de 2026', html)
        self.assertEqual(html.count('Antes del servicio'), 2)
        self.assertEqual({a.pk, b.pk}, {c.pk for c in cotizaciones_operables()})

    def test_una_sola_hoja_no_lleva_portada(self):
        crear_cotizacion()
        self.assertNotIn('Agenda de operación', html_de(cotizaciones_operables()))

    def test_consultas_constantes_al_crecer_las_hojas(self):
        def consultas():
            with CaptureQueriesContext(connection) as capturadas:
                contexto_documento_hoja(cotizaciones_operables())
            return len(capturadas)

        agregar_item(crear_cotizacion(nombre='Uno'), nombre='Silla uno')
        con_una = consultas()
        for n in range(4):
            cot = crear_cotizacion(nombre=f'Extra {n}')
            agregar_item(cot, nombre=f'Silla {n}')
        # Mismas consultas con cinco hojas que con una (sin barra ni depósito): todo va precargado.
        self.assertEqual(consultas(), con_una)
        self.assertLessEqual(con_una, 8)

    def test_tope_de_hojas_definido(self):
        self.assertEqual(MAX_HOJAS, 15)


class VistaHojaTest(TestCase):
    def setUp(self):
        self.cot = crear_cotizacion()
        self.url = f'/admin/comercial/cotizacion/{self.cot.pk}/hoja-operacion/'
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')

    def _staff_con_permiso(self, nombre='enc'):
        usuario = User.objects.create_user(nombre, f'{nombre}@example.com', 'x-segura-123', is_staff=True)
        usuario.user_permissions.add(Permission.objects.get(codename='view_cotizacion'))
        self.client.force_login(usuario)
        return usuario

    def test_anonimo_va_al_login(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 302)
        self.assertIn('login', respuesta['Location'])

    def test_staff_sin_permiso_recibe_403(self):
        usuario = User.objects.create_user('sinperm', 's@example.com', 'x-segura-123', is_staff=True)
        self.client.force_login(usuario)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_vendedor_recibe_403(self):
        usuario = self._staff_con_permiso('vende')
        usuario.groups.add(Group.objects.create(name='Vendedor'))
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_cancelada_o_inexistente_da_404(self):
        login_superuser_con_totp(self.client, self.admin)
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='CANCELADA')
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get('/admin/comercial/cotizacion/999999/hoja-operacion/').status_code, 404)

    def test_staff_con_permiso_recibe_pdf_en_linea(self):
        self._staff_con_permiso()
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(respuesta.content.startswith(b'%PDF'))
        self.assertTrue(respuesta['Content-Disposition'].startswith('inline'))
        self.assertIn(f'QKT_HojaOperacion_COT-{self.cot.pk:03d}.pdf', respuesta['Content-Disposition'])

    def test_descargar_es_attachment(self):
        login_superuser_con_totp(self.client, self.admin)
        respuesta = self.client.get(self.url + '?descargar=1')
        self.assertTrue(respuesta['Content-Disposition'].startswith('attachment'))

    def test_boton_en_el_detalle_solo_si_operable_y_no_vendedor(self):
        login_superuser_con_totp(self.client, self.admin)
        detalle = f'/admin/comercial/cotizacion/{self.cot.pk}/change/'
        self.assertContains(self.client.get(detalle), self.url)
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='BORRADOR')
        self.assertNotContains(self.client.get(detalle), self.url)


class AccionHojaTest(TestCase):
    URL = '/admin/comercial/cotizacion/'

    def setUp(self):
        self.admin = User.objects.create_superuser('dir', 'dir@example.com', 'x-segura-123')
        login_superuser_con_totp(self.client, self.admin)

    def _accion(self, cotizaciones):
        return self.client.post(self.URL, {
            'action': 'hoja_operacion_pdf', '_selected_action': [c.pk for c in cotizaciones]}, follow=True)

    def _mensajes(self, respuesta):
        return [str(m) for m in get_messages(respuesta.wsgi_request)]

    def test_una_cotizacion(self):
        cot = crear_cotizacion()
        respuesta = self._accion([cot])
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertIn(f'COT-{cot.pk:03d}', respuesta['Content-Disposition'])

    def test_varias_en_un_solo_pdf_con_agenda(self):
        a = crear_cotizacion(nombre='A')
        b = crear_cotizacion(nombre='B', fecha=FECHA + timedelta(days=2))
        respuesta = self._accion([a, b])
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertIn('QKT_AgendaOperacion_20261127_20261129.pdf', respuesta['Content-Disposition'])

    def test_mas_de_quince_no_genera(self):
        cots = [crear_cotizacion(nombre=f'C{n}') for n in range(MAX_HOJAS + 1)]
        respuesta = self._accion(cots)
        self.assertNotEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(any('máximo 15' in m for m in self._mensajes(respuesta)))

    def test_ninguna_operable_da_error(self):
        cot = crear_cotizacion(estado='BORRADOR')
        respuesta = self._accion([cot])
        self.assertNotEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertTrue(any('Ninguna de las seleccionadas' in m for m in self._mensajes(respuesta)))

    def test_mezcla_omite_con_aviso(self):
        buena = crear_cotizacion(nombre='Buena')
        mala = crear_cotizacion(nombre='Mala', estado='CANCELADA')
        respuesta = self._accion([buena, mala])
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        # Los mensajes quedan pendientes para la siguiente pantalla del admin.
        siguiente = self.client.get(self.URL)
        self.assertTrue(any(f'COT-{mala.pk:03d}' in str(m) for m in get_messages(siguiente.wsgi_request)))

    def test_la_accion_no_aparece_para_vendedor(self):
        usuario = User.objects.create_user('vende', 'v@example.com', 'x-segura-123', is_staff=True)
        usuario.groups.add(Group.objects.create(name='Vendedor'))
        usuario.user_permissions.add(Permission.objects.get(codename='view_cotizacion'))
        self.client.force_login(usuario)
        cot = crear_cotizacion()
        respuesta = self._accion([cot])
        self.assertNotEqual(respuesta['Content-Type'], 'application/pdf')


class ComandoHojaTest(TestCase):
    def setUp(self):
        self.salida = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.salida, ignore_errors=True)

    def _correr(self, fechas, **extra):
        salida = StringIO()
        call_command('hoja_operacion', fechas=fechas, salida=str(self.salida), stdout=salida, **extra)
        return salida.getvalue()

    def test_un_pdf_por_cotizacion_y_la_agenda(self):
        a = crear_cotizacion(nombre='A')
        b = crear_cotizacion(nombre='B', fecha=FECHA + timedelta(days=1))
        crear_cotizacion(nombre='Fuera', fecha=FECHA + timedelta(days=5))
        self._correr('2026-11-27, 2026-11-28')
        nombres = sorted(p.name for p in self.salida.iterdir())
        self.assertEqual(nombres, sorted([
            f'QKT_HojaOperacion_20261127_COT-{a.pk:03d}.pdf',
            f'QKT_HojaOperacion_20261128_COT-{b.pk:03d}.pdf',
            'AGENDA_20261127_20261128.pdf',
        ]))
        for ruta in self.salida.iterdir():
            self.assertTrue(ruta.read_bytes().startswith(b'%PDF'))

    def test_crea_la_carpeta_si_falta(self):
        crear_cotizacion()
        destino = self.salida / 'nueva' / 'carpeta'
        call_command('hoja_operacion', fechas='2026-11-27', salida=str(destino), stdout=StringIO())
        self.assertTrue(any(destino.iterdir()))

    def test_fecha_invalida(self):
        with self.assertRaisesMessage(CommandError, 'Fecha inválida'):
            self._correr('2026-13-45')
        self.assertEqual(list(self.salida.iterdir()), [])

    def test_sin_resultados(self):
        crear_cotizacion(estado='BORRADOR')
        with self.assertRaisesMessage(CommandError, 'No hay eventos operables'):
            self._correr('2026-11-27')
        self.assertEqual(list(self.salida.iterdir()), [])


class HojaOperacionACargoTest(TestCase):
    """El día del servicio la hoja dice quién está a cargo: encargado (o suplente) y equipo de apoyo vigentes."""

    def test_trae_encargado_y_apoyo_vigentes_ese_dia(self):
        from comunicacion.models import Responsable
        from operaciones.services_hoja import _a_cargo

        dia = date(2026, 12, 12)
        Responsable.objects.create(rol='ENCARGADO', nombre='Hermano', telefono_wa='529995550021',
                                   vigente_desde=dia - timedelta(days=3), vigente_hasta=dia + timedelta(days=3))
        Responsable.objects.create(rol='APOYO', nombre='César', telefono_wa='529995550025', avisa_llegadas=True,
                                   vigente_desde=dia - timedelta(days=3), vigente_hasta=dia + timedelta(days=3))
        a_cargo = _a_cargo(dia)
        self.assertEqual(a_cargo['encargado'], {'nombre': 'Hermano', 'telefono': '999 555 0021'})
        self.assertEqual([p['nombre'] for p in a_cargo['apoyo']], ['César'])
        self.assertEqual(a_cargo['apoyo'][0]['temas'], 'Llegadas')
        self.assertEqual(_a_cargo(dia + timedelta(days=30))['apoyo'], [])
