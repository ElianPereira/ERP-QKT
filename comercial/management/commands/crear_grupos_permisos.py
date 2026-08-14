"""
Crea los 3 grupos de acceso por área (SEC-AUTHZ-001a, Issue #199) y les asigna
los permisos de modelo correspondientes.

Idempotente: usa get_or_create en Group y group.permissions.set(...), así que
correrlo varias veces no duplica nada. No es destructivo, se corre igual en
dev y en producción como parte del despliegue.

Dirección no es un grupo Django: sigue siendo `is_superuser`, ya el patrón
usado en `importar_historico_view` y `migrar_archivos_privados_view` — un
superusuario pasa cualquier `has_perm`/`permission_required` sin necesitar
permisos explícitos.

`legal.SolicitudARCO` no se liga a ningún grupo de aquí: conserva su permiso
independiente `legal.ver_identificacion_arco`, asignado a mano por el
propietario a quien gestione ARCO.
"""
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

VERBOS_ESTANDAR = ('view', 'add', 'change', 'delete')

# grupo -> apps completas a las que accede (todos los modelos, CRUD estándar)
GRUPOS_APPS = {
    'Ventas': ['comercial', 'airbnb', 'comunicacion'],
    'Contabilidad': ['contabilidad', 'facturacion'],
    'Nómina': ['nomina'],
}

# Excepciones dentro de un área: (app_label, modelo) -> verbos permitidos,
# en vez de los 4 estándar. Aquí solo ConstanteSistema (Ventas consulta
# precios de referencia, pero cambiarlos queda reservado a superusuario).
EXCEPCIONES = {
    ('comercial', 'constantesistema'): ('view',),
}


class Command(BaseCommand):
    help = "Crea los grupos Ventas/Contabilidad/Nómina y asigna sus permisos de modelo."

    def handle(self, *args, **options):
        for nombre_grupo, apps_del_grupo in GRUPOS_APPS.items():
            grupo, creado = Group.objects.get_or_create(name=nombre_grupo)
            permisos = []

            for app_label in apps_del_grupo:
                app_config = apps.get_app_config(app_label)
                for modelo in app_config.get_models():
                    model_name = modelo._meta.model_name
                    verbos = EXCEPCIONES.get((app_label, model_name), VERBOS_ESTANDAR)
                    for verbo in verbos:
                        codename = f'{verbo}_{model_name}'
                        try:
                            permiso = Permission.objects.get(
                                content_type__app_label=app_label, codename=codename,
                            )
                        except Permission.DoesNotExist:
                            self.stderr.write(self.style.WARNING(
                                f'  Permiso {app_label}.{codename} no existe (¿migraciones al día?), se omite.'
                            ))
                            continue
                        permisos.append(permiso)

            grupo.permissions.set(permisos)
            accion = 'Creado' if creado else 'Actualizado'
            self.stdout.write(self.style.SUCCESS(
                f'{accion} grupo "{nombre_grupo}" con {len(permisos)} permisos.'
            ))
