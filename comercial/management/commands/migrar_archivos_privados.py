"""
Copia al bucket privado los documentos sensibles que todavía viven en el
bucket público (SEC-FILE-001a).

Simula por defecto; `--aplicar` escribe. **Copia, no mueve**: no borra nada del
bucket público, porque si algo sale mal el archivo debe seguir estando en algún
sitio. El borrado del origen es un paso manual posterior, y solo cuando se haya
verificado que las descargas funcionan contra el bucket nuevo.

Conserva el `name` exacto de cada archivo: la BD guarda esa ruta y no se toca,
así que copiar con el mismo nombre es lo que hace que el cambio de storage sea
transparente.

Uso:
    python manage.py migrar_archivos_privados            # simula
    python manage.py migrar_archivos_privados --aplicar  # copia
"""
from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from core_erp.storages_qkt import _hay_bucket_privado

# Los mismos campos a los que models.py les puso storage=storage_privado.
CAMPOS_PRIVADOS = (
    ('legal', 'SolicitudARCO', 'identificacion'),
    ('nomina', 'ReciboNomina', 'archivo_pdf'),
    ('contabilidad', 'EstadoCuentaBancario', 'archivo'),
    ('comercial', 'ContratoServicio', 'archivo'),
)


class Command(BaseCommand):
    help = 'Copia los documentos sensibles del bucket público al privado.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Copia de verdad. Sin esta bandera solo informa qué haría.',
        )

    def handle(self, *args, **options):
        if not _hay_bucket_privado():
            raise CommandError(
                'No hay bucket privado configurado. Define '
                'CLOUDFLARE_R2_PRIVATE_BUCKET_NAME (y las credenciales si son '
                'distintas) antes de correr esto.'
            )

        aplicar = options['aplicar']
        origen = storages['default']
        destino = storages['privado']

        copiados = ya_estaban = sin_origen = 0
        fallos = []

        for app_label, model_name, campo in CAMPOS_PRIVADOS:
            modelo = apps.get_model(app_label, model_name)
            registros = (
                modelo.objects
                .exclude(**{campo: ''})
                .exclude(**{f'{campo}__isnull': True})
                .only('pk', campo)
                .order_by('pk')
                .iterator()
            )

            for registro in registros:
                archivo = getattr(registro, campo)
                if not archivo or not archivo.name:
                    continue

                referencia = f'{app_label}.{model_name} pk={registro.pk} {campo}={archivo.name}'

                try:
                    if destino.exists(archivo.name):
                        ya_estaban += 1
                        continue
                    if not origen.exists(archivo.name):
                        # Típico de los registros heredados de Cloudinary que
                        # nunca se migraron; los rescata otro comando.
                        sin_origen += 1
                        self.stdout.write(self.style.WARNING(f'  sin origen: {referencia}'))
                        continue
                except Exception as exc:
                    fallos.append((referencia, f'consulta al storage: {exc}'))
                    continue

                if not aplicar:
                    copiados += 1
                    self.stdout.write(f'  copiaría: {referencia}')
                    continue

                try:
                    with origen.open(archivo.name, 'rb') as f:
                        contenido = f.read()
                    guardado = destino.save(archivo.name, ContentFile(contenido))
                except Exception as exc:
                    fallos.append((referencia, str(exc)))
                    continue

                if guardado != archivo.name:
                    # Si el destino renombró, la BD apuntaría a una ruta que no
                    # existe: es peor que no haber copiado. Se avisa y se
                    # limpia la copia con nombre equivocado.
                    fallos.append((
                        referencia,
                        f'el destino renombró a {guardado!r}; se descarta la copia',
                    ))
                    try:
                        destino.delete(guardado)
                    except Exception:
                        pass
                    continue

                copiados += 1

        self.stdout.write('')
        self.stdout.write('Resumen:')
        verbo = 'Copiados' if aplicar else 'Se copiarían'
        self.stdout.write(self.style.SUCCESS(f'  {verbo}: {copiados}'))
        self.stdout.write(f'  Ya estaban en el privado: {ya_estaban}')
        self.stdout.write(self.style.WARNING(f'  Sin archivo en el origen: {sin_origen}'))
        self.stdout.write(self.style.ERROR(f'  Fallos: {len(fallos)}'))
        for referencia, motivo in fallos:
            self.stdout.write(self.style.ERROR(f'  - {referencia}: {motivo}'))

        if not aplicar:
            self.stdout.write('')
            self.stdout.write('Simulación. Vuelve a correrlo con --aplicar para copiar.')
        elif fallos:
            raise CommandError(
                f'{len(fallos)} archivos no se pudieron copiar. NO borres nada del '
                'bucket público hasta resolverlos.'
            )
