"""
Contrato de Hospedaje propio (basado en el modelo PROFECO) y retiro de
Arrendamiento de Mobiliario de la generación de contratos.

Ejecutar: python manage.py test comercial.test_contrato_hospedaje
"""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from comercial.models import Cliente, ContratoServicio, Cotizacion, ItemCotizacion, Producto
from comercial.services import ContratoService
from core_erp.test_utils import login_superuser_con_totp


def _html_del_contrato(cot):
    """HTML que ContratoService manda a WeasyPrint, sin generar el PDF."""
    with patch('weasyprint.HTML') as html:
        html.return_value.write_pdf.return_value = b'%PDF'
        ContratoService(cot).generar()
    return html.call_args.kwargs['string']


# Contratos basados en el modelo PROFECO (CONTRATO_PROPIO_ACTIVO=False).
@override_settings(CONTRATO_PROPIO_ACTIVO=False)
class ContratoHospedajeTest(TestCase):
    def setUp(self):
        cliente = Cliente.objects.create(nombre='Ana Ruiz', tipo_persona='FISICA')
        self.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Hospedaje (2 noches)', tipo_servicio='HOSPEDAJE',
            fecha_evento=date(2026, 11, 10), fecha_salida=date(2026, 11, 12), num_personas=6,
        )
        kaan = Producto.objects.create(
            nombre="Ka'an Room", precio_venta_fijo=Decimal('780.00'),
            rol_cotizador='HABITACION_HOSPEDAJE', capacidad_base_hospedaje=4,
        )
        ItemCotizacion.objects.create(cotizacion=self.cot, producto=kaan, cantidad=2)

    def test_usa_la_plantilla_de_hospedaje(self):
        html = _html_del_contrato(self.cot)
        self.assertIn('Contrato de Prestación de Servicios de Hospedaje', html)
        self.assertIn('EL HUÉSPED', html)
        self.assertNotIn('Arrendamiento de Salón', html)

    def test_no_se_ampara_en_el_registro_de_eventos(self):
        # El 9341-2023 solo cubre Evento y Pasadía.
        html = _html_del_contrato(self.cot)
        self.assertNotIn('9341-2023', html)
        self.assertNotIn('registrado por la Procuraduría', html)

    @override_settings(PROFECO_REGISTRO_HOSPEDAJE='1234-2026')
    def test_muestra_el_registro_cuando_se_configura(self):
        html = _html_del_contrato(self.cot)
        self.assertIn('1234-2026', html)
        self.assertIn('registrado por la Procuraduría', html)

    def test_ocupacion_por_habitacion(self):
        html = _html_del_contrato(self.cot)
        self.assertIn('4 personas</strong>', html)
        self.assertIn('6 personas adicionales por habitación', html)
        self.assertIn('10 personas por habitación', html)

    def test_plazo_de_pago_sale_del_modelo(self):
        html = _html_del_contrato(self.cot)
        self.assertIn(f"{Cotizacion.DIAS_PAGO_TOTAL['HOSPEDAJE']} días naturales antes", html)


# Contratos basados en el modelo PROFECO (CONTRATO_PROPIO_ACTIVO=False).
@override_settings(CONTRATO_PROPIO_ACTIVO=False)
class ContratoEventoSinArrendamientoTest(TestCase):
    def setUp(self):
        cliente = Cliente.objects.create(nombre='Luis Pech', tipo_persona='FISICA')
        self.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Boda', tipo_servicio='EVENTO',
            fecha_evento=date(2026, 12, 5), num_personas=100,
        )

    def test_evento_conserva_su_registro_y_sin_mobiliario(self):
        html = _html_del_contrato(self.cot)
        self.assertIn('9341-2023', html)
        self.assertNotIn('9339-2023', html)
        self.assertNotIn('Mobiliario', html)
        self.assertIn('Arrendamiento de Salón', html)


class GenerarContratoArrendamientoTest(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser('dir', 'dir@example.com', 'x')
        login_superuser_con_totp(self.client, self.admin)
        cliente = Cliente.objects.create(nombre='C', tipo_persona='FISICA')
        self.cot = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='X', tipo_servicio='ARRENDAMIENTO',
            fecha_evento=date(2026, 12, 5),
        )
        Cotizacion.objects.filter(pk=self.cot.pk).update(estado='CONFIRMADA')

    def test_arrendamiento_ya_no_genera_contrato(self):
        respuesta = self.client.get(f'/cotizacion/{self.cot.pk}/contrato/generar/')
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(ContratoServicio.objects.exists())
