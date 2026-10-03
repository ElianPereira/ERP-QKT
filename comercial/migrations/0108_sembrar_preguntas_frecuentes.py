"""Preguntas frecuentes iniciales, redactadas a partir del Reglamento v1.3 y la
Política de Cancelación v2.2 vigentes (y datos operativos confirmados por el
propietario). Las consulta el agente de WhatsApp (`preguntas_frecuentes`) y la
landing del ERP.

Solo agrega: una pregunta con el mismo texto que ya exista no se toca, así que
lo que el propietario haya capturado o corregido a mano se respeta. El orden
arranca en 101 para quedar después de las que ya existan. Revertir no borra
nada (pudieron editarse en el admin después del deploy).
"""
from django.db import migrations

PREGUNTAS = [
    ('¿Puedo llevar a mi mascota?',
     'Sí, somos pet friendly. Avísanos con anticipación cuántas mascotas llevas, de qué especie y '
     'de qué tamaño. Deben tener sus vacunas al día (incluida la antirrábica), estar desparasitadas '
     'y traer placa con su nombre y un teléfono. En áreas comunes van con correa y siempre '
     'supervisadas; no pueden entrar a la alberca ni a su orilla, y hay que recoger sus desechos. '
     'En hospedaje no se suben a camas ni sillones, y el depósito en garantía sube $500 por '
     'mascota. Los perros guía y de asistencia tienen acceso siempre, sin restricciones ni cargo.'),
    ('¿Puedo llevar mi propia comida y bebidas?',
     'Sí, puedes llevar tus alimentos y bebidas o contratar a tus propios proveedores, sin cobro de '
     'descorche. Quien prepara o lleva la comida es responsable de su calidad e higiene. No se '
     'permite vender bebidas alcohólicas dentro de la Quinta ni servir alcohol a menores de edad, '
     'ni llevar envases de vidrio a la alberca o a su alrededor.'),
    ('¿Puedo traer mis propios proveedores (banquete, DJ, decoración)?',
     'Sí. Regístralos con al menos 7 días de anticipación: nombre, teléfono, servicio y hora de '
     'llegada. Un proveedor no registrado puede no tener acceso. Cada proveedor debe llevarse su '
     'basura voluminosa (cajas, hielo, aceite, restos de comida) y retirar su equipo al terminar.'),
    ('¿Hasta qué hora puede haber música?',
     'En eventos y pasadías la música se apaga puntualmente al terminar el horario contratado; no '
     'hay prórrogas de palabra, cualquier extensión se acuerda antes y por escrito. En hospedaje '
     'hay horario de silencio de 10:00 p.m. a 8:00 a.m. Siempre cuidamos el descanso de los vecinos.'),
    ('¿Cuántas personas caben?',
     'Eventos: hasta 150 personas. Pasadía: 20 personas incluidas y hasta 10 más con costo por '
     'persona (máximo 30). Hospedaje: 4 personas por habitación con comodidad, y hasta 6 más con '
     'costo extra (máximo 10). El aforo máximo no se puede rebasar el día del evento.'),
    ('¿Cuándo confirmo el número final de invitados?',
     'Con al menos 10 días de anticipación. Las personas que lleguen sin haberse confirmado se '
     'cobran a la tarifa de persona adicional vigente más un 50%.'),
    ('¿Cuál es el horario de la pasadía?',
     'De 11:00 a.m. a 7:00 p.m. La pasadía no incluye quedarse a dormir; para eso tenemos '
     'hospedaje en nuestras habitaciones Ka\'an y Otoch.'),
    ('¿A qué hora es la entrada y salida del hospedaje?',
     'Entrada a partir de las 2:00 p.m. y salida a más tardar a las 10:00 a.m., con 30 minutos de '
     'tolerancia. Después, cada hora se cobra al 10% de la tarifa por noche, y a partir de la '
     '1:00 p.m. se cobra una noche adicional. Si necesitas salir más tarde, pregúntanos con tiempo.'),
    ('¿Cuánto tiempo tengo para montar y desmontar?',
     'En eventos puedes montar desde temprano el mismo día. Montar el día anterior solo es posible '
     'si la agenda lo permite y te lo confirmamos por escrito. Al terminar el evento hay 30 minutos '
     'para desmontar y desalojar.'),
    ('¿Hay salvavidas en la alberca?',
     'No contamos con salvavidas ni servicio de cuidado de niños. Los menores deben estar siempre '
     'supervisados por un adulto, sobre todo en la alberca y los juegos. No pueden entrar a la '
     'alberca personas bajo el efecto del alcohol.'),
    ('¿Hay estacionamiento?',
     'Sí, hay estacionamiento interior y exterior. Su uso es bajo tu responsabilidad: no '
     'respondemos por los vehículos ni por los objetos que se dejen dentro.'),
    ('¿Qué es el depósito en garantía y cuándo me lo devuelven?',
     'Es un monto aparte del precio que se devuelve después de tu servicio. Eventos: 10% del '
     'total. Hospedaje: $500 por habitación, más $500 si llevas mascota. Pasadía: no aplica. Se '
     'devuelve dentro de los 7 días siguientes por el mismo medio de pago, descontando solo daños '
     'documentados con foto y desglose por escrito.'),
    ('¿Puedo cancelar? ¿Me devuelven mi dinero?',
     'Dentro de los 5 días hábiles después de firmar tu contrato puedes cancelar sin penalización '
     'y te devolvemos el 100%. Después, en eventos y pasadías depende de la anticipación: más de 60 '
     'días, 90% de reembolso; de 31 a 60 días, 75%; de 16 a 30 días, 50%; 15 días o menos, sin '
     'reembolso. En hospedaje: más de 15 días antes del check-in, 100%; de 7 a 15 días, 50%; menos '
     'de 7 días, sin reembolso. La cancelación se solicita por escrito.'),
    ('¿Puedo cambiar la fecha?',
     'Sí, una vez sin costo si avisas con 60 días o más de anticipación, sujeto a disponibilidad. '
     'Con menos de 60 días hay un cargo administrativo del 10%. Se permiten máximo dos cambios '
     'por reservación.'),
    ('¿Y si llueve el día de mi pasadía?',
     'Si hay lluvia intensa o tormenta que impida disfrutar las instalaciones, puedes reprogramar '
     'una sola vez sin costo a una fecha disponible dentro de los 60 días siguientes, avisando '
     'antes de la hora de inicio.'),
    ('¿Puedo usar pirotecnia, confeti o chispas frías?',
     'La pirotecnia solo se permite exhibiendo el permiso vigente de Protección Civil o SEDENA. No '
     'se permite confeti, diamantina, arroz, chispas frías ni espuma en la alberca, jardines o '
     'áreas verdes; en otras áreas requiere autorización y puede tener cargo de limpieza. Velas, '
     'antorchas y asadores se avisan antes y se vigilan siempre.'),
    ('¿Puedo decorar o colgar cosas?',
     'Sí, pero sin clavar, atornillar, perforar, pintar ni pegar nada que dañe muros, techos, '
     'árboles o instalaciones. Cualquier fijación se acuerda antes con nuestro personal, y al '
     'terminar hay que retirar toda la decoración.'),
    ('¿Dónde están ubicados?',
     'Estamos en Carretera Tanil – Ticimul km 1.920, Umán, Yucatán. Al confirmar tu reservación te '
     'enviamos una guía con la ubicación en Google Maps y todo lo que necesitas para tu visita.'),
    ('¿Se permiten armas?',
     'No. Por seguridad, no se permiten armas de ningún tipo dentro de la Quinta, aunque se cuente '
     'con licencia. Solo se exceptúa a seguridad pública en funciones y a seguridad privada '
     'contratada, avisada y autorizada por escrito con anticipación.'),
    ('¿Incluyen hielo?',
     'El hielo no está incluido, salvo cuando contratas con nosotros servicios de bebidas como '
     'refrescos o licores. Si llevas tus propias bebidas, el hielo corre por tu cuenta.'),
    ('¿Hay wifi?',
     'Sí, contamos con wifi. Pide los datos de conexión a nuestro personal al llegar.'),
    ('¿Tienen bocina o equipo de sonido?',
     'No contamos con bocina ni equipo de sonido. Puedes llevar tu propio equipo o contratar a un '
     'DJ o grupo (regístralo como proveedor con 7 días de anticipación). La música se apaga al '
     'terminar tu horario contratado.'),
    ('¿Hay baños y regaderas?',
     'Sí hay baños. Las regaderas y vestidores son únicamente los de las habitaciones.'),
    ('¿Hay asador?',
     'Sí, tenemos asador de piedra. Avísanos con anticipación si lo vas a usar; debe estar '
     'vigilado todo el tiempo mientras esté encendido.'),
]


def sembrar(apps, schema_editor):
    PreguntaFrecuente = apps.get_model('comercial', 'PreguntaFrecuente')
    for i, (pregunta, respuesta) in enumerate(PREGUNTAS, start=101):
        PreguntaFrecuente.objects.get_or_create(
            pregunta=pregunta, defaults={'respuesta': respuesta, 'orden': i, 'activo': True},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0107_bloqueo_fecha'),
    ]

    operations = [
        migrations.RunPython(sembrar, migrations.RunPython.noop),
    ]
