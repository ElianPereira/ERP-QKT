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
import json
import os
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_POST
from weasyprint import HTML

from core_erp.ratelimit import (
    limpiar_portal_acceso,
    portal_acceso_bloqueado,
    registrar_portal_acceso_fallido,
)
from core_erp.ratelimit import (
    rate_limit as _rate_limit,
)

from .models import (
    Cotizacion,
    EspacioLanding,
    GuiaTipoServicio,
    ImagenLanding,
    PlanPago,
    PortalCliente,
    PreguntaFrecuente,
    TestimonioLanding,
)
from .paynet import TIENDAS_PAYNET


def landing_publico(request):
    """
    Landing pública — quintakooxtanil.com
    erp.* redirige al admin; clientes.* redirige al dominio principal.
    """
    host = request.get_host().split(':')[0].lower()
    if host.startswith('erp.'):
        return redirect('/admin/')

    imagenes = ImagenLanding.objects.filter(activo=True)
    img = {}
    for sec in ('HERO', 'NOSOTROS', 'EVENTO', 'PASADIA', 'HOSPEDAJE'):
        img[sec.lower()] = imagenes.filter(seccion=sec).first()
    galeria_imgs = imagenes.filter(Q(seccion='GALERIA') | Q(mostrar_en_galeria=True))
    img['galeria'] = galeria_imgs

    cats_con_fotos = set(galeria_imgs.values_list('categoria_galeria', flat=True))
    galeria_categorias = [
        (c, d) for c, d in ImagenLanding.CATEGORIA_GALERIA_CHOICES
        if c in cats_con_fotos
    ]

    testimonios = TestimonioLanding.objects.filter(activo=True)
    espacios = EspacioLanding.objects.filter(activo=True)
    preguntas = PreguntaFrecuente.objects.filter(activo=True)

    context = {
        'img': img,
        'galeria_categorias': galeria_categorias,
        'testimonios': testimonios,
        'espacios': espacios,
        'preguntas': preguntas,
    }
    return render(request, 'landing/index.html', context)


def _portal_vigente_o_404(token):
    """Resuelve un PortalCliente por token, exigiendo activo y sin expirar.

    Un solo 404 para inactivo, expirado o inexistente: no confirma si el
    token existió alguna vez (mismo criterio que ERROR_ACCESO en portal_acceso).
    """
    return get_object_or_404(
        PortalCliente, token=token, activo=True, expira_en__gt=timezone.now()
    )


@_rate_limit(key='portal_acceso', limit=20, window=60)
def portal_acceso(request):
    """
    Página de acceso al portal. El cliente ingresa:
    - Código de cotización (ej: 7 o COT-007)
    - Últimos 4 dígitos de su teléfono
    """
    error = None

    # Un único mensaje para todos los fallos de acceso. Distinguir "no existe
    # esa cotización" de "el teléfono no coincide" permitía enumerar qué
    # códigos son válidos —y el código es el id secuencial— antes de gastar un
    # solo intento adivinando los 4 dígitos.
    ERROR_ACCESO = "Los datos no coinciden. Verifica tu código y los últimos 4 dígitos de tu teléfono."

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
            cotizacion_id = int(codigo_limpio)

            # Bucket por cotización, no por IP: reparte los intentos entre IPs
            # distintas y sigue topando aquí, porque el objetivo (los 4
            # dígitos de un mismo cliente) no cambia con la IP.
            if portal_acceso_bloqueado(cotizacion_id):
                return HttpResponse(
                    'Demasiados intentos. Espera unos minutos antes de volver a intentarlo.',
                    status=429,
                    headers={'Retry-After': str(settings.PORTAL_ACCESO_VENTANA)},
                )

            try:
                cotizacion = Cotizacion.objects.select_related('cliente').get(id=cotizacion_id)

                # Validar últimos 4 dígitos del teléfono
                tel_cliente = ''.join(filter(str.isdigit, cotizacion.cliente.telefono or ''))
                portal = getattr(cotizacion, 'portal', None)
                if tel_cliente[-4:] == telefono and portal is not None and portal.vigente:
                    limpiar_portal_acceso(cotizacion_id)
                    return redirect('portal_evento', token=portal.token)
                else:
                    # Ya sea el teléfono, un portal inexistente/inactivo o uno
                    # expirado: mismo error y mismo contador, para no revelar
                    # cuál de los tres fue. El alta del portal ocurre en el
                    # flujo comercial (ItemCotizacion.save), no aquí.
                    registrar_portal_acceso_fallido(cotizacion_id)
                    error = ERROR_ACCESO
            except Cotizacion.DoesNotExist:
                registrar_portal_acceso_fallido(cotizacion_id)
                error = ERROR_ACCESO

    from comunicacion.services import normalizar_telefono_wa
    return render(request, 'portal/acceso.html', {
        'error': error,
        'wa_numero': normalizar_telefono_wa(getattr(settings, 'WA_NUMERO_CONTACTO_PUBLICO', '')),
    })


