"""
Cotizador Público — comercial/views_cotizador.py
=================================================
Replica el flujo del webhook de ManyChat:
- Crea Cliente (reutiliza si ya existe por teléfono)
- Crea Cotización BORRADOR con items reales del catálogo
- Crea PortalCliente automáticamente
- Envía notificación WhatsApp al negocio
- Retorna URL del portal para redirigir al cliente

Rutas:
  GET  /cotizar/         → Formulario multi-paso
  POST /cotizar/enviar/  → Procesa y crea en ERP → JSON
  GET  /cotizar/gracias/ → Fallback de confirmación
"""

import json
import math
import requests
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from decouple import config

from .models import (
    Cliente, Cotizacion, ItemCotizacion, Producto, PortalCliente
)

logger = logging.getLogger(__name__)


# ─── Helpers (reutilizados del webhook) ───────────────────────────────────────

def _buscar_producto_por_nombre(nombre_parcial):
    return Producto.objects.filter(nombre__icontains=nombre_parcial).first()


def _agregar_item(cotizacion, producto, cantidad=1, desc_override=None):
    if not producto:
        return None
    precio = producto.sugerencia_precio()
    return ItemCotizacion.objects.create(
        cotizacion=cotizacion,
        producto=producto,
        descripcion=desc_override or producto.nombre,
        cantidad=Decimal(str(cantidad)),
        precio_unitario=Decimal(str(precio)),
    )


def _detectar_clima(fecha):
    if not fecha:
        return 'calor'
    m = fecha.month
    if m == 5:
        return 'extremo'
    elif m in (3, 4, 6, 7, 8, 9, 10):
        return 'calor'
    return 'normal'


def _redondear_personas(n, es_pasadia=False):
    if es_pasadia:
        return min(int(n), 20)
    return max(20, math.ceil(int(n) / 10) * 10)


