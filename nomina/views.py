import pandas as pd
import io
import os
import math
import logging
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from datetime import datetime, date, time, timedelta
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from .models import Empleado, ReciboNomina
from weasyprint import HTML

logger = logging.getLogger(__name__)


# ==========================================
# UTILIDADES DE PARSEO
# ==========================================

def parsear_horas_complejas(valor):
    """Parsea h:mm:ss, h:mm, decimal. Retorna float horas decimales."""
    try:
        if pd.isna(valor) or valor == '-' or str(valor).strip() == '':
            return 0.0
        s = str(valor).strip()
        if ':' in s:
            parts = s.split(':')
            h = float(parts[0])
            m = float(parts[1])
            sec = float(parts[2]) if len(parts) > 2 else 0.0
            return round(h + (m / 60.0) + (sec / 3600.0), 4)
        return round(float(s), 4)
    except:
        return 0.0


def parsear_hms(valor):
    """Parsea h:mm:ss → tupla (h, m, s)."""
    try:
        if pd.isna(valor) or valor == '-' or str(valor).strip() == '':
            return (0, 0, 0)
        s = str(valor).strip()
        if ':' in s:
            parts = s.split(':')
            return (int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)
        val = float(s)
        h = int(val)
        resto_min = (val - h) * 60
        m = int(resto_min)
        sec = int((resto_min - m) * 60)
        return (h, m, sec)
    except:
        return (0, 0, 0)


def redondear_horas_90(horas_decimal):
    """
    Regla 90%: fracción < 54 min → truncar. >= 54 min → redondear arriba.
    Ej: 6.11h (7min) → 6h | 6.92h (55min) → 7h
    """
    if horas_decimal <= 0:
        return 0.0
    parte_entera = int(horas_decimal)
    fraccion = horas_decimal - parte_entera
    if fraccion >= 0.9:
        return float(parte_entera + 1)
    else:
        return float(parte_entera)


def calcular_hora_salida(hora_entrada, horas_h, horas_m, horas_s):
    """Calcula salida = entrada + duración."""
    try:
        dt_entrada = datetime(2026, 1, 1, hora_entrada.hour, hora_entrada.minute, 0)
        dt_salida = dt_entrada + timedelta(hours=horas_h, minutes=horas_m, seconds=horas_s)
        return dt_salida.strftime('%H:%M')
    except:
        return '-'


def parsear_horario_trabajo(df):
    """Extrae Work Schedule del Excel. Retorna {weekday: time}."""
    DIAS_EN = {
        'MONDAY': 0, 'TUESDAY': 1, 'WEDNESDAY': 2, 'THURSDAY': 3,
        'FRIDAY': 4, 'SATURDAY': 5, 'SUNDAY': 6
    }
    horarios = {i: time(8, 0) for i in range(7)}
    try:
        for r in range(len(df)):
            val = str(df.iloc[r, 0]).strip().upper() if pd.notna(df.iloc[r, 0]) else ''
            if 'WORK SCHEDULE' in val or val == 'DAY':
                start_row = r + 1 if 'DAY' not in val else r + 1
                if 'DAY' not in val:
                    for r2 in range(r, min(r + 5, len(df))):
                        if pd.notna(df.iloc[r2, 0]) and 'DAY' in str(df.iloc[r2, 0]).upper():
                            start_row = r2 + 1
                            break
                for r3 in range(start_row, min(start_row + 7, len(df))):
                    dia_txt = str(df.iloc[r3, 0]).strip().upper() if pd.notna(df.iloc[r3, 0]) else ''
                    for dia_en, dia_idx in DIAS_EN.items():
                        if dia_en in dia_txt:
                            val_start = df.iloc[r3, 1]
                            if isinstance(val_start, time):
                                horarios[dia_idx] = val_start
                            elif pd.notna(val_start) and str(val_start).upper() != 'REST':
                                try:
                                    t = pd.to_datetime(str(val_start)).time()
                                    horarios[dia_idx] = t
                                except:
                                    pass
                            break
                break
    except:
        pass
    return horarios


# ==========================================
# GENERADOR DE RECIBOS (reutilizable)
# ==========================================

def _generar_recibos_desde_datos(datos_empleados):
    """Genera recibos PDF desde dict {nombre: [registros]}. Retorna count."""
    count = 0
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"

    for nombre, registros in datos_empleados.items():
        if not registros:
            continue

        total_horas_reales = round(sum(r['horas_raw'] for r in registros), 2)
        total_horas_a_pagar = sum(r['horas_a_pagar'] for r in registros)
        ahorro_horas = round(total_horas_reales - total_horas_a_pagar, 2)

        empleado_obj, _ = Empleado.objects.get_or_create(nombre=nombre)

        fechas_dt = [pd.to_datetime(r['fecha']) for r in registros]
        periodo = f"{min(fechas_dt).strftime('%Y-%m-%d')} al {max(fechas_dt).strftime('%Y-%m-%d')}"

        tarifa = float(empleado_obj.tarifa_base)
        total_pagado = round(total_horas_a_pagar * tarifa, 2)
        total_sin_redondeo = round(total_horas_reales * tarifa, 2)
        ahorro_dinero = round(total_sin_redondeo - total_pagado, 2)

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
        safe_name = "".join([c for c in nombre if c.isalnum() or c == ' ']).strip().replace(' ', '_')
        recibo.archivo_pdf.save(f"Nomina_{safe_name}.pdf", ContentFile(pdf))
        count += 1

    return count


