"""La pregunta «¿Dónde están ubicados?» (sembrada en `0108`) decía que el enlace
de Google Maps llegaba con la guía al confirmar; el propietario pidió que Kooxi
lo comparta a quien pregunte. Solo se reescribe si la respuesta sigue igual a
la sembrada: una editada en el admin no se toca. Revertir la deja como estaba.
"""
from django.db import migrations

PREGUNTA = '¿Dónde están ubicados?'
ANTERIOR = ('Estamos en Carretera Tanil – Ticimul km 1.920, Umán, Yucatán. Al confirmar tu reservación te '
            'enviamos una guía con la ubicación en Google Maps y todo lo que necesitas para tu visita.')
# Misma ficha que comunicacion.services_notificaciones.MAPS_URL_QKT (copiada: una
# migración no debe depender de código que puede cambiar).
NUEVA = ('Estamos en Carretera Tanil – Ticimul km 1.920, Umán, Yucatán. Así llegas en Google Maps: '
         'https://www.google.com/maps/place/Quinta+Ko%C2%B4ox+Tanil/'
         '@20.8889293,-89.7032089,17z/data=!3m1!4b1!4m6!3m5!'
         '1s0x8f5613ea09962ecf:0x3a487596f790346c!8m2!3d20.8889293!4d-89.7032089!16s%2Fg%2F11h8f9592m')


def actualizar(apps, schema_editor):
    PreguntaFrecuente = apps.get_model('comercial', 'PreguntaFrecuente')
    PreguntaFrecuente.objects.filter(pregunta=PREGUNTA, respuesta=ANTERIOR).update(respuesta=NUEVA)


def revertir(apps, schema_editor):
    PreguntaFrecuente = apps.get_model('comercial', 'PreguntaFrecuente')
    PreguntaFrecuente.objects.filter(pregunta=PREGUNTA, respuesta=NUEVA).update(respuesta=ANTERIOR)


class Migration(migrations.Migration):
    dependencies = [('comercial', '0121_solicitud_cancelacion')]
    operations = [migrations.RunPython(actualizar, revertir)]
