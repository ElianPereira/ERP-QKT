"""
Management command: sync_jibble
================================
Sincroniza timesheets de Jibble y genera recibos de nómina.

Uso manual:
    python manage.py sync_jibble                          # Semana anterior
    python manage.py sync_jibble --inicio 2026-03-16 --fin 2026-03-22
    python manage.py sync_jibble --diagnostico            # Solo verificar conexión

Railway Cron (cada lunes a las 7am):
    python manage.py sync_jibble
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError

from nomina.models import Empleado
from nomina.services import JibbleAPIError, JibbleService
from nomina.services_recibos import calcular_recibo, generar_recibo
from nomina.views import _transformar_datos_jibble


class Command(BaseCommand):
    help = 'Sincroniza timesheets de Jibble y genera recibos de nómina'

    def add_arguments(self, parser):
        parser.add_argument(
            '--inicio',
            type=str,
            help='Fecha inicio (YYYY-MM-DD). Default: lunes de la semana anterior.',
        )
        parser.add_argument(
            '--fin',
            type=str,
            help='Fecha fin (YYYY-MM-DD). Default: domingo de la semana anterior.',
        )
        parser.add_argument(
            '--diagnostico',
            action='store_true',
            help='Solo ejecutar diagnóstico de conexión, sin generar recibos.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simular procesamiento sin guardar recibos en DB.',
        )

    def handle(self, *args, **options):
        svc = JibbleService()

        # =====================
        # MODO DIAGNÓSTICO
        # =====================
        if options['diagnostico']:
            self.stdout.write(self.style.NOTICE('Ejecutando diagnóstico Jibble...'))
            diag = svc.diagnostico()

            self.stdout.write(f"  Configurado: {diag['configurado']}")
            self.stdout.write(f"  Autenticado: {diag['autenticado']}")
            self.stdout.write(f"  Personas encontradas: {diag['personas_count']}")

            for p in diag['personas_muestra']:
                self.stdout.write(f"    - {p['nombre']} (ID: {p['id']})")

            if diag['errores']:
                for err in diag['errores']:
                    self.stdout.write(self.style.ERROR(f"  ERROR: {err}"))
            else:
                self.stdout.write(self.style.SUCCESS('  Conexión OK'))
            return

        # =====================
        # DETERMINAR RANGO DE FECHAS
        # =====================
        if options['inicio'] and options['fin']:
            fecha_inicio = options['inicio']
            fecha_fin = options['fin']
        else:
            # Default: semana anterior (lunes a domingo)
            hoy = date.today()
            lunes_pasado = hoy - timedelta(days=hoy.weekday() + 7)
            domingo_pasado = lunes_pasado + timedelta(days=6)
            fecha_inicio = lunes_pasado.strftime('%Y-%m-%d')
            fecha_fin = domingo_pasado.strftime('%Y-%m-%d')

        self.stdout.write(
            self.style.NOTICE(f'Procesando nómina Jibble: {fecha_inicio} al {fecha_fin}')
        )

        # =====================
        # AUTENTICAR Y OBTENER DATOS
        # =====================
        try:
            svc.autenticar()
            self.stdout.write('  Autenticación OK')
        except JibbleAPIError as e:
            raise CommandError(f'Error de autenticación: {e}')

        try:
            resultado = svc.obtener_timesheets_semana(fecha_inicio, fecha_fin)
            self.stdout.write(f"  Fuente: {resultado['fuente']}")
            self.stdout.write(f"  Empleados encontrados: {len(resultado['personas'])}")
        except JibbleAPIError as e:
            raise CommandError(f'Error al obtener timesheets: {e}')

        if not resultado['personas']:
            self.stdout.write(self.style.WARNING('  No se encontraron datos de tiempo.'))
            return

        # =====================
        # PROCESAR Y GENERAR RECIBOS
        # =====================
        dry_run = options['dry_run']
        datos_empleados, fecha_emision_map = _transformar_datos_jibble(resultado['personas'])
        count = 0

        for nombre, registros in datos_empleados.items():
            empleado_obj, created = Empleado.objects.get_or_create(nombre=nombre)
            if created:
                self.stdout.write(f"  Empleado NUEVO creado: {nombre}")

            calculo = calcular_recibo(empleado_obj, registros)
            self.stdout.write(
                f"  {nombre}: {calculo['horas_reales']}h reales → {calculo['horas_a_pagar']}h a pagar "
                f"(${calculo['total']:,.2f}, ahorro ${calculo['ajuste_dinero']:,.2f})"
            )
            if dry_run:
                continue

            generar_recibo(
                empleado_obj, registros, inicio=fecha_inicio, fin=fecha_fin,
                fecha_emision=fecha_emision_map.get(nombre, ''),
            )
            count += 1

        if dry_run:
            self.stdout.write(self.style.NOTICE(f'  DRY RUN: {len(resultado["personas"])} empleados procesados (sin guardar).'))
        elif count > 0:
            self.stdout.write(self.style.SUCCESS(f'  {count} recibos generados exitosamente.'))
        else:
            self.stdout.write(self.style.WARNING('  No se generaron recibos.'))