# ==========================================
# VISTA 1: CARGA DESDE EXCEL
# ==========================================

@staff_member_required
def cargar_nomina(request):
    if request.method == 'POST' and request.FILES.get('archivo_excel'):
        archivo = request.FILES['archivo_excel']
        try:
            if archivo.name.endswith('.csv'):
                df = pd.read_csv(archivo, header=None, sep=None, engine='python')
            else:
                df = pd.read_excel(archivo, header=None)

            horarios_semana = parsear_horario_trabajo(df)

            row_fechas_idx = -1
            mapa_columnas_fechas = {}
            for r in range(min(20, len(df))):
                fila = df.iloc[r].values
                fechas_encontradas = 0
                temp_map = {}
                for c, val in enumerate(fila):
                    if pd.isna(val):
                        continue
                    try:
                        fecha_dt = pd.to_datetime(val)
                        if not pd.isna(fecha_dt) and fecha_dt.year > 2000:
                            temp_map[c] = fecha_dt
                            fechas_encontradas += 1
                    except:
                        pass
                if fechas_encontradas > 3:
                    row_fechas_idx = r
                    mapa_columnas_fechas = temp_map
                    break

            if not mapa_columnas_fechas:
                messages.error(request, "No encontré la fila de fechas en el archivo.")
                return redirect('admin:nomina_recibonomina_changelist')

            datos_empleados = {}
            for r in range(row_fechas_idx + 1, len(df)):
                fila = df.iloc[r]
                fila_txt = [str(x).upper().strip() for x in fila.values]
                es_fila_payroll = False
                nombre = ""
                for i in range(min(5, len(fila_txt))):
                    txt = fila_txt[i]
                    if "PAYROLL" in txt or "HORAS" in txt:
                        es_fila_payroll = True
                        nombre = fila_txt[0]
                        break
                if es_fila_payroll and nombre and nombre not in ["NAN", "", "-"]:
                    if nombre not in datos_empleados:
                        datos_empleados[nombre] = []
                    for col_idx, fecha_obj in mapa_columnas_fechas.items():
                        if col_idx < len(fila):
                            horas_raw = parsear_horas_complejas(fila[col_idx])
                            h, m, s = parsear_hms(fila[col_idx])
                            if horas_raw > 0:
                                dia_semana = fecha_obj.weekday()
                                hora_entrada_prog = horarios_semana.get(dia_semana, time(8, 0))
                                hora_salida = calcular_hora_salida(hora_entrada_prog, h, m, s)
                                horas_a_pagar = redondear_horas_90(horas_raw)
                                datos_empleados[nombre].append({
                                    'fecha': fecha_obj.strftime('%Y-%m-%d'),
                                    'dia': fecha_obj.strftime('%A')[:3],
                                    'entrada': hora_entrada_prog.strftime('%H:%M'),
                                    'salida': hora_salida,
                                    'horas_fmt': f"{h}:{m:02d}:{s:02d}",
                                    'horas_raw': horas_raw,
                                    'horas_a_pagar': horas_a_pagar,
                                    'horas_a_pagar_fmt': f"{horas_a_pagar:.0f}:00",
                                    'fue_recortado': horas_a_pagar < horas_raw,
                                })

            count = _generar_recibos_desde_datos(datos_empleados)
            if count > 0:
                messages.success(request, f"Éxito: {count} recibos generados con regla de redondeo 90%.")
            else:
                messages.warning(request, "No se encontraron datos procesables.")
            return redirect('admin:nomina_recibonomina_changelist')

        except Exception as e:
            messages.error(request, f"Error crítico al procesar: {e}")
            return redirect('admin:nomina_recibonomina_changelist')

    return render(request, 'nomina/formulario_carga.html')


# ==========================================
# VISTA 2: SYNC JIBBLE (ADMIN)
# ==========================================

