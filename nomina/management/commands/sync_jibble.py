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

import os
from datetime import date, timedelta, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.conf import settings

from weasyprint import HTML

from nomina.models import Empleado, ReciboNomina
from nomina.services import JibbleService, JibbleAPIError
from nomina.views import redondear_horas_90


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
        ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
        logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"

        count = 0
        dry_run = options['dry_run']

        for nombre, info in resultado['personas'].items():
            if not info['dias']:
                continue

            empleado_obj, created = Empleado.objects.get_or_create(nombre=nombre)
            if created:
                self.stdout.write(f"  Empleado NUEVO creado: {nombre}")

            tarifa = float(empleado_obj.tarifa_base)
            registros = []
            total_horas_reales = 0.0
            total_horas_a_pagar = 0.0

            for dia in info['dias']:
                seg = dia['duracion_segundos']
                horas_decimal = seg / 3600.0
                h = seg // 3600
                m = (seg % 3600) // 60
                s = seg % 60

                horas_pagar = redondear_horas_90(horas_decimal)
                total_horas_reales += horas_decimal
                total_horas_a_pagar += horas_pagar

                registros.append({
                    'fecha': dia['fecha'],
                    'dia': datetime.strptime(dia['fecha'], '%Y-%m-%d').strftime('%A')[:3],
                    'entrada': dia.get('entrada', '-'),
                    'salida': dia.get('salida', '-'),
                    'horas_fmt': f"{h}:{m:02d}:{s:02d}",
                    'horas_raw': horas_decimal,
                    'horas_a_pagar': horas_pagar,
                    'horas_a_pagar_fmt': f"{horas_pagar:.0f}:00",
                    'fue_recortado': horas_pagar < horas_decimal,
                })

            total_horas_reales = round(total_horas_reales, 2)
            ahorro_horas = round(total_horas_reales - total_horas_a_pagar, 2)
            total_pagado = round(total_horas_a_pagar * tarifa, 2)
            total_sin_redondeo = round(total_horas_reales * tarifa, 2)
            ahorro_dinero = round(total_sin_redondeo - total_pagado, 2)

            self.stdout.write(
                f"  {nombre}: {total_horas_reales}h reales → {total_horas_a_pagar:.0f}h a pagar "
                f"(${total_pagado:,.2f}, ahorro ${ahorro_dinero:,.2f})"
            )

            if dry_run:
                continue

            periodo = f"{fecha_inicio} al {fecha_fin}"

            context = {
                'empleado': empleado_obj,
                'periodo': periodo,
                'lista_asistencia': registros,
                'total_horas_reales': f"{total_horas_reales:.2f}",
                'total_horas_a_pagar': f"{total_horas_a_pagar:.0f}",
                'ahorro_horas': f"{ahorro_horas:.2f}",
                'total_pagado': f"{total_pagado:,.2f}",
                'total_sin_redondeo': f"{total_sin_redondeo:,.2f}",
                'ahorro_dinero': f"{ahorro_dinero:,.2f}",
                'folio': f"NOM-{ReciboNomina.objects.count()+1:03d}",
                'logo_url': logo_url,
            }

            html = render_to_string('nomina/recibo_nomina.html', context)
            pdf = HTML(string=html).write_pdf()

            recibo = ReciboNomina.objects.create(
                empleado=empleado_obj,
                periodo=periodo,
                horas_trabajadas=Decimal(str(total_horas_a_pagar)),
                tarifa_aplicada=empleado_obj.tarifa_base,
                total_pagado=Decimal(str(total_pagado)),
            )

            safe_name = "".join(
                [c for c in nombre if c.isalnum() or c == ' ']
            ).strip().replace(' ', '_')
            recibo.archivo_pdf.save(f"Nomina_{safe_name}.pdf", ContentFile(pdf))
            count += 1

        if dry_run:
            self.stdout.write(self.style.NOTICE(f'  DRY RUN: {len(resultado["personas"])} empleados procesados (sin guardar).'))
        elif count > 0:
            self.stdout.write(self.style.SUCCESS(f'  {count} recibos generados exitosamente.'))
        else:
            self.stdout.write(self.style.WARNING('  No se generaron recibos.'))