@_rate_limit(key='portal_evento', limit=10, window=60)
def portal_evento(request, token):
    """
    Vista principal del portal — muestra toda la info del evento.
    """
    portal = _portal_vigente_o_404(token)
    portal.registrar_visita()

    cotizacion = portal.cotizacion
    cliente = cotizacion.cliente
    items = cotizacion.items.all()
    pagos = cotizacion.pagos.all().order_by('fecha_pago').select_related('transaccion_openpay')

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
    monto_minimo, monto_minimo_motivo = cotizacion.monto_minimo_pago_detalle()

    # Número público de contacto para los enlaces wa.me que ve el cliente.
    # Es el de atención, distinto del emisor de la Cloud API y distinto del
    # WA_NUMERO_NEGOCIO al que van las alertas internas. Si no está configurado
    # la plantilla oculta el enlace en vez de renderizar un wa.me/ roto.
    from comunicacion.services import normalizar_telefono_wa
    wa_numero = normalizar_telefono_wa(getattr(settings, 'WA_NUMERO_CONTACTO_PUBLICO', ''))

    from .services_openpay import transacciones_pendientes
    pagos_pendientes_openpay = transacciones_pendientes(cotizacion) if saldo_pendiente > 0 else []

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
        # Checkout Openpay (solo si hay credenciales configuradas)
        'openpay_habilitado': bool(settings.OPENPAY_MERCHANT_ID and settings.OPENPAY_PUBLIC_KEY),
        'openpay_merchant_id': settings.OPENPAY_MERCHANT_ID,
        'openpay_public_key': settings.OPENPAY_PUBLIC_KEY,
        'openpay_sandbox': settings.OPENPAY_MODE == 'sandbox',
        'monto_minimo_pago': monto_minimo,
        'monto_minimo_pago_motivo': monto_minimo_motivo,
        'identificacion_completa': cotizacion.identificacion_completa(),
        # Catálogo de cadenas Paynet como JSON: el portal y la ficha PDF leen
        # la misma lista, así no se desincronizan.
        'tiendas_paynet': json.dumps([list(t) for t in TIENDAS_PAYNET]),
        # Referencias de efectivo/SPEI de un intento anterior, aún vigentes:
        # se muestran de entrada para que el cliente las reintente en vez de
        # generar otra sin saber que ya tenía una pendiente.
        'pagos_pendientes_openpay': pagos_pendientes_openpay,
    }

    return render(request, 'portal/evento.html', context)


@_rate_limit(key='portal_descargar_cotizacion', limit=10, window=60)
def portal_descargar_cotizacion(request, token):
    """Descarga PDF de cotización desde el portal."""
    portal = _portal_vigente_o_404(token)
    cotizacion = portal.cotizacion

    from .views import obtener_contexto_cotizacion
    context = obtener_contexto_cotizacion(cotizacion)
    html_string = render_to_string('cotizaciones/pdf_recibo.html', context)

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Cotizacion_COT-{cotizacion.id:03d}.pdf"'
    HTML(string=html_string).write_pdf(response)
    return response

@_rate_limit(key='portal_descargar_plan', limit=10, window=60)
def portal_descargar_plan(request, token):
    """Descarga PDF del plan de pagos desde el portal."""
    portal = _portal_vigente_o_404(token)
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


