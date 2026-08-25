"""
Los documentos legales deben ser alcanzables desde las superficies públicas.

Requisito de la validación técnica de Openpay: el aviso de privacidad y los
términos y condiciones tienen que estar enlazados donde el cliente captura
datos o paga, no solo existir en una URL suelta.
"""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from comercial.models import (
    Cliente,
    Cotizacion,
    ItemCotizacion,
    PortalCliente,
)


class EnlacesLegalesEnCotizadorTest(TestCase):
    def test_cotizador_publico_enlaza_los_documentos_legales(self):
        response = self.client.get(reverse('cotizador_publico'))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for nombre in ('legal:aviso_privacidad', 'legal:terminos',
                       'legal:politica_cancelacion'):
            self.assertIn(reverse(nombre), html, f'Falta el enlace a {nombre}')


class ConsentimientoObligatorioTest(TestCase):
    """
    El consentimiento se valida en el servidor, no solo con la casilla del JS:
    una petición hecha por fuera del formulario no puede saltárselo.
    """

    def test_cotizador_rechaza_la_solicitud_sin_consentimiento(self):
        response = self.client.post(
            reverse('cotizador_enviar'),
            data={
                'nombre': 'Cliente Sin Consentimiento',
                'telefono': '9990000000',
                'servicio': 'EVENTO',
                'fecha': '2027-01-15',
            },
            secure=True,
        )

        self.assertEqual(response.status_code, 400)
        errores = ' '.join(response.json()['errores'])
        self.assertIn('Aviso de Privacidad', errores)
        self.assertFalse(Cotizacion.objects.exists())

    def test_checkout_rechaza_el_pago_sin_consentimiento(self):
        cliente = Cliente.objects.create(nombre='Cliente Checkout',
                                         tipo_persona='FISICA')
        cotizacion = Cotizacion.objects.create(
            cliente=cliente, nombre_evento='Evento', fecha_evento='2027-03-10',
            incluye_refrescos=False,
        )
        ItemCotizacion.objects.create(
            cotizacion=cotizacion, descripcion='Servicio de evento',
            cantidad=1, precio_unitario=Decimal('10000.00'),
        )
        # Este test aísla el requisito de consentimiento legal, no el de
        # identificación oficial (otro requisito distinto del checkout) —
        # sin esto el gate de identificación ganaría primero.
        cotizacion.identificacion_oficial.name = 'cotizaciones/identificaciones/test-ine.jpg'
        cotizacion.save()
        cotizacion.refresh_from_db()
        portal = PortalCliente.objects.get(cotizacion=cotizacion)

        response = self.client.post(
            reverse('portal_procesar_pago_openpay', args=[portal.token]),
            data={'metodo': 'store',
                  'monto': str(cotizacion.saldo_pendiente())},
            secure=True,
        )

        datos = response.json()
        self.assertFalse(datos['ok'])
        self.assertIn('Términos y Condiciones', datos['mensaje'])
