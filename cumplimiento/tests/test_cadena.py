from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from cumplimiento import services
from cumplimiento.models import Tramite, Vigencia


def _obtenida(tramite, dias=200):
    hoy = timezone.localdate()
    v = Vigencia(tramite=tramite, estado='OBTENIDA', fecha_inicio=hoy, fecha_vencimiento=hoy + timedelta(days=dias),
                 documento=SimpleUploadedFile('a.pdf', b'%PDF-1.4'))
    return v


@override_settings(STORAGES={'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
                             'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class CadenaTramitesTest(TestCase):
    def setUp(self):
        services.cargar_catalogo_sugerido()
        self.ext = Tramite.objects.get(nombre='Constancia de extintores')
        self.pc = Tramite.objects.get(nombre='Constancia de Protección Civil')

    def test_catalogo_define_orden_y_previos(self):
        nombres = list(Tramite.objects.values_list('nombre', flat=True))
        self.assertEqual(nombres, ['Constancia de extintores', 'Constancia de Protección Civil',
                                   'Licencia de uso de suelo', 'Licencia de funcionamiento'])
        self.assertEqual(self.pc.requiere, self.ext)
        self.assertIsNone(self.ext.requiere)

    def test_no_se_obtiene_sin_el_previo_vigente(self):
        with self.assertRaises(ValidationError) as cm:
            _obtenida(self.pc).full_clean(exclude=['tramite'])
        self.assertIn('estado', cm.exception.message_dict)
        base = _obtenida(self.ext)
        base.save()
        _obtenida(self.pc).full_clean(exclude=['tramite'])

    def test_previo_vencido_tampoco_habilita(self):
        _obtenida(self.ext, dias=-5).save()
        with self.assertRaises(ValidationError):
            _obtenida(self.pc).full_clean(exclude=['tramite'])

    def test_tablero_marca_el_previo_pendiente_y_orden(self):
        filas = [f for g in services.resumen_tablero()['grupos'] for f in g['filas']]
        self.assertEqual([f['tramite'].nombre for f in filas][:2],
                         ['Constancia de extintores', 'Constancia de Protección Civil'])
        self.assertEqual(filas[1]['previo'], self.ext)
        self.assertEqual(filas[0]['dias_texto'], '—')

    def test_no_se_permiten_ciclos(self):
        self.ext.requiere = self.pc
        with self.assertRaises(ValidationError):
            self.ext.full_clean()
