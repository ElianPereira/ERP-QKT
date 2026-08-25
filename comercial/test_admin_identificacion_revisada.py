"""Tests de CotizacionAdmin.save_model — auditoría de la revisión manual de
la identificación oficial (INE) subida desde el portal.

No hay forma automática de validar que el archivo subido sea realmente una
identificación (requeriría un servicio externo de verificación de
identidad); la mitigación es que quien concilia pagos la revise a simple
vista y marque `identificacion_revisada`, y el sistema deja constancia de
quién y cuándo — igual que ya hace con `cancelada_por`/`fecha_cancelacion`.
"""
from datetime import date

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from comercial.admin import CotizacionAdmin
from comercial.models import Cliente, Cotizacion


class IdentificacionRevisadaAuditoriaTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.staff = User.objects.create_user(
            username='conciliador', password='x', is_staff=True,
        )
        self.cliente = Cliente.objects.create(nombre='Juan Pérez López')
        self.cotizacion = Cotizacion.objects.create(
            cliente=self.cliente, fecha_evento=date(2027, 1, 1),
        )
        self.site = CotizacionAdmin(Cotizacion, admin.site)

    def _guardar(self, obj):
        request = self.factory.post('/')
        request.user = self.staff
        self.site.save_model(request, obj, form=None, change=True)

    def test_marcar_revisada_registra_quien_y_cuando(self):
        obj = Cotizacion.objects.get(pk=self.cotizacion.pk)
        obj.identificacion_revisada = True
        self._guardar(obj)

        obj.refresh_from_db()
        self.assertTrue(obj.identificacion_revisada)
        self.assertEqual(obj.identificacion_revisada_por, self.staff)
        self.assertIsNotNone(obj.identificacion_revisada_en)

    def test_desmarcar_revisada_limpia_la_auditoria(self):
        self.cotizacion.identificacion_revisada = True
        self.cotizacion.identificacion_revisada_por = self.staff
        from django.utils import timezone
        self.cotizacion.identificacion_revisada_en = timezone.now()
        self.cotizacion.save()

        obj = Cotizacion.objects.get(pk=self.cotizacion.pk)
        obj.identificacion_revisada = False
        self._guardar(obj)

        obj.refresh_from_db()
        self.assertFalse(obj.identificacion_revisada)
        self.assertIsNone(obj.identificacion_revisada_por)
        self.assertIsNone(obj.identificacion_revisada_en)

    def test_volver_a_guardar_ya_revisada_no_cambia_la_auditoria_previa(self):
        obj = Cotizacion.objects.get(pk=self.cotizacion.pk)
        obj.identificacion_revisada = True
        self._guardar(obj)
        obj.refresh_from_db()
        primera_fecha = obj.identificacion_revisada_en

        # Segundo guardado sin tocar el flag: no debe reescribir la marca.
        obj.nombre_evento = 'Otro nombre'
        self._guardar(obj)
        obj.refresh_from_db()
        self.assertEqual(obj.identificacion_revisada_en, primera_fecha)
        self.assertEqual(obj.identificacion_revisada_por, self.staff)

    def test_badge_muestra_sin_identificacion(self):
        obj = Cotizacion.objects.get(pk=self.cotizacion.pk)
        badge = self.site.identificacion_badge(obj)
        self.assertIn('—', str(badge))

    def test_badge_muestra_sin_revisar(self):
        self.cotizacion.identificacion_oficial.name = 'cotizaciones/identificaciones/x.jpg'
        self.cotizacion.save()
        badge = self.site.identificacion_badge(self.cotizacion)
        self.assertIn('Sin revisar', str(badge))

    def test_badge_muestra_revisada(self):
        self.cotizacion.identificacion_oficial.name = 'cotizaciones/identificaciones/x.jpg'
        self.cotizacion.identificacion_revisada = True
        self.cotizacion.save()
        badge = self.site.identificacion_badge(self.cotizacion)
        self.assertIn('Revisada', str(badge))