def _enviar_wa_negocio(mensaje: str) -> bool:
    wa_token    = config('WA_CLOUD_API_TOKEN', default='')
    wa_phone_id = config('WA_PHONE_NUMBER_ID', default='')
    wa_negocio  = config('WA_NUMERO_NEGOCIO', default='529994457178')
    if not wa_token or not wa_phone_id:
        return False
    try:
        resp = requests.post(
            f"https://graph.facebook.com/v19.0/{wa_phone_id}/messages",
            headers={"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": wa_negocio,
                  "type": "text", "text": {"preview_url": False, "body": mensaje}},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Error WA negocio: {e}")
        return False


# ─── Vistas ───────────────────────────────────────────────────────────────────

def cotizador_publico(request):
    return render(request, 'cotizador/index.html')


@csrf_exempt
@require_http_methods(["POST"])
def cotizador_enviar(request):
    """
    Procesa la solicitud del cotizador web.
    Replica exactamente la lógica del webhook de ManyChat.
    """
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST.dict()

    # ── Datos base ─────────────────────────────────────────────
    nombre    = str(data.get('nombre', '')).strip()
    telefono  = str(data.get('telefono', '')).strip()
    servicio  = str(data.get('servicio', '')).strip()      # EVENTO|PASADIA|HOSPEDAJE
    fecha_str = str(data.get('fecha', '')).strip()
    personas  = str(data.get('personas', '50')).strip()
    hora_ini  = str(data.get('hora_inicio', '')).strip()
    hora_fin  = str(data.get('hora_fin', '')).strip()
    tipo_ev   = str(data.get('tipo_evento', 'Evento General')).strip()
    notas     = str(data.get('notas', '')).strip()

    # Extras evento
    inc_cerveza    = bool(data.get('inc_cerveza', False))
    inc_nacional   = bool(data.get('inc_nacional', False))
    inc_premium    = bool(data.get('inc_premium', False))
    inc_cocteleria = bool(data.get('inc_cocteleria', False))
    inc_mixologia  = bool(data.get('inc_mixologia', False))
    inc_dj_basico  = bool(data.get('inc_dj_basico', False))
    inc_dj_ilum    = bool(data.get('inc_dj_ilum', False))
    inc_catering   = bool(data.get('inc_catering', False))
    inc_taquiza    = bool(data.get('inc_taquiza', False))

    # Extras pasadía
    inc_brincolin  = bool(data.get('inc_brincolin', False))

    # Validaciones
    tel_d = ''.join(filter(str.isdigit, telefono))
    errores = []
    if not nombre:      errores.append("El nombre es requerido.")
    if len(tel_d) < 10: errores.append("El teléfono debe tener al menos 10 dígitos.")
    if not servicio:    errores.append("Selecciona un tipo de servicio.")
    if not fecha_str:   errores.append("La fecha es requerida.")
    if errores:
        return JsonResponse({'ok': False, 'errores': errores}, status=400)

    # ── Parsear fecha ──────────────────────────────────────────
    fecha_evento = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            fecha_evento = datetime.strptime(fecha_str, fmt).date()
            break
        except ValueError:
            pass
    if not fecha_evento:
        fecha_evento = timezone.now().date() + timedelta(days=30)

    # ── Horas ──────────────────────────────────────────────────
    def _parsear_hora(s):
        if not s:
            return None
        for fmt in ("%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(s.strip(), fmt).time()
            except ValueError:
                pass
        return None

    hora_inicio_obj = _parsear_hora(hora_ini)
    hora_fin_obj    = _parsear_hora(hora_fin)

    if servicio == 'PASADIA':
        hora_inicio_obj = datetime.strptime("10:00", "%H:%M").time()
        hora_fin_obj    = datetime.strptime("19:00", "%H:%M").time()
        horas_evento    = 9
    elif hora_inicio_obj and hora_fin_obj:
        from datetime import date as dt_date, datetime as dt
        dt_i = dt.combine(dt_date.today(), hora_inicio_obj)
        dt_f = dt.combine(dt_date.today(), hora_fin_obj)
        if dt_f <= dt_i:
            dt_f += timedelta(days=1)
        horas_evento = max(6, int((dt_f - dt_i).total_seconds() / 3600))
    else:
        horas_evento = 6

    # ── Número de personas ─────────────────────────────────────
    try:
        num_raw = max(1, min(int(''.join(filter(str.isdigit, personas)) or '50'), 2000))
    except ValueError:
        num_raw = 50
    num_personas = _redondear_personas(num_raw, servicio == 'PASADIA')

    # ── Disponibilidad de fecha ────────────────────────────────
    aviso_fecha = None
    try:
        from airbnb.validacion_fechas import verificar_disponibilidad_fecha
        disponible, msg_disp = verificar_disponibilidad_fecha(fecha_evento)
        if not disponible:
            aviso_fecha = msg_disp
    except Exception:
        pass

    # ── Cliente ────────────────────────────────────────────────
    from .services import get_or_create_cliente_desde_canal
    cliente, _ = get_or_create_cliente_desde_canal(
        telefono_raw=tel_d,
        nombre_raw=nombre,
        origen='Web',
    )

    # ── Nombre del evento ──────────────────────────────────────
    nombres_srv = {'EVENTO': 'Evento Social', 'PASADIA': 'Pasadía', 'HOSPEDAJE': 'Hospedaje'}
    if servicio == 'EVENTO':
        nombre_evento = f"{tipo_ev} — {nombre}"
    elif servicio == 'PASADIA':
        nombre_evento = f"Pasadía — {nombre}"
    else:
        nombre_evento = f"Hospedaje — {nombre}"
    if notas:
        nombre_evento += f" | {notas[:60]}"

    # ── Crear Cotización ───────────────────────────────────────
    inc_refrescos = any([inc_cerveza, inc_nacional, inc_premium, inc_cocteleria, inc_mixologia])
    clima = _detectar_clima(fecha_evento)

    cotizacion = Cotizacion(
        cliente=cliente,
        nombre_evento=nombre_evento[:200],
        fecha_evento=fecha_evento,
        num_personas=num_personas,
        horas_servicio=horas_evento,
        hora_inicio=hora_inicio_obj,
        hora_fin=hora_fin_obj,
        estado='BORRADOR',
        clima=clima,
        requiere_factura=True,
        incluye_refrescos=inc_refrescos,
        incluye_cerveza=inc_cerveza,
        incluye_licor_nacional=inc_nacional,
        incluye_licor_premium=inc_premium,
        incluye_cocteleria_basica=inc_cocteleria,
        incluye_cocteleria_premium=inc_mixologia,
    )
    cotizacion.save()

    resumen_partes = []

    # ── Items según servicio ───────────────────────────────────
    if servicio == 'EVENTO':
        prod = _buscar_producto_por_nombre('Paquete Esencial')
        if prod:
            _agregar_item(cotizacion, prod, 1,
                f"Paquete Esencial QKT — {tipo_ev} ({num_personas} Pax, {horas_evento}hrs)")
            resumen_partes.append("Paquete Esencial")

        if horas_evento > 6:
            horas_extra = horas_evento - 6
            prod = (_buscar_producto_por_nombre('Hora Extra De Arrendamiento')
                    or _buscar_producto_por_nombre('Hora Extra'))
            if prod:
                _agregar_item(cotizacion, prod, horas_extra,
                    f"Horas Extra de Arrendamiento ({horas_extra} hrs adicionales)")
                resumen_partes.append(f"+{horas_extra}hrs")

        if inc_dj_ilum:
            prod = (_buscar_producto_por_nombre('DJ Con Iluminación')
                    or _buscar_producto_por_nombre('DJ Iluminacion'))
            if prod:
                _agregar_item(cotizacion, prod, 1, f"DJ con Iluminación — {horas_evento} Hrs")
                resumen_partes.append("DJ+Ilum")
        elif inc_dj_basico:
            prod = (_buscar_producto_por_nombre('Básico De DJ')
                    or _buscar_producto_por_nombre('DJ Basico'))
            if prod:
                _agregar_item(cotizacion, prod, 1, f"DJ Básico — {horas_evento} Hrs")
                resumen_partes.append("DJ")

        if inc_catering:
            prod = _buscar_producto_por_nombre('Catering')
            if prod:
                mult = math.ceil(num_personas / 10)
                _agregar_item(cotizacion, prod, mult,
                    f"Servicio de Catering ({num_personas} Pax)")
                resumen_partes.append("Catering")

        if inc_taquiza:
            prod = _buscar_producto_por_nombre('Taquiza')
            if prod:
                mult = math.ceil(num_personas / 10)
                _agregar_item(cotizacion, prod, mult,
                    f"Servicio de Taquiza ({num_personas} Pax)")
                resumen_partes.append("Taquiza")

        barra = []
        if inc_cerveza:    barra.append("Cerveza")
        if inc_nacional:   barra.append("Nacional")
        if inc_premium:    barra.append("Premium")
        if inc_cocteleria: barra.append("Coctelería")
        if inc_mixologia:  barra.append("Mixología")
        if barra:
            resumen_partes.append("Barra(" + "/".join(barra) + ")")

    elif servicio == 'PASADIA':
        prod = (_buscar_producto_por_nombre('Pasadía')
                or _buscar_producto_por_nombre('Pasadia'))
        if prod:
            _agregar_item(cotizacion, prod, 1,
                f"Paquete Pasadía QKT ({num_personas} Pax, 10am-7pm)")
            resumen_partes.append("Pasadía")

        if inc_brincolin:
            prod = (_buscar_producto_por_nombre('Brincolín')
                    or _buscar_producto_por_nombre('Brincolin'))
            if prod:
                _agregar_item(cotizacion, prod, 1, "Brincolín")
                resumen_partes.append("Brincolín")

    elif servicio == 'HOSPEDAJE':
        resumen_partes.append("Hospedaje")

    # ── Portal del cliente ─────────────────────────────────────
    portal, _ = PortalCliente.objects.get_or_create(
        cotizacion=cotizacion,
        defaults={'activo': True},
    )
    portal_url = f"https://clientes.quintakooxtanil.com/mi-evento/{portal.token}/"

    # ── Notificación WA al negocio ─────────────────────────────
    emoji = {'EVENTO': '🎉', 'PASADIA': '☀️', 'HOSPEDAJE': '🏠'}.get(servicio, '📋')
    resumen_txt = ", ".join(resumen_partes) if resumen_partes else "Sin servicios adicionales"
    _enviar_wa_negocio(
        f"🔔 *Nueva solicitud web*\n\n"
        f"{emoji} *Servicio:* {nombres_srv.get(servicio, servicio)}\n"
        f"👤 *Nombre:* {nombre}\n"
        f"📞 *Teléfono:* {tel_d}\n"
        f"📅 *Fecha:* {fecha_evento.strftime('%d/%m/%Y')}\n"
        f"👥 *Personas:* {num_personas}\n"
        f"🕐 *Horario:* {hora_ini or '—'} a {hora_fin or '—'}\n"
        f"📋 *Servicios:* {resumen_txt}\n"
        f"📝 *Notas:* {notas or 'Sin notas'}\n\n"
        f"🔗 Ver cotización:\n{portal_url}\n\n"
        f"_COT-{cotizacion.id:03d} — ERP QKT_"
    )

    if aviso_fecha:
        try:
            from comunicacion.services import alertar_equipo_fecha_chocada
            alertar_equipo_fecha_chocada(cotizacion, aviso_fecha)
        except Exception:
            pass

    return JsonResponse({
        'ok': True,
        'portal_url': portal_url,
        'cotizacion_id': cotizacion.id,
        'folio': f"COT-{cotizacion.id:03d}",
        'aviso_fecha': aviso_fecha,
    })


def api_disponibilidad_fecha(request):
    """GET /api/disponibilidad/?fecha=YYYY-MM-DD
    Responde si la fecha está libre o ya apartada (Airbnb / cotización confirmada)."""
    fecha_str = (request.GET.get('fecha') or '').strip()
    fecha = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            fecha = datetime.strptime(fecha_str, fmt).date()
            break
        except ValueError:
            pass
    if not fecha:
        return JsonResponse({'ok': False, 'error': 'Fecha inválida'}, status=400)
    try:
        from airbnb.validacion_fechas import verificar_disponibilidad_fecha
        disponible, mensaje = verificar_disponibilidad_fecha(fecha)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)
    return JsonResponse({
        'ok': True,
        'fecha': fecha.strftime('%Y-%m-%d'),
        'disponible': disponible,
        'mensaje': mensaje or 'Fecha disponible',
    })


def api_fechas_ocupadas(request):
    """GET /api/fechas-ocupadas/?dias=365
    Devuelve la lista de fechas no disponibles (Airbnb + cotizaciones apartadas)
    en el rango [hoy, hoy+dias] para pintar un calendario."""
    try:
        dias = int(request.GET.get('dias', '365'))
    except ValueError:
        dias = 365
    dias = max(1, min(dias, 730))
    hoy = timezone.now().date()
    fin = hoy + timedelta(days=dias)
    try:
        from airbnb.validacion_fechas import obtener_fechas_bloqueadas
        bloqueos = obtener_fechas_bloqueadas(hoy, fin)
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)

    fechas = set()
    for b in bloqueos:
        ini, f_fin = b['fecha_inicio'], b['fecha_fin']
        d = ini
        while d <= f_fin:
            fechas.add(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)
    return JsonResponse({
        'ok': True,
        'desde': hoy.strftime('%Y-%m-%d'),
        'hasta': fin.strftime('%Y-%m-%d'),
        'fechas_ocupadas': sorted(fechas),
    })


def cotizador_gracias(request):
    portal_url = request.GET.get('portal', 'https://clientes.quintakooxtanil.com')
    return render(request, 'cotizador/gracias.html', {'portal_url': portal_url})