@_rate_limit(key='portal_descargar_contrato', limit=10, window=60)
def portal_descargar_contrato(request, token):
    """Sirve el contrato PDF desde el portal, sin revelar la URL del storage.

    Antes se redirigía a `contrato.archivo.url`, que en R2 es una URL pública y
    permanente: quedaba en el historial del cliente y podía compartirse —o
    filtrarse por Referer— dando acceso al contrato a cualquiera. Ahora el
    contenido pasa por aquí, donde el token del portal sigue siendo el control
    de acceso, y la respuesta se marca como no cacheable.
    """
    portal = _portal_vigente_o_404(token)
    cotizacion = portal.cotizacion

    contrato = cotizacion.contratos.filter(archivo__isnull=False).order_by('-generado_en').first()
    if not contrato or not contrato.archivo:
        raise Http404("No hay contrato disponible.")

    try:
        archivo = contrato.archivo.open('rb')
    except (FileNotFoundError, OSError):
        # Heredado de Cloudinary y dado por perdido (la cuenta quedó
        # deshabilitada; ver Memoria en CLAUDE.md).
        raise Http404("El contrato no está disponible en este momento.") from None

    respuesta = FileResponse(
        archivo,
        as_attachment=False,
        filename=f'Contrato_COT-{cotizacion.id:03d}.pdf',
        content_type='application/pdf',
    )
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta


@_rate_limit(key='portal_descargar_guia', limit=10, window=60)
def portal_descargar_guia(request, token):
    """Sirve el PDF de la guía informativa del tipo de servicio de la cotización.

    Es el enlace que manda el WhatsApp de `notificar_guia_evento` (Issue #234)
    — el email adjunta el PDF directamente, esta vista es solo para el canal
    que no puede llevar el archivo adjunto sin una plantilla de Meta más lenta
    de aprobar.
    """
    portal = _portal_vigente_o_404(token)
    cotizacion = portal.cotizacion

    guia = GuiaTipoServicio.objects.filter(tipo_servicio=cotizacion.tipo_servicio).first()
    if not guia or not guia.archivo_pdf:
        raise Http404("La guía no está disponible en este momento.")

    try:
        archivo = guia.archivo_pdf.open('rb')
    except (FileNotFoundError, OSError):
        raise Http404("La guía no está disponible en este momento.") from None

    respuesta = FileResponse(
        archivo,
        as_attachment=False,
        filename=f'Guia_{cotizacion.get_tipo_servicio_display()}.pdf',
        content_type='application/pdf',
    )
    respuesta['Cache-Control'] = 'private, no-store'
    return respuesta


# Mismo criterio de tamaño/formato para una identificación oficial escaneada
# o fotografiada con el celular — suficiente para una foto de buena calidad
# sin abrir la puerta a archivos arbitrarios.
IDENTIFICACION_TIPOS_PERMITIDOS = {'image/jpeg', 'image/png', 'application/pdf'}
IDENTIFICACION_TAMANO_MAXIMO = 8 * 1024 * 1024  # 8 MB


@_rate_limit(key='portal_subir_identificacion', limit=10, window=60)
@require_POST
def portal_subir_identificacion(request, token):
    """
    Sube la identificación oficial (INE) de quien contrata. Se guarda en
    storage privado (ver Cotizacion.identificacion_oficial) — el gate real de
    "obligatoria antes de pagar" vive en
    views_openpay.portal_procesar_pago_openpay, esta vista solo la recibe.
    """
    portal = _portal_vigente_o_404(token)
    cotizacion = portal.cotizacion

    archivo = request.FILES.get('identificacion')
    if not archivo:
        return JsonResponse({'ok': False, 'mensaje': 'Selecciona un archivo.'}, status=400)

    if archivo.content_type not in IDENTIFICACION_TIPOS_PERMITIDOS:
        return JsonResponse({
            'ok': False,
            'mensaje': 'Formato no válido. Sube una foto (JPG/PNG) o un PDF de tu identificación.',
        }, status=400)

    if archivo.size > IDENTIFICACION_TAMANO_MAXIMO:
        return JsonResponse({'ok': False, 'mensaje': 'El archivo pesa demasiado (máximo 8 MB).'}, status=400)

    cotizacion.identificacion_oficial = archivo
    cotizacion.save(update_fields=['identificacion_oficial'])

    return JsonResponse({'ok': True, 'mensaje': 'Identificación recibida.'})
