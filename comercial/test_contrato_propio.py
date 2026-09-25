"""
Contratos propios: marco + anexo por servicio (Issue #318).

Ejecutar: python manage.py test comercial.test_contrato_propio
"""
from datetime import date, time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.storage import InMemoryStorage
from django.test import TestCase, override_settings

from comercial.models import Cliente, ContratoServicio, Cotizacion, ItemCotizacion, Producto
from comercial.reglas_contrato import deposito_sugerido
from comercial.services import ContratoService
from core_erp.test_utils import login_superuser_con_totp


def _html(cot, **kwargs):
    """HTML que ContratoService manda a WeasyPrint, sin generar el PDF."""
    with patch('weasyprint.HTML') as html:
        html.return_value.write_pdf.return_value = b'%PDF'
        ContratoService(cot, **kwargs).generar()
    return html.call_args.kwargs['string']


class ContratoPropioBase(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre='Ana Ruiz', tipo_persona='FISICA')

    def _cot(self, tipo, **kwargs):
        datos = {
            'cliente': self.cliente, 'nombre_evento': 'Prueba', 'tipo_servicio': tipo,
            'fecha_evento': date(2026, 12, 5), 'hora_inicio': time(11), 'hora_fin': time(19),
            'num_personas': 20,
        }
        datos.update(kwargs)
        return Cotizacion.objects.create(**datos)

    def _habitacion(self, cot, nombre):
        prod = Producto.objects.create(
            nombre=nombre, precio_venta_fijo=Decimal('780.00'),
            rol_cotizador='HABITACION_HOSPEDAJE', capacidad_base_hospedaje=4,
        )
        ItemCotizacion.objects.create(cotizacion=cot, producto=prod, cantidad=2)


class DepositoSugeridoTest(ContratoPropioBase):
    def test_evento_es_el_diez_por_ciento_redondeado(self):
        cot = self._cot('EVENTO')
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('23200.05'))
        cot.refresh_from_db()
        self.assertEqual(deposito_sugerido(cot), Decimal('2320.01'))

    def test_hospedaje_cuenta_habitaciones_no_noches(self):
        cot = self._cot('HOSPEDAJE', fecha_salida=date(2026, 12, 7))
        self._habitacion(cot, "Ka'an Room")
        self._habitacion(cot, 'Otoch Room')
        self.assertEqual(deposito_sugerido(cot), Decimal('1000.00'))

    def test_pasadia_no_lleva_deposito(self):
        self.assertEqual(deposito_sugerido(self._cot('PASADIA')), Decimal('0.00'))


class PlantillaContratoTest(ContratoPropioBase):
    def test_apagado_sigue_emitiendo_el_contrato_actual(self):
        html = _html(self._cot('EVENTO'))
        self.assertIn('9341-2023', html)
        self.assertNotIn('Anexo Evento', html)

    def test_vista_previa_usa_el_contrato_propio_con_marca_de_agua(self):
        html = _html(self._cot('EVENTO'), vista_previa=True)
        self.assertIn('Anexo Evento', html)
        self.assertIn('BORRADOR', html)
        self.assertNotIn('9341-2023', html)
        # El contrato viejo contradecía al Reglamento (pet friendly).
        self.assertNotIn('No se permite la entrada de animales', html)

    def test_el_anexo_sale_del_tipo_de_la_cotizacion(self):
        self.assertIn('Anexo Pasadía', _html(self._cot('PASADIA'), vista_previa=True))
        cot = self._cot('HOSPEDAJE', fecha_salida=date(2026, 12, 7))
        self._habitacion(cot, "Ka'an Room")
        html = _html(cot, vista_previa=True)
        self.assertIn('Anexo Hospedaje', html)
        self.assertIn("Ka&#x27;an Room", html)

    def test_tabla_de_cancelacion_por_servicio(self):
        self.assertIn('Más de 60 días naturales antes', _html(self._cot('PASADIA'), vista_previa=True))
        cot = self._cot('HOSPEDAJE', fecha_salida=date(2026, 12, 7))
        self.assertIn('Más de 15 días naturales antes de la llegada', _html(cot, vista_previa=True))

    def test_pasadia_no_menciona_deposito_en_garantia(self):
        html = _html(self._cot('PASADIA'), vista_previa=True)
        self.assertIn('no requiere depósito en garantía', html)
        self.assertNotIn('Cuarta. Depósito en garantía', html)

    @override_settings(CONTRATO_PROPIO_ACTIVO=True)
    def test_activo_emite_sin_marca_de_agua_y_con_deposito_sugerido(self):
        cot = self._cot('EVENTO')
        Cotizacion.objects.filter(pk=cot.pk).update(precio_final=Decimal('20000.00'))
        cot.refresh_from_db()
        html = _html(cot)
        self.assertNotIn('marca-agua">BORRADOR', html)
        self.assertIn('$2,000.00 MXN', html)


class VistasContratoTest(ContratoPropioBase):
    def setUp(self):
        super().setUp()
        self.admin = get_user_model().objects.create_superuser('dir', 'dir@example.com', 'x')
        login_superuser_con_totp(self.client, self.admin)

    @patch('weasyprint.HTML')
    def test_vista_previa_no_guarda_nada(self, html):
        html.return_value.write_pdf.return_value = b'%PDF'
        cot = self._cot('PASADIA')
        respuesta = self.client.get(f'/cotizacion/{cot.pk}/contrato/vista-previa/')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta['Content-Type'], 'application/pdf')
        self.assertFalse(ContratoServicio.objects.exists())
        cot.refresh_from_db()
        self.assertFalse(cot.archivo_contrato)

    @patch('weasyprint.HTML')
    def test_generar_ignora_el_tipo_de_la_url(self, html):
        html.return_value.write_pdf.return_value = b'%PDF'
        cot = self._cot('PASADIA')
        Cotizacion.objects.filter(pk=cot.pk).update(estado='CONFIRMADA')
        memoria = InMemoryStorage()
        with patch.object(ContratoServicio._meta.get_field('archivo'), 'storage', memoria), \
                patch.object(Cotizacion._meta.get_field('archivo_contrato'), 'storage', memoria):
            self.client.get(f'/cotizacion/{cot.pk}/contrato/generar/?tipo_servicio=EVENTO')
        self.assertEqual(ContratoServicio.objects.get().tipo_servicio, 'PASADIA')
