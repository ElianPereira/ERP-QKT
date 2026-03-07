"""
Management command: setup_airbnb
Configura los anuncios iniciales de Airbnb con las URLs de iCal.

Uso:
    python manage.py setup_airbnb
"""
from django.core.management.base import BaseCommand

from airbnb.models import AnuncioAirbnb


class Command(BaseCommand):
    help = 'Configura los anuncios iniciales de Airbnb'

    def handle(self, *args, **options):
        self.stdout.write("\n" + "="*60)
        self.stdout.write("CONFIGURACIÓN INICIAL DE ANUNCIOS AIRBNB")
        self.stdout.write("="*60 + "\n")
        
        # Definición de los 3 anuncios
        anuncios_data = [
            {
                'nombre': 'Habitación 1 - Quinta',
                'tipo': 'HABITACION',
                'url_ical': 'https://www.airbnb.mx/calendar/ical/1499565328932084820.ics?t=4d1912af66194ada93e3d180830886e2',
                'afecta_eventos_quinta': True,
            },
            {
                'nombre': 'Habitación 2 - Quinta',
                'tipo': 'HABITACION',
                'url_ical': 'https://www.airbnb.mx/calendar/ical/1505421520138203166.ics?t=12c79d6eab29484884694f82d9db8390',
                'afecta_eventos_quinta': True,
            },
            {
                'nombre': 'Casa Completa',
                'tipo': 'CASA',
                'url_ical': 'https://www.airbnb.mx/calendar/ical/1448640293323478515.ics?t=2358dcdba5c747b4ac5097e476ce2987',
                'afecta_eventos_quinta': False,  # NO afecta la quinta
            },
        ]
        
        creados = 0
        actualizados = 0
        
        for data in anuncios_data:
            anuncio, created = AnuncioAirbnb.objects.update_or_create(
                url_ical=data['url_ical'],
                defaults={
                    'nombre': data['nombre'],
                    'tipo': data['tipo'],
                    'afecta_eventos_quinta': data['afecta_eventos_quinta'],
                    'activo': True,
                }
            )
            
            if created:
                creados += 1
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ Creado: {anuncio.nombre}")
                )
            else:
                actualizados += 1
                self.stdout.write(
                    f"  → Actualizado: {anuncio.nombre}"
                )
            
            # Mostrar info adicional
            self.stdout.write(f"    Tipo: {anuncio.get_tipo_display()}")
            self.stdout.write(f"    Afecta quinta: {'Sí ⚠️' if anuncio.afecta_eventos_quinta else 'No'}")
            self.stdout.write(f"    Listing ID: {anuncio.airbnb_listing_id}")
            self.stdout.write("")
        
        self.stdout.write("-"*40)
        self.stdout.write(f"Creados: {creados}")
        self.stdout.write(f"Actualizados: {actualizados}")
        self.stdout.write("")
        
        self.stdout.write(
            self.style.SUCCESS(
                "✓ Configuración completada. Ejecuta 'python manage.py sincronizar_airbnb' para sincronizar."
            )
        )
        self.stdout.write("="*60 + "\n")
