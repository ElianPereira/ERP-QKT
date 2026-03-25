import pandas as pd
import io
import os
import math
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from datetime import datetime, time, timedelta
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from .models import Empleado, ReciboNomina
from weasyprint import HTML


# ==========================================
# UTILIDADES DE PARSEO
# ==========================================

def parsear_horas_complejas(valor):
    """
    Parsea un valor de horas en múltiples formatos.
    Soporta: h:mm:ss, h:mm, decimal (6.5), texto.
    Retorna: float con horas decimales.
    """
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
    """
    Parsea un valor h:mm:ss y retorna tupla (horas, minutos, segundos).
    Retorna (0, 0, 0) si no se puede parsear.
    """
    try:
        if pd.isna(valor) or valor == '-' or str(valor).strip() == '':
            return (0, 0, 0)
        s = str(valor).strip()
        if ':' in s:
            parts = s.split(':')
            h = int(parts[0])
            m = int(parts[1])
            sec = int(parts[2]) if len(parts) > 2 else 0
            return (h, m, sec)
        # Si es decimal, convertir
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
    Regla de redondeo 90% para control de tiempo muerto.
    
    Lógica: Si la fracción de hora no alcanza el 90% (54 minutos de 60),
    se trunca al entero inferior. Si alcanza >=90%, se redondea al superior.
    
    Ejemplos:
        6.11 hrs (6h 07min) → 6.00 hrs  (7 min < 54 min → truncar)
        6.92 hrs (6h 55min) → 7.00 hrs  (55 min >= 54 min → redondear arriba)
        10.56 hrs (10h 34min) → 10.00 hrs (34 min < 54 min → truncar)
    
    Args:
        horas_decimal: float con las horas trabajadas (ej: 6.1144)
    
    Returns:
        float con las horas redondeadas para pago
    """
    if horas_decimal <= 0:
        return 0.0
    
    parte_entera = int(horas_decimal)
    fraccion = horas_decimal - parte_entera
    
    # 0.9 de hora = 54 minutos = 90% de la hora
    if fraccion >= 0.9:
        return float(parte_entera + 1)
    else:
        return float(parte_entera)


def calcular_hora_salida(hora_entrada, horas_h, horas_m, horas_s):
    """
    Calcula la hora de salida sumando la duración trabajada a la hora de entrada.
    
    Args:
        hora_entrada: datetime.time con la hora de entrada programada
        horas_h, horas_m, horas_s: int con duración trabajada
    
    Returns:
        str con hora de salida formateada "HH:MM" 
    """
    try:
        dt_entrada = datetime(2026, 1, 1, hora_entrada.hour, hora_entrada.minute, 0)
        dt_salida = dt_entrada + timedelta(hours=horas_h, minutes=horas_m, seconds=horas_s)
        return dt_salida.strftime('%H:%M')
    except:
        return '-'


def parsear_horario_trabajo(df):
    """
    Escanea el Excel buscando la sección 'Work Schedule' para extraer
    la hora de inicio (START) programada por día de la semana.
    
    Retorna dict: {0: time(8,0), 1: time(8,0), ...}  (0=Monday ... 6=Sunday)
    Default: 08:00 para todos los días si no encuentra la sección.
    """
    DIAS_EN = {
        'MONDAY': 0, 'TUESDAY': 1, 'WEDNESDAY': 2, 'THURSDAY': 3,
        'FRIDAY': 4, 'SATURDAY': 5, 'SUNDAY': 6
    }
    
    horarios = {i: time(8, 0) for i in range(7)}  # Default 08:00
    
    try:
        # Buscar fila con "Work Schedule"
        for r in range(len(df)):
            val = str(df.iloc[r, 0]).strip().upper() if pd.notna(df.iloc[r, 0]) else ''
            if 'WORK SCHEDULE' in val or val == 'DAY':
                # Si encontramos "DAY", las filas siguientes son los días
                start_row = r + 1 if 'DAY' not in val else r + 1
                
                # Buscar la fila header "DAY"
                if 'DAY' not in val:
                    for r2 in range(r, min(r + 5, len(df))):
                        if pd.notna(df.iloc[r2, 0]) and 'DAY' in str(df.iloc[r2, 0]).upper():
                            start_row = r2 + 1
                            break
                
                # Leer los 7 días
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
# VISTA PRINCIPAL: CARGA DE NÓMINA
# ==========================================

@staff_member_required
def cargar_nomina(request):
    if request.method == 'POST' and request.FILES.get('archivo_excel'):
        archivo = request.FILES['archivo_excel']
        
        try:
            # =====================
            # 1. LEER ARCHIVO
            # =====================
            if archivo.name.endswith('.csv'):
                df = pd.read_csv(archivo, header=None, sep=None, engine='python')
            else:
                df = pd.read_excel(archivo, header=None)

            # =====================
            # 2. PARSEAR HORARIO DE TRABAJO (Work Schedule)
            # =====================
            horarios_semana = parsear_horario_trabajo(df)

            # =====================
            # 3. ESCÁNER DE FECHAS
            # =====================
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

            # =====================
            # 4. ESCÁNER DE EMPLEADOS
            # =====================
            datos_empleados = {}

            for r in range(row_fechas_idx + 1, len(df)):
                fila = df.iloc[r]
                fila_txt = [str(x).upper().strip() for x in fila.values]
                
                es_fila_payroll = False
                nombre = ""
                
                # Buscamos PAYROLL u HORAS en las primeras columnas
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
                            # Parsear horas decimales y h:m:s
                            horas_raw = parsear_horas_complejas(fila[col_idx])
                            h, m, s = parsear_hms(fila[col_idx])
                            
                            if horas_raw > 0:
                                # Día de la semana (0=Monday)
                                dia_semana = fecha_obj.weekday()
                                hora_entrada_prog = horarios_semana.get(dia_semana, time(8, 0))
                                
                                # Calcular hora de salida real
                                hora_salida = calcular_hora_salida(hora_entrada_prog, h, m, s)
                                
                                # Aplicar regla de redondeo 90%
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

            # =====================
            # 5. GENERAR RECIBOS
            # =====================
            count = 0
            
            ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
            logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"

            for nombre, registros in datos_empleados.items():
                if not registros:
                    continue

                # Totales: horas reales vs horas a pagar
                total_horas_reales = round(sum(r['horas_raw'] for r in registros), 2)
                total_horas_a_pagar = sum(r['horas_a_pagar'] for r in registros)
                ahorro_horas = round(total_horas_reales - total_horas_a_pagar, 2)
                
                empleado_obj, _ = Empleado.objects.get_or_create(nombre=nombre)
                
                fechas_dt = [pd.to_datetime(r['fecha']) for r in registros]
                periodo = f"{min(fechas_dt).strftime('%Y-%m-%d')} al {max(fechas_dt).strftime('%Y-%m-%d')}"
                
                # El pago se calcula con las HORAS A PAGAR (post-redondeo)
                tarifa = float(empleado_obj.tarifa_base)
                total_pagado = round(total_horas_a_pagar * tarifa, 2)
                
                # Para referencia: cuánto HUBIERA sido sin redondeo
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

            if count > 0:
                messages.success(request, f"Éxito: {count} recibos generados con regla de redondeo 90%.")
            else:
                messages.warning(request, "No se encontraron datos procesables.")
            
            return redirect('admin:nomina_recibonomina_changelist')

        except Exception as e:
            messages.error(request, f"Error crítico al procesar: {e}")
            return redirect('admin:nomina_recibonomina_changelist')

    return render(request, 'nomina/formulario_carga.html')