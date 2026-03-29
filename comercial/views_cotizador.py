"""
Cotizador Público — comercial/views_cotizador.py
=================================================
Flujo:
  GET  /cotizar/         → Formulario multi-paso
  POST /cotizar/enviar/  → Crea Cliente + Cotización BORRADOR + Portal → JSON
  GET  /cotizar/gracias/ → Página de confirmación (fallback)
"""

import json
import requests
import logging
from datetime import datetime, timedelta

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from decouple import config

from .models import Cliente, Cotizacion, PortalCliente, ConstanteSistema

logger = logging.getLogger(__name__)


def _enviar_whatsapp_negocio(mensaje: str) -> bool:
    """Envía notificación al número del negocio vía WhatsApp Cloud API."""
    wa_token    = config('WA_CLOUD_API_TOKEN', default='')
    wa_phone_id = config('WA_PHONE_NUMBER_ID', default='')
    wa_negocio  = config('WA_NUMERO_NEGOCIO', default='529994457178')

    if not wa_token or not wa_phone_id:
        logger.warning("WA_CLOUD_API_TOKEN o WA_PHONE_NUMBER_ID no configurados.")
        return False

    url     = f"https://graph.facebook.com/v19.0/{wa_phone_id}/messages"
    headers = {"Authorization": f"Bearer {wa_token}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": wa_negocio,
        "type": "text",
        "text": {"preview_url": False, "body": mensaje},
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Error notificación WhatsApp negocio: {e}")
        return False


def cotizador_publico(request):
    """GET: Renderiza el formulario multi-paso."""
    return render(request, 'cotizador/index.html')


@csrf_exempt
@require_http_methods(["POST"])
def cotizador_enviar(request):
    """
    POST JSON: Crea Cliente + Cotización BORRADOR + Portal.
    Retorna {'ok': True, 'portal_url': '...', 'folio': 'COT-001'}
    """
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST.dict()

    nombre    = str(data.get('nombre', '')).strip()
    telefono  = str(data.get('telefono', '')).strip()
    servicio  = str(data.get('servicio', '')).strip()   # EVENTO|PASADIA|HOSPEDAJE
    fecha_str = str(data.get('fecha', '')).strip()
    personas  = str(data.get('personas', '50')).strip()
    notas     = str(data.get('notas', '')).strip()

    # Validaciones
    errores = []
    if not nombre:
        errores.append("El nombre es requerido.")
    tel_digitos = ''.join(filter(str.isdigit, telefono))
    if len(tel_digitos) < 10:
        errores.append("El teléfono debe tener al menos 10 dígitos.")
    if not servicio:
        errores.append("Selecciona un tipo de servicio.")
    if not fecha_str:
        errores.append("La fecha es requerida.")
    if errores:
        return JsonResponse({'ok': False, 'errores': errores}, status=400)

    # Parsear fecha
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            fecha_evento = datetime.strptime(fecha_str, fmt).date()
            break
        except ValueError:
            pass
    else:
        fecha_evento = timezone.now().date() + timedelta(days=30)

    # Número de personas
    try:
        num_personas = max(1, min(int(''.join(filter(str.isdigit, personas)) or '50'), 2000))
    except ValueError:
        num_personas = 50

    # Cliente: reutilizar si existe el teléfono
    cliente = Cliente.objects.filter(telefono=tel_digitos).first()
    if not cliente:
        cliente = Cliente.objects.create(
            nombre=nombre.upper(),
            telefono=tel_digitos,
            origen='Web',
        )
    else:
        if not cliente.nombre or cliente.nombre.startswith('PROSPECTO'):
            cliente.nombre = nombre.upper()
            cliente.save(update_fields=['nombre'])

    # Nombre del evento
    nombres_srv = {'EVENTO': 'Evento Social', 'PASADIA': 'Pasadía', 'HOSPEDAJE': 'Hospedaje'}
    nombre_evento = f"{nombres_srv.get(servicio, servicio)} — {nombre}"
    if notas:
        nombre_evento += f" | {notas[:50]}"

    # Crear Cotización BORRADOR
    cotizacion = Cotizacion(
        cliente=cliente,
        nombre_evento=nombre_evento[:200],
        fecha_evento=fecha_evento,
        num_personas=num_personas,
        estado='BORRADOR',
        clima='normal',
        requiere_factura=False,
        incluye_refrescos=False,
        incluye_cerveza=False,
        incluye_licor_nacional=False,
        incluye_licor_premium=False,
        incluye_cocteleria_basica=False,
        incluye_cocteleria_premium=False,
    )
    cotizacion.save()

    # Crear Portal del Cliente
    portal, _ = PortalCliente.objects.get_or_create(
        cotizacion=cotizacion,
        defaults={'activo': True},
    )

    portal_url = f"https://clientes.quintakooxtanil.com/mi-evento/{portal.token}/"

    # Notificación WhatsApp al negocio
    emojis = {'EVENTO': '🎉', 'PASADIA': '☀️', 'HOSPEDAJE': '🏠'}
    _enviar_whatsapp_negocio(
        f"🔔 *Nueva solicitud web*\n\n"
        f"{emojis.get(servicio,'📋')} *Servicio:* {nombres_srv.get(servicio, servicio)}\n"
        f"👤 *Nombre:* {nombre}\n"
        f"📞 *Teléfono:* {tel_digitos}\n"
        f"📅 *Fecha:* {fecha_evento.strftime('%d/%m/%Y')}\n"
        f"👥 *Personas:* {num_personas}\n"
        f"📝 *Notas:* {notas or 'Sin notas'}\n\n"
        f"🔗 Ver cotización:\n{portal_url}\n\n"
        f"_COT-{cotizacion.id:03d} — ERP QKT_"
    )

    return JsonResponse({
        'ok': True,
        'portal_url': portal_url,
        'cotizacion_id': cotizacion.id,
        'folio': f"COT-{cotizacion.id:03d}",
    })


def cotizador_gracias(request):
    portal_url = request.GET.get('portal', 'https://clientes.quintakooxtanil.com')
    return render(request, 'cotizador/gracias.html', {'portal_url': portal_url})