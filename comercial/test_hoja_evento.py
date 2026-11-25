"""Hoja del evento en PDF: qué, cuándo, dónde, quién y cómo de una cotización."""
import io
from datetime import date, time, timedelta
from decimal import Decimal

import pdfplumber
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from comercial.models import AsignacionEspacio, AsignacionPersonal, Cliente, Cotizacion, Espacio, Pago
from comunicacion.models import Responsable
from core_erp.test_utils import login_superuser_con_totp
from nomina.models import Empleado
from operaciones.models import ItemChecklist, PlantillaChecklist
from operaciones.services import generar_tarea_turnover

FECHA = date.today() + timedelta(days=20)


def _texto(respuesta):
    with pdfplumber.open(io.BytesIO(respuesta.content)) as pdf:
        return '\n'.join(p.extract_text() or '' for p in pdf.pages)


class HojaEventoTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.usuario = User.objects.create_superuser('direccion', 'd@x.mx', 'clave-de-prueba-123')
        cliente = Cliente.objects.create(nombre='Ana Pech Canul', telefono='9995550001', email='ana@x.mx')
        cls.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Boda Pech', tipo_servicio='EVENTO', num_personas=80,
            fecha_evento=FECHA, hora_inicio=time(17, 0), hora_fin=time(23, 0),
            notas_cliente='Llevan su propio pastel.')
        Cotizacion.objects.filter(pk=cls.cot.pk).update(precio_final=Decimal('20000.00'))
        cls.cot.refresh_from_db()
        Pago.objects.create(cotizacion=cls.cot, monto=Decimal('5000.00'), metodo='TRANSFERENCIA', tipo='INGRESO')
        irving = Empleado.objects.create(nombre='Irving Mesero', telefono='9995550023')
        AsignacionPersonal.objects.create(cotizacion=cls.cot, empleado=irving, rol='MESERO', fecha=FECHA,
                                          hora_inicio=time(16, 0), hora_fin=time(23, 30))
        jardin = Espacio.objects.create(nombre='Jardín principal', tipo='JARDIN')
        AsignacionEspacio.objects.create(cotizacion=cls.cot, espacio=jardin, fecha=FECHA,
                                         hora_inicio=time(17, 0), hora_fin=time(23, 0))
        Responsable.objects.create(rol='ENCARGADO', nombre='Hermano', telefono_wa='529995550021',
                                   vigente_desde=FECHA - timedelta(days=5), vigente_hasta=FECHA + timedelta(days=5))
        Responsable.objects.create(rol='APOYO', nombre='César', telefono_wa='529995550025', avisa_llegadas=True,
                                   vigente_desde=FECHA - timedelta(days=5), vigente_hasta=FECHA + timedelta(days=5))
        plantilla = PlantillaChecklist.objects.create(nombre='Preparación del evento', tipo='TURNOVER_EVENTO',
                                                      responsable_default=irving, duracion_estimada_horas=2)
        ItemChecklist.objects.create(plantilla=plantilla, orden=1, texto='Montar mesas en el jardín')
        generar_tarea_turnover(cls.cot)

    def setUp(self):
        login_superuser_con_totp(self.client, self.usuario)

    def test_pdf_con_que_cuando_donde_quien_y_como(self):
        r = self.client.get(reverse('cotizacion_hoja_evento', args=[self.cot.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(f'QKT_HojaEvento_COT-{self.cot.pk:03d}.pdf', r['Content-Disposition'])
        texto = _texto(r)
        for esperado in ('Hoja del evento', 'Boda Pech', '5:00 p.m. a 11:00 p.m.', 'Umán',
                         'Jardín principal', 'Ana Pech Canul', '999 555 0001', 'Irving Mesero', 'Mesero',
                         'Hermano', 'César', 'Llevan su propio pastel.', '$15,000.00'):
            with self.subTest(esperado=esperado):
                self.assertIn(esperado, texto)

    def test_incluye_el_checklist_de_preparacion(self):
        self.assertTrue(self.cot.tareas_operativas.exists())
        texto = _texto(self.client.get(reverse('cotizacion_hoja_evento', args=[self.cot.pk])))
        self.assertIn('Montar mesas en el jardín', texto)

    def test_sin_personal_lo_avisa(self):
        cot = Cotizacion.objects.create(cliente=self.cot.cliente, nombre_evento='Pasadía', tipo_servicio='PASADIA',
                                        num_personas=20, fecha_evento=FECHA + timedelta(days=40))
        texto = _texto(self.client.get(reverse('cotizacion_hoja_evento', args=[cot.pk])))
        self.assertIn('Sin personal asignado', texto)
        self.assertIn('Horario por confirmar', texto)

    def test_enlaces_en_cotizacion_y_calendario(self):
        url = reverse('cotizacion_hoja_evento', args=[self.cot.pk])
        cambio = self.client.get(reverse('admin:comercial_cotizacion_change', args=[self.cot.pk]))
        self.assertContains(cambio, url)
        lista = self.client.get(reverse('admin:comercial_cotizacion_changelist'))
        self.assertContains(lista, url)
        eventos = self.client.get(reverse('calendario_unificado_eventos'),
                                  {'start': (FECHA - timedelta(days=1)).isoformat(),
                                   'end': (FECHA + timedelta(days=1)).isoformat()}).json()
        self.assertIn(url, [e.get('extendedProps', {}).get('hoja') for e in eventos])
