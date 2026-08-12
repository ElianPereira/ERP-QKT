from django.core.management.base import BaseCommand, CommandError

from comercial.services_recuperacion import (
    CLOUD_NAME_DEFAULT,
    RecuperacionError,
    recuperar_archivos,
)


class Command(BaseCommand):
    help = "Recupera archivos históricos desde las URLs públicas de Cloudinary al storage actual."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cloud-name",
            default=CLOUD_NAME_DEFAULT,
            help=f"Cloud name de origen (default: {CLOUD_NAME_DEFAULT}).",
        )

    def handle(self, *args, **options):
        try:
            resultado = recuperar_archivos(options["cloud_name"])
        except RecuperacionError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write("")
        self.stdout.write("Resumen de recuperación:")
        self.stdout.write(f"  Total procesados: {resultado.procesados}")
        self.stdout.write(self.style.SUCCESS(f"  Recuperados: {resultado.recuperados}"))
        self.stdout.write(f"  Ya existentes (omitidos): {resultado.omitidos}")
        self.stdout.write(
            self.style.WARNING(f"  No encontrados: {len(resultado.no_encontrados)}")
        )

        for referencia, intentos in resultado.no_encontrados:
            self.stdout.write(self.style.WARNING(f"  - {referencia}"))
            for url, detalle in intentos:
                self.stdout.write(f"      {url} -> {detalle}")
