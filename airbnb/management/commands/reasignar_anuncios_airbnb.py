"""
Comando de gestión: reasignar_anuncios_airbnb
=============================================
Intenta asignar retroactivamente un AnuncioAirbnb a los registros PagoAirbnb
que quedaron con anuncio=None (sin anuncio asignado).

Se aplican tres estrategias en orden:

  1. Por espacio_csv: usa el nombre guardado del CSV original con la misma lógica
     de _buscar_anuncio (búsqueda directa, inversa y por primera palabra).
  2. Por código de confirmación: busca si el uid_ical de alguna ReservaAirbnb
     contiene el código de confirmación del pago.
  3. Por coincidencia de fechas: busca una ReservaAirbnb cuyo check-in y check-out
     coincidan exactamente con el pago.

Uso:
    python manage.py reasignar_anuncios_airbnb
    python manage.py reasignar_anuncios_airbnb --dry-run
    python manage.py reasignar_anuncios_airbnb --verbose
"""

from django.core.management.base import BaseCommand
from django.db.models import Q

from airbnb.models import AnuncioAirbnb, PagoAirbnb, ReservaAirbnb


class Command(BaseCommand):
    help = "Reasigna anuncios a pagos de Airbnb que quedaron sin asignar (anuncio=None)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra qué cambiaría sin guardar nada en BD",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Imprime el detalle de cada pago procesado",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbose = options["verbose"]

        if dry_run:
            self.stdout.write(self.style.WARNING("[ DRY-RUN ] No se guardarán cambios\n"))

        sin_asignar = PagoAirbnb.objects.filter(anuncio__isnull=True).order_by("fecha_checkin")
        total = sin_asignar.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("✓ Todos los pagos ya tienen anuncio asignado."))
            return

        self.stdout.write(f"Procesando {total} pago(s) sin anuncio asignado...\n")

        asignados = 0
        no_encontrados = []

        for pago in sin_asignar:
            anuncio, estrategia = self._resolver_anuncio(pago)

            if anuncio:
                if verbose:
                    self.stdout.write(
                        f"  [{estrategia}] COD:{pago.codigo_confirmacion or '-'} "
                        f"{pago.huesped} ({pago.fecha_checkin}) → {anuncio.nombre}"
                    )
                if not dry_run:
                    pago.anuncio = anuncio
                    pago.save(update_fields=["anuncio"])
                asignados += 1
            else:
                no_encontrados.append(pago)
                if verbose:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [SIN MATCH] COD:{pago.codigo_confirmacion or '-'} "
                            f"{pago.huesped} ({pago.fecha_checkin}) "
                            f"espacio_csv='{pago.espacio_csv}'"
                        )
                    )

        # Resumen
        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(f"[ DRY-RUN ] Se asignarían: {asignados}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ Asignados: {asignados} / {total}"))

        if no_encontrados:
            self.stdout.write(
                self.style.ERROR(
                    f"✗ Sin coincidencia ({len(no_encontrados)} pago(s)) — "
                    "verifica que el anuncio exista en la BD con el nombre correcto:"
                )
            )
            for p in no_encontrados:
                self.stdout.write(
                    f"    • id={p.id} | cod={p.codigo_confirmacion or '-'} | "
                    f"huesped={p.huesped} | checkin={p.fecha_checkin} | "
                    f"espacio_csv='{p.espacio_csv}'"
                )

    # ------------------------------------------------------------------
    # Estrategias de resolución
    # ------------------------------------------------------------------

    def _resolver_anuncio(self, pago):
        """Intenta determinar el anuncio de un pago usando múltiples estrategias.

        Returns:
            (AnuncioAirbnb | None, str descripción_estrategia)
        """
        # Estrategia 1: por espacio_csv guardado
        if pago.espacio_csv:
            anuncio = self._buscar_por_nombre(pago.espacio_csv)
            if anuncio:
                return anuncio, "espacio_csv"

        # Estrategia 2: por código de confirmación en uid_ical de ReservaAirbnb
        if pago.codigo_confirmacion:
            reserva = ReservaAirbnb.objects.filter(
                uid_ical__icontains=pago.codigo_confirmacion
            ).select_related("anuncio").first()
            if reserva:
                return reserva.anuncio, "codigo_confirmacion→reserva"

        # Estrategia 3: por coincidencia exacta de fechas en ReservaAirbnb
        reservas_fecha = ReservaAirbnb.objects.filter(
            fecha_inicio=pago.fecha_checkin,
            fecha_fin=pago.fecha_checkout,
        ).exclude(estado__in=["CANCELADA", "BLOQUEADA"]).select_related("anuncio")

        if reservas_fecha.count() == 1:
            return reservas_fecha.first().anuncio, "fechas_exactas"

        # Estrategia 4: por fecha de check-in único en ese día
        reservas_checkin = ReservaAirbnb.objects.filter(
            fecha_inicio=pago.fecha_checkin,
        ).exclude(estado__in=["CANCELADA", "BLOQUEADA"]).select_related("anuncio")

        if reservas_checkin.count() == 1:
            return reservas_checkin.first().anuncio, "checkin_único"

        # Estrategia 5: fechas con tolerancia ±1 día en checkout
        # (a veces Airbnb CSV y iCal difieren un día en fecha_fin)
        from datetime import timedelta
        reservas_tolerancia = ReservaAirbnb.objects.filter(
            fecha_inicio=pago.fecha_checkin,
            fecha_fin__in=[
                pago.fecha_checkout,
                pago.fecha_checkout + timedelta(days=1),
                pago.fecha_checkout - timedelta(days=1),
            ],
        ).exclude(estado__in=["CANCELADA", "BLOQUEADA"]).select_related("anuncio")

        if reservas_tolerancia.count() == 1:
            return reservas_tolerancia.first().anuncio, "fechas_tolerancia±1"

        return None, ""

    def _buscar_por_nombre(self, texto):
        """Misma lógica que ImportadorCSVPagosService._buscar_anuncio."""
        if not texto:
            return None

        # Directa: el nombre del anuncio contiene el texto
        anuncio = AnuncioAirbnb.objects.filter(nombre__icontains=texto).first()
        if anuncio:
            return anuncio

        # Inversa: el texto contiene el nombre del anuncio (match más largo)
        texto_lower = texto.lower()
        mejor = None
        for a in AnuncioAirbnb.objects.filter(activo=True):
            if a.nombre.lower() in texto_lower:
                if mejor is None or len(a.nombre) > len(mejor.nombre):
                    mejor = a
        if mejor:
            return mejor

        # Fallback: primera palabra
        primera = texto.split()[0] if texto.split() else texto
        return AnuncioAirbnb.objects.filter(nombre__icontains=primera).first()
