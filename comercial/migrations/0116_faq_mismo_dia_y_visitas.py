"""Dos preguntas frecuentes pedidas por el propietario tras chats reales de
Kooxi: contratar para el mismo día y visitar la Quinta. En ambos casos decide
una persona del equipo; la regla dura vive en `comunicacion.herramientas_agente`
y en el prompt, esto es el texto que el agente le repite al cliente.

Solo agrega, mismo criterio que `0108`: una pregunta con el mismo texto que ya
exista no se toca. Revertir no borra nada.
"""
from django.db import migrations
from django.db.models import Max

PREGUNTAS = [
    ('¿Con cuánta anticipación puedo contratar?',
     'Puedes reservar con la anticipación que quieras; mientras antes, más fechas libres hay. '
     'Contratar para el mismo día solo es posible si el equipo confirma que la Quinta puede estar '
     'lista para recibirte: una fecha libre en el calendario no significa que esté preparada. '
     'Escríbenos el servicio, la hora y cuántas personas serían, y alguien del equipo te confirma '
     'por este chat. La fecha se aparta hasta pagar el anticipo.'),
    ('¿Puedo visitar la Quinta antes de reservar?',
     'Sí, con gusto te recibimos, siempre con cita. Dinos qué día y a qué hora te gustaría venir y '
     'alguien del equipo te confirma por este chat si se puede. Estamos en la Carretera Tanil – '
     'Ticimul km 1.920, en Umán.'),
]


def sembrar(apps, schema_editor):
    PreguntaFrecuente = apps.get_model('comercial', 'PreguntaFrecuente')
    orden = PreguntaFrecuente.objects.aggregate(m=Max('orden'))['m'] or 100
    for i, (pregunta, respuesta) in enumerate(PREGUNTAS, start=orden + 1):
        PreguntaFrecuente.objects.get_or_create(
            pregunta=pregunta, defaults={'respuesta': respuesta, 'orden': i, 'activo': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0115_catalogo_categorias_gasto'),
    ]

    operations = [
        migrations.RunPython(sembrar, migrations.RunPython.noop),
    ]
