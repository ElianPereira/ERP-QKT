from django.db import migrations, models

from comercial.roles_cotizador import sembrar_roles as _sembrar


# El catálogo real ya tiene estos productos capturados a mano, así que el rol se
# siembra aquí en vez de obligar a marcarlos uno por uno en el admin tras el
# deploy. Solo asigna roles: si el helper cambiara en el futuro, lo peor que
# puede pasar es que una BD nueva arranque sin marcar (el campo es opcional).
def sembrar_roles(apps, schema_editor):
    _sembrar(apps.get_model('comercial', 'Producto'))


def limpiar_roles(apps, schema_editor):
    Producto = apps.get_model('comercial', 'Producto')
    Producto.objects.exclude(rol_cotizador='').update(rol_cotizador='')


class Migration(migrations.Migration):

    dependencies = [
        ('comercial', '0071_portal_cliente_expiracion'),
    ]

    operations = [
        migrations.AddField(
            model_name='producto',
            name='rol_cotizador',
            field=models.CharField(
                blank=True,
                choices=[
                    ('BASE_EVENTO', 'Base — Evento (se agrega solo)'),
                    ('BASE_PASADIA', 'Base — Pasadía (se agrega solo)'),
                    ('HORA_EXTRA', 'Hora extra de arrendamiento'),
                ],
                db_index=True,
                help_text=(
                    'Producto que el cotizador agrega SOLO al elegir el servicio (el '
                    'arrendamiento de la quinta). No se ofrece como extra: si el cliente '
                    'pudiera marcarlo, se cobraría dos veces. Vacío = extra normal.'
                ),
                max_length=20,
                verbose_name='Rol en el cotizador',
            ),
        ),
        migrations.RunPython(sembrar_roles, limpiar_roles),
    ]
