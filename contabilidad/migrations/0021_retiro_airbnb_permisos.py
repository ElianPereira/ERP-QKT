"""
Retiro de Airbnb (Issue #311), fase 3: la app `airbnb` sale de INSTALLED_APPS.

Sus tablas ya las borró `airbnb.0008`; lo que queda son los ContentType y
Permission de `app_label='airbnb'` (y su asignación a grupos o usuarios), que
Django no limpia solo cuando se retira una app. Vive en `contabilidad`, igual
que el borrado de datos, porque `airbnb` ya no tiene migraciones activas.
"""
from django.db import migrations


def borrar_permisos_airbnb(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    tipos = ContentType.objects.filter(app_label='airbnb')
    # Permission primero (CASCADE a grupos/usuarios); las pólizas y la bitácora
    # del admin que apunten a estos tipos quedan en NULL (SET_NULL).
    Permission.objects.filter(content_type__in=tipos).delete()
    tipos.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('contabilidad', '0020_retiro_airbnb_datos'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('admin', '0003_logentry_add_action_flag_choices'),
    ]

    operations = [
        migrations.RunPython(borrar_permisos_airbnb, migrations.RunPython.noop),
    ]
