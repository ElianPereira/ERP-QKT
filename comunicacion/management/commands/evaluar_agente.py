"""
Corre las pruebas del agente de WhatsApp contra el modelo real (Issue #366).

Uso:
    python manage.py evaluar_agente                 # solo lista los casos
    python manage.py evaluar_agente --ejecutar      # los corre (cuesta, llama a Anthropic)
    python manage.py evaluar_agente --ejecutar --caso erp --caso mi_saldo
    python manage.py evaluar_agente --ejecutar --salida reporte.json

Correrlo antes y después de cambiar el prompt, el modelo o el esfuerzo: un
ajuste que arregla un caso puede romper otro. Termina con código 1 si falla
algún caso.
"""
import json
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from comunicacion.evaluacion_agente import cargar_casos, evaluar


class Command(BaseCommand):
    help = 'Corre las pruebas del agente de WhatsApp contra el modelo real.'

    def add_arguments(self, parser):
        parser.add_argument('--ejecutar', action='store_true',
                            help='Corre los casos (llama a la API de Anthropic y cuesta).')
        parser.add_argument('--caso', action='append', help='Id de un caso; se puede repetir.')
        parser.add_argument('--salida', help='Archivo JSON donde guardar el reporte completo.')

    def handle(self, *args, **opciones):
        casos = cargar_casos(opciones['caso'])
        if not casos:
            raise CommandError('No hay casos con esos ids.')
        if not opciones['ejecutar']:
            for c in casos:
                self.stdout.write(f"{c['id']:<26} {c['tipo']:<10} {c['mensajes'][-1][:70]}")
            self.stdout.write(f'\n{len(casos)} casos. Agrega --ejecutar para correrlos contra el modelo.')
            return

        resultados = evaluar(opciones['caso'])
        costo = sum((r['costo_usd'] for r in resultados), Decimal('0'))
        for r in resultados:
            estado = self.style.SUCCESS('OK   ') if r['ok'] else self.style.ERROR('FALLA')
            self.stdout.write(f"{estado} {r['id']:<26} {r['tipo']:<10} {', '.join(r['herramientas']) or '—'}")
            for falla in r['fallas']:
                self.stdout.write(f'      - {falla}')
            if not r['ok'] and r['respuestas']:
                self.stdout.write(f"      Respuesta: {r['respuestas'][-1][:300]}")
        aprobados = sum(r['ok'] for r in resultados)
        self.stdout.write(f'\n{aprobados}/{len(resultados)} casos aprobados. Costo estimado: US${costo}')
        if opciones['salida']:
            with open(opciones['salida'], 'w', encoding='utf-8') as f:
                json.dump(resultados, f, ensure_ascii=False, indent=2, default=str)
        if aprobados < len(resultados):
            raise SystemExit(1)
