import pandas as pd
import io
import os
from django.conf import settings
from datetime import datetime, time
from django.shortcuts import render, redirect
from django.contrib import messages
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.contrib.admin.views.decorators import staff_member_required
from .models import Empleado, ReciboNomina
from weasyprint import HTML

def parsear_horas_complejas(valor):
    try:
        if pd.isna(valor) or valor == '-' or str(valor).strip() == '':
            return 0.0
        s = str(valor).strip()
        if ':' in s:
            parts = s.split(':')
            h = float(parts[0])
            m = float(parts[1])
            sec = float(parts[2]) if len(parts) > 2 else 0.0
            return round(h + (m / 60.0) + (sec / 3600.0), 2)
        return round(float(s), 2)
    except:
        return 0.0

@staff_member_required
def cargar_nomina(request):
    # --- CORRECCIÓN CLAVE ---
    # Cambiamos 'excel_file' por 'archivo_excel' para que coincida con tu HTML
    if request.method == 'POST' and request.FILES.get('archivo_excel'):
        archivo = request.FILES['archivo_excel']
        
        try:
            # 1. Leer archivo
            if archivo.name.endswith('.csv'):
                df = pd.read_csv(archivo, header=None, sep=None, engine='python')
            else:
                df = pd.read_excel(archivo, header=None)

            # 2. ESCÁNER DE FECHAS
            row_fechas_idx = -1
            mapa_columnas_fechas = {} 

            for r in range(min(20, len(df))):
                fila = df.iloc[r].values
                fechas_encontradas = 0
                temp_map = {}
                for c, val in enumerate(fila):
                    if pd.isna(val): continue
                    try:
                        fecha_dt = pd.to_datetime(val)
                        if not pd.isna(fecha_dt) and fecha_dt.year > 2000:
                            temp_map[c] = fecha_dt
                            fechas_encontradas += 1
                    except: pass
                
                if fechas_encontradas > 3:
                    row_fechas_idx = r
                    mapa_columnas_fechas = temp_map
                    break
            
            if not mapa_columnas_fechas:
                messages.error(request, "❌ No encontré la fila de fechas en el archivo.")
                # Redirige de vuelta a la lista del admin
                return redirect('admin:nomina_recibonomina_changelist')

            # 3. ESCÁNER DE EMPLEADOS
            datos_empleados = {} 

            for r in range(row_fechas_idx + 1, len(df)):
                fila = df.iloc[r]
                fila_txt = [str(x).upper().strip() for x in fila.values]
                
                es_fila_nomina = False
                nombre = ""
                # Buscamos PAYROLL u HORAS en las primeras columnas
                for i in range(min(5, len(fila_txt))):
                    txt = fila_txt[i]
                    if "PAYROLL" in txt or "HORAS" in txt:
                        es_fila_nomina = True
                        nombre = fila_txt[0] 
                        break
                
                if es_fila_nomina and nombre and nombre not in ["NAN", "", "-"]:
                    if nombre not in datos_empleados: datos_empleados[nombre] = []
                    
                    for col_idx, fecha_obj in mapa_columnas_fechas.items():
                        if col_idx < len(fila):
                            horas = parsear_horas_complejas(fila[col_idx])
                            # Solo agregamos si hay horas > 0
                            if horas > 0:
                                datos_empleados[nombre].append({
                                    'fecha': fecha_obj.strftime('%Y-%m-%d'),
                                    'dia': fecha_obj.strftime('%A')[:3],
                                    # Mantenemos las columnas con guiones para que el PDF no se rompa
                                    'entrada': '-', 
                                    'salida': '-',
                                    'horas_fmt': f"{horas:.2f}",
                                    'horas_raw': horas
                                })

            # 4. GENERAR RECIBOS
            count = 0
            
            # --- FIX IMAGEN (Ruta Local) ---
            ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
            logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
            # -------------------------------

            for nombre, registros in datos_empleados.items():
                if not registros: continue

                total_horas = round(sum(r['horas_raw'] for r in registros), 2)
                empleado_obj, _ = Empleado.objects.get_or_create(nombre=nombre)
                
                fechas_dt = [pd.to_datetime(r['fecha']) for r in registros]
                periodo = f"{min(fechas_dt).strftime('%Y-%m-%d')} al {max(fechas_dt).strftime('%Y-%m-%d')}"
                total_pagado = round(total_horas * float(empleado_obj.tarifa_base), 2)

                context = {
                    'empleado': empleado_obj,
                    'periodo': periodo,
                    'lista_asistencia': registros,
                    'total_horas': f"{total_horas:.2f}",
                    'total_pagado': total_pagado,
                    'folio': f"NOM-{ReciboNomina.objects.count()+1:03d}",
                    'logo_url': logo_url 
                }
                
                html = render_to_string('nomina/recibo_nomina.html', context)
                pdf = HTML(string=html).write_pdf()
                
                recibo = ReciboNomina.objects.create(
                    empleado=empleado_obj, periodo=periodo,
                    horas_trabajadas=total_horas, tarifa_aplicada=empleado_obj.tarifa_base,
                    total_pagado=total_pagado
                )
                safe_name = "".join([c for c in nombre if c.isalnum() or c==' ']).strip().replace(' ','_')
                recibo.archivo_pdf.save(f"Nomina_{safe_name}.pdf", ContentFile(pdf))
                count += 1

            if count > 0: messages.success(request, f"✅ Éxito: {count} recibos generados.")
            else: messages.warning(request, "⚠️ No se encontraron datos procesables.")
            
            # --- CORRECCIÓN FINAL ---
            # Redirigir siempre a la LISTA del admin, nunca al formulario vacío
            return redirect('admin:nomina_recibonomina_changelist')

        except Exception as e:
            messages.error(request, f"Error crítico al procesar: {e}")
            return redirect('admin:nomina_recibonomina_changelist')

    # Si entran por GET (URL directa), mostramos el formulario aparte
    return render(request, 'nomina/formulario_carga.html')