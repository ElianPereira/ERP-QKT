# ==========================================
# CREAR ARCHIVO: comercial/views_portal.py
# ==========================================
"""
Portal del Cliente — Vistas Públicas
=====================================
Permite al cliente ver su cotización, plan de pagos, contrato
y estado de pagos sin necesidad de login al admin.

Acceso: código de cotización + últimos 4 dígitos del teléfono.
"""
import os
from decimal import Decimal
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, Http404
from django.template.loader import render_to_string
from django.utils import timezone
from django.conf import settings
from weasyprint import HTML

from .models import Cotizacion, PortalCliente, PlanPago


from core_erp.ratelimit import rate_limit as _rate_limit


@_rate_limit(key='portal_acceso', limit=20, window=60)
def portal_acceso(request):
    """
    Página de acceso al portal. El cliente ingresa:
    - Código de cotización (ej: 7 o COT-007)
    - Últimos 4 dígitos de su teléfono
    """
    error = None
    
    if request.method == 'POST':
        codigo = request.POST.get('codigo', '').strip()
        telefono = request.POST.get('telefono', '').strip()
        
        # Limpiar código: aceptar "7", "007", "COT-007", "cot007"
        codigo_limpio = ''.join(filter(str.isdigit, codigo))
        
        if not codigo_limpio or not telefono:
            error = "Ingresa tu código de cotización y los últimos 4 dígitos de tu teléfono."
        elif len(telefono) != 4 or not telefono.isdigit():
            error = "Ingresa exactamente los últimos 4 dígitos de tu teléfono."
        else:
            try:
                cotizacion_id = int(codigo_limpio)
                cotizacion = Cotizacion.objects.select_related('cliente').get(id=cotizacion_id)
                
                # Validar últimos 4 dígitos del teléfono
                tel_cliente = ''.join(filter(str.isdigit, cotizacion.cliente.telefono or ''))
                if tel_cliente[-4:] == telefono:
                    # Buscar o crear portal
                    portal, created = PortalCliente.objects.get_or_create(
                        cotizacion=cotizacion,
                        defaults={'activo': True}
                    )
                    if portal.activo:
                        return redirect('portal_evento', token=portal.token)
                    else:
                        error = "El acceso a este evento está deshabilitado."
                else:
                    error = "Los datos no coinciden. Verifica tu código y teléfono."
            except Cotizacion.DoesNotExist:
                error = "No encontramos una cotización con ese código."
            except (ValueError, IndexError):
                error = "Los datos no coinciden. Verifica tu código y teléfono."
    
    return render(request, 'portal/acceso.html', {'error': error})


def portal_evento(request, token):
    """
    Vista principal del portal — muestra toda la info del evento.
    """
    portal = get_object_or_404(PortalCliente, token=token, activo=True)
    portal.registrar_visita()
    
    cotizacion = portal.cotizacion
    cliente = cotizacion.cliente
    items = cotizacion.items.all()
    pagos = cotizacion.pagos.all().order_by('fecha_pago')
    
    # Plan de pagos
    plan = None
    parcialidades = []
    try:
        plan = cotizacion.plan_pago
        if plan and plan.activo:
            parcialidades = plan.parcialidades.all()
    except PlanPago.DoesNotExist:
        pass
    
    # Contrato
    contrato = None
    try:
        contrato = cotizacion.contratos.filter(archivo__isnull=False).order_by('-generado_en').first()
    except Exception:
        pass
    
    # Historial de comunicaciones
    try:
        from comunicacion.models import ComunicacionCliente
        comunicaciones = ComunicacionCliente.objects.filter(
            cotizacion=cotizacion
        ).order_by('-fecha_envio')[:20]
    except Exception:
        comunicaciones = []

    # Calcular datos
    total_pagado = cotizacion.total_pagado()
    saldo_pendiente = cotizacion.saldo_pendiente()
    porcentaje = cotizacion.porcentaje_pagado
    
    # WhatsApp URL
    wa_numero = '529999999999'  # Cambiar por tu número real
    try:
        from .models import ConstanteSistema
        obj = ConstanteSistema.objects.get(clave='WHATSAPP_NEGOCIO')
        wa_numero = obj.descripcion or wa_numero
    except Exception:
        pass
    
    context = {
        'portal': portal,
        'cotizacion': cotizacion,
        'cliente': cliente,
        'items': items,
        'pagos': pagos,
        'plan': plan,
        'parcialidades': parcialidades,
        'contrato': contrato,
        'total_pagado': total_pagado,
        'saldo_pendiente': saldo_pendiente,
        'porcentaje': porcentaje,
        'wa_numero': wa_numero,
        'comunicaciones': comunicaciones,
    }
    
    return render(request, 'portal/evento.html', context)


def portal_descargar_cotizacion(request, token):
    """Descarga PDF de cotización desde el portal."""
    portal = get_object_or_404(PortalCliente, token=token, activo=True)
    cotizacion = portal.cotizacion
    
    from .views import obtener_contexto_cotizacion
    context = obtener_contexto_cotizacion(cotizacion)
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)
    
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Cotizacion_COT-{cotizacion.id:03d}.pdf"'
    HTML(string=html_string).write_pdf(response)
    return response

def portal_descargar_plan(request, token):
    """Descarga PDF del plan de pagos desde el portal."""
    portal = get_object_or_404(PortalCliente, token=token, activo=True)
    cotizacion = portal.cotizacion
    
    try:
        plan = cotizacion.plan_pago
    except PlanPago.DoesNotExist:
        raise Http404("No hay plan de pagos disponible.")
    
    ruta_logo = os.path.join(settings.BASE_DIR, 'static', 'img', 'logo.png')
    logo_url = f"file:///{ruta_logo.replace(os.sep, '/')}" if os.name == 'nt' else f"file://{ruta_logo}"
    
    context = {
        'cotizacion': cotizacion,
        'plan': plan,
        'parcialidades': plan.parcialidades.all(),
        'logo_url': logo_url,
        'fecha_generacion': timezone.now(),
    }
    
    html_string = render_to_string('cotizaciones/pdf_plan_pagos.html', context)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Plan_Pagos_COT-{cotizacion.id:03d}.pdf"'
    HTML(string=html_string).write_pdf(response)
    return response


def portal_descargar_contrato(request, token):
    """Descarga el contrato PDF desde el portal."""
    portal = get_object_or_404(PortalCliente, token=token, activo=True)
    cotizacion = portal.cotizacion
    
    contrato = cotizacion.contratos.filter(archivo__isnull=False).order_by('-generado_en').first()
    if not contrato or not contrato.archivo:
        raise Http404("No hay contrato disponible.")
    
    return redirect(contrato.archivo.url)