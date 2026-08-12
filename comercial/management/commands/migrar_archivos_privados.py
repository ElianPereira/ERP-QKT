"""
Copia al bucket privado los documentos sensibles que todavía viven en el
bucket público (SEC-FILE-001a).

Simula por defecto; `--aplicar` escribe. La lógica vive en
`comercial/services_migracion_privada.py`, compartida con la página del admin
`/admin/migrar-archivos-privados/`.

Uso:
    python manage.py migrar_archivos_privados            # simula
    python manage.py migrar_archivos_privados --aplicar  # copia
"""
from django.core.management.base import BaseCommand, CommandError

from comercial.services_migracion_privada import MigracionError, migrar_archivos_privados


class Command(BaseCommand):
    help = 'Copia los documentos sensibles del bucket público al privado.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--aplicar', action='store_true',
            help='Copia de verdad. Sin esta bandera solo informa qué haría.',
        )

    def handle(self, *args, **options):
        aplicar = options['aplicar']
        try:
            resultado = migrar_archivos_privados(aplicar=aplicar)
        except MigracionError as exc:
            raise CommandError(str(exc)) from exc

        for referencia in resultado.sin_origen:
            self.stdout.write(self.style.WARNING(f'  sin origen: {referencia}'))

        self.stdout.write('')
        self.stdout.write('Resumen:')
        verbo = 'Copiados' if aplicar else 'Se copiarían'
        self.stdout.write(self.style.SUCCESS(f'  {verbo}: {resultado.copiados}'))
        self.stdout.write(f'  Ya estaban en el privado: {resultado.ya_estaban}')
        self.stdout.write(
            self.style.WARNING(f'  Sin archivo en el origen: {len(resultado.sin_origen)}')
        )
        self.stdout.write(self.style.ERROR(f'  Fallos: {len(resultado.fallos)}'))
        for referencia, motivo in resultado.fallos:
            self.stdout.write(self.style.ERROR(f'  - {referencia}: {motivo}'))

        if not aplicar:
            self.stdout.write('')
            self.stdout.write('Simulación. Vuelve a correrlo con --aplicar para copiar.')
        elif resultado.fallos:
            raise CommandError(
                f'{len(resultado.fallos)} archivos no se pudieron copiar. NO borres nada del '
                'bucket público hasta resolverlos.'
            )