@staff_member_required
def sync_jibble_view(request):
    """Sincroniza timesheets desde Jibble vía API."""
    from .services import JibbleService, JibbleAPIError

    svc = JibbleService()
    if not svc.esta_configurado():
        messages.error(request, "Jibble no configurado. Agrega JIBBLE_CLIENT_ID y JIBBLE_CLIENT_SECRET en Railway.")
        return redirect('admin:nomina_recibonomina_changelist')

    if request.method == 'POST':
        fecha_inicio = request.POST.get('fecha_inicio', '')
        fecha_fin = request.POST.get('fecha_fin', '')
        if not fecha_inicio or not fecha_fin:
            messages.error(request, "Selecciona fecha inicio y fin.")
            return redirect('admin:nomina_recibonomina_changelist')
        try:
            svc.autenticar()
            resultado = svc.obtener_timesheets_semana(fecha_inicio, fecha_fin)
            personas = resultado.get('personas', {})
            fuente = resultado.get('fuente', '?')
            if not personas:
                messages.warning(request, f"Jibble ({fuente}): No se encontraron datos para el periodo.")
                return redirect('admin:nomina_recibonomina_changelist')
            datos_empleados = _transformar_datos_jibble(personas)
            count = _generar_recibos_desde_datos(datos_empleados)
            if count > 0:
                messages.success(request, f"Jibble ({fuente}): {count} recibos generados con regla 90%.")
            else:
                messages.warning(request, "No se generaron recibos.")
        except JibbleAPIError as e:
            messages.error(request, f"Error Jibble: {e}")
        except Exception as e:
            messages.error(request, f"Error inesperado: {e}")
        return redirect('admin:nomina_recibonomina_changelist')

    return redirect('admin:nomina_recibonomina_changelist')


# ==========================================
# VISTA 3: WEBHOOK CRON (cron-job.org)
# ==========================================

@csrf_exempt
@require_POST
def webhook_sync_jibble(request):
    """
    Webhook para cron externo. Protegido por Bearer token.
    POST /api/nomina/sync-jibble/
    Header: Authorization: Bearer <NOMINA_CRON_TOKEN>
    """
    from .services import JibbleService, JibbleAPIError

    cron_token = getattr(settings, 'NOMINA_CRON_TOKEN', '')
    if not cron_token:
        return JsonResponse({'error': 'NOMINA_CRON_TOKEN no configurado'}, status=500)

    auth_header = request.headers.get('Authorization', '')
    if auth_header != f'Bearer {cron_token}':
        return JsonResponse({'error': 'No autorizado'}, status=401)

    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}

    fecha_inicio = body.get('fecha_inicio', '')
    fecha_fin = body.get('fecha_fin', '')
    if not fecha_inicio or not fecha_fin:
        hoy = date.today()
        lunes_pasado = hoy - timedelta(days=hoy.weekday() + 7)
        domingo_pasado = lunes_pasado + timedelta(days=6)
        fecha_inicio = lunes_pasado.strftime('%Y-%m-%d')
        fecha_fin = domingo_pasado.strftime('%Y-%m-%d')

    svc = JibbleService()
    if not svc.esta_configurado():
        return JsonResponse({'error': 'Jibble no configurado'}, status=500)

    try:
        svc.autenticar()
        resultado = svc.obtener_timesheets_semana(fecha_inicio, fecha_fin)
        personas = resultado.get('personas', {})
        fuente = resultado.get('fuente', '?')
        if not personas:
            return JsonResponse({'status': 'ok', 'recibos_generados': 0, 'fuente': fuente})
        datos_empleados = _transformar_datos_jibble(personas)
        count = _generar_recibos_desde_datos(datos_empleados)
        return JsonResponse({
            'status': 'ok', 'periodo': f'{fecha_inicio} al {fecha_fin}',
            'fuente': fuente, 'empleados_procesados': len(personas),
            'recibos_generados': count,
        })
    except JibbleAPIError as e:
        return JsonResponse({'error': f'Jibble API: {e}'}, status=502)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ==========================================
# VISTA 4: DIAGNÓSTICO JIBBLE
# ==========================================

@staff_member_required
def jibble_diagnostico_view(request):
    from .services import JibbleService
    svc = JibbleService()
    return JsonResponse(svc.diagnostico())


# ==========================================
# UTILIDAD: TRANSFORMAR DATOS JIBBLE
# ==========================================

def _transformar_datos_jibble(personas):
    """Transforma dict Jibble al formato estándar de _generar_recibos_desde_datos()."""
    datos_empleados = {}
    for nombre, info in personas.items():
        if not info['dias']:
            continue
        datos_empleados[nombre] = []
        for dia in info['dias']:
            seg = dia['duracion_segundos']
            horas_decimal = seg / 3600.0
            h = seg // 3600
            m = (seg % 3600) // 60
            s = seg % 60
            horas_a_pagar = redondear_horas_90(horas_decimal)
            datos_empleados[nombre].append({
                'fecha': dia['fecha'],
                'dia': datetime.strptime(dia['fecha'], '%Y-%m-%d').strftime('%A')[:3],
                'entrada': dia.get('entrada', '-'),
                'salida': dia.get('salida', '-'),
                'horas_fmt': f"{h}:{m:02d}:{s:02d}",
                'horas_raw': horas_decimal,
                'horas_a_pagar': horas_a_pagar,
                'horas_a_pagar_fmt': f"{horas_a_pagar:.0f}:00",
                'fue_recortado': horas_a_pagar < horas_decimal,
            })
    return datos_empleados