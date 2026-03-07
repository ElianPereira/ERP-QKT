"""
Management command: sincronizar_airbnb
Sincroniza todos los calendarios de Airbnb y detecta conflictos.

Uso:
    python manage.py sincronizar_airbnb
    
Para automatizar (cron cada 6 horas):
    0 */6 * * * cd /app && python manage.py sincronizar_airbnb >> /var/log/airbnb_sync.log 2>&1
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from airbnb.models import AnuncioAirbnb
from airbnb.services import SincronizadorAirbnbService, DetectorConflictosService


class Command(BaseCommand):
    help = 'Sincroniza calendarios de Airbnb y detecta conflictos con eventos de la quinta'

    def add_arguments(self, parser):
        parser.add_argument(
            '--anuncio',
            type=int,
            help='ID de anuncio específico a sincronizar (opcional)',
        )
        parser.add_argument(
            '--skip-conflictos',
            action='store_true',
            help='Omitir detección de conflictos',
        )

    def handle(self, *args, **options):
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"SINCRONIZACIÓN AIRBNB - {timezone.now().strftime('%Y-%m-%d %H:%M')}")
        self.stdout.write(f"{'='*60}\n")
        
        sincronizador = SincronizadorAirbnbService()
        
        # Sincronizar anuncio específico o todos
        anuncio_id = options.get('anuncio')
        
        if anuncio_id:
            try:
                anuncio = AnuncioAirbnb.objects.get(pk=anuncio_id, activo=True)
                self.stdout.write(f"Sincronizando: {anuncio.nombre}")
                
                creadas, actualizadas, errores = sincronizador.sincronizar_anuncio(anuncio)
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ {creadas} nuevas, {actualizadas} actualizadas, {errores} errores"
                    )
                )
            except AnuncioAirbnb.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Anuncio con ID {anuncio_id} no encontrado o inactivo")
                )
                return
        else:
            # Sincronizar todos
            anuncios = AnuncioAirbnb.objects.filter(activo=True)
            self.stdout.write(f"Anuncios activos: {anuncios.count()}\n")
            
            total_creadas = 0
            total_actualizadas = 0
            total_errores = 0
            
            for anuncio in anuncios:
                self.stdout.write(f"→ {anuncio.nombre}...")
                
                try:
                    creadas, actualizadas, errores = sincronizador.sincronizar_anuncio(anuncio)
                    total_creadas += creadas
                    total_actualizadas += actualizadas
                    total_errores += errores
                    
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ {creadas} nuevas, {actualizadas} actualizadas"
                        )
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ Error: {str(e)}")
                    )
                    total_errores += 1
            
            self.stdout.write(f"\n{'-'*40}")
            self.stdout.write(f"RESUMEN SINCRONIZACIÓN:")
            self.stdout.write(f"  Reservas nuevas:      {total_creadas}")
            self.stdout.write(f"  Reservas actualizadas:{total_actualizadas}")
            self.stdout.write(f"  Errores:              {total_errores}")
        
        # Detectar conflictos
        if not options.get('skip_conflictos'):
            self.stdout.write(f"\n{'-'*40}")
            self.stdout.write("DETECCIÓN DE CONFLICTOS:")
            
            detector = DetectorConflictosService()
            conflictos = detector.detectar_conflictos()
            
            if conflictos:
                self.stdout.write(
                    self.style.WARNING(f"  ⚠ {len(conflictos)} nuevos conflictos detectados:")
                )
                for c in conflictos[:5]:  # Mostrar máximo 5
                    self.stdout.write(
                        f"    • {c.fecha_conflicto}: {c.reserva_airbnb.anuncio.nombre} vs {c.cotizacion.nombre_evento}"
                    )
                if len(conflictos) > 5:
                    self.stdout.write(f"    ... y {len(conflictos) - 5} más")
            else:
                self.stdout.write(
                    self.style.SUCCESS("  ✓ No se detectaron nuevos conflictos")
                )
        
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(self.style.SUCCESS("Sincronización completada"))
        self.stdout.write(f"{'='*60}\n")
