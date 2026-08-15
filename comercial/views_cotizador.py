"""
Cotizador Público — comercial/views_cotizador.py
=================================================
Flujo:
- Crea Cliente (reutiliza si ya existe por teléfono)
- Crea Cotización BORRADOR con items reales del catálogo
- Crea PortalCliente automáticamente
- Alerta al negocio y notifica al cliente (email + WhatsApp) vía
  comunicacion.services_notificaciones
- Retorna URL del portal para redirigir al cliente

Rutas:
  GET  /cotizar/         → Formulario multi-paso
  POST /cotizar/enviar/  → Procesa y crea en ERP → JSON
  GET  /cotizar/gracias/ → Fallback de confirmación
"""

import json
import math
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from core_erp import impuestos

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.conf import settings

from core_erp.ratelimit import rate_limit

from .forms_cotizador import CotizadorEnviarForm
from .models import (
    Cliente, Cotizacion, ItemCotizacion, Producto, PortalCliente
)

logger = logging.getLogger(__name__)


# ─── Helpers (reutilizados del webhook) ─────────────────────────────────────────────────────

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


# ─── Vistas ──────────────────────────────────────────────────────────────────────────────

def cotizador_publico(request):
    return render(request, 'cotizador/index.html')


# Endpoint público con sesión anónima (CSRF vía cookie, no login): se limita
# por IP para frenar spam/abuso, ya que cada envío crea Cliente + Cotización
# y dispara una notificación de WhatsApp.
@rate_limit(key='cotizador_enviar', limit=10, window=60)
@require_http_methods(["POST"])
def cotizador_enviar(request):
    """
    Procesa la solicitud del cotizador web.
    """
    try:
        data = json.loads(request.body)
    except Exception:
        data = request.POST.dict()

    form = CotizadorEnviarForm(data)
    if not form.is_valid():
        errores = [mensaje for lista in form.errors.values() for mensaje in lista]
        return JsonResponse({'ok': False, 'errores': errores}, status=400)
    limpio = form.cleaned_data

    # ── Datos base ────────────────────────────────────────────────────────────
    nombre    = limpio['nombre'].strip()
    telefono  = limpio['telefono'].strip()
    email     = limpio['email'].strip()
    servicio  = limpio['servicio'].strip()      # EVENTO|PASADIA|ARRENDAMIENTO
    fecha_str = limpio['fecha'].strip()
    personas  = limpio['personas'].strip()
    hora_ini  = limpio['hora_inicio'].strip()
    hora_fin  = limpio['hora_fin'].strip()
    tipo_ev   = limpio['tipo_evento'].strip() or 'Evento General'
    notas              = limpio['notas'].strip()
    como_nos_encontro  = limpio['como_nos_encontro'].strip()

    # Barra (siguen como booleanos — alimentan CalculadoraBarraService). No
    # forman parte del form: son flags internos del formulario, no texto
    # libre que alimente ningún campo mostrado o buscable.
    inc_refrescos  = bool(data.get('inc_refrescos', False))
    inc_cerveza    = bool(data.get('inc_cerveza', False))
    inc_nacional   = bool(data.get('inc_nacional', False))
    inc_premium    = bool(data.get('inc_premium', False))
    inc_cocteleria = bool(data.get('inc_cocteleria', False))
    inc_mixologia  = bool(data.get('inc_mixologia', False))

    # Extras dinámicos (IDs de Producto con visible_cotizador=True)
    extras_ids_raw = data.get('extras_ids', [])

    # Consentimiento (art. 8 LFPDPPP): el aviso y los términos ya se validaron
    # como obligatorios en CotizadorEnviarForm.clean(); las finalidades
    # secundarias son opcionales y se registran por separado.
    finalidades_opt = [
        str(c).strip().upper()
        for c in (data.get('finalidades') or [])
        if str(c).strip()
    ]

    # Datos fiscales (opcionales)
    req_factura    = bool(limpio['requiere_factura'])
    rfc_raw        = limpio['rfc'].strip().upper()
    razon_social   = limpio['razon_social'].strip()
    cp_fiscal      = limpio['cp_fiscal'].strip()

    tel_d = ''.join(filter(str.isdigit, telefono))

    # ── Parsear fecha ──────────────────────────────────────────────────────────
    fecha_evento = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            fecha_evento = datetime.strptime(fecha_str, fmt).date()
            break
        except ValueError:
            pass
    if not fecha_evento:
        fecha_evento = timezone.now().date() + timedelta(days=30)

    # ── Horas ─────────────────────────────────────────────────────────────────────
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

    # ── Número de personas ──────────────────────────────────────────────────────────────
    try:
        num_raw = max(1, min(int(''.join(filter(str.isdigit, personas)) or '50'), 200))
    except ValueError:
        num_raw = 50
    num_personas = _redondear_personas(num_raw, servicio == 'PASADIA')

    # ── Disponibilidad de fecha ────────────────────────────────────────────────────────────
    aviso_fecha = None
    try:
        from airbnb.validacion_fechas import verificar_disponibilidad_fecha
        disponible, msg_disp = verificar_disponibilidad_fecha(fecha_evento)
        if not disponible:
            aviso_fecha = msg_disp
    except Exception:
        pass

    # ── Cliente ────────────────────────────────────────────────────────────────────
    from .services import get_or_create_cliente_desde_canal
    cliente, _ = get_or_create_cliente_desde_canal(
        telefono_raw=tel_d,
        nombre_raw=nombre,
        origen='Web',
        email_raw=email,
    )

    # ── Evidencia de consentimiento ────────────────────────────────────────────────
    # Se registra en cuanto existe el cliente. Si el registro falla (por ejemplo,
    # porque falta sembrar algún documento vigente) NO se pierde la solicitud: se
    # deja el error en el log y el lead sigue su curso.
    try:
        from legal.models import OrigenAceptacion
        from legal.services import LegalService
        LegalService.registrar_aceptacion(
            request=request,
            correo=email,
            origen=OrigenAceptacion.FORM_COTIZACION,
            cliente=cliente,
            finalidades_aceptadas=finalidades_opt,
        )
    except Exception:
        logger.exception(
            "No se pudo registrar la aceptación legal del cliente %s.", cliente.pk
        )

    if req_factura and rfc_raw:
        cliente.es_cliente_fiscal = True
        cliente.rfc = rfc_raw[:13]
        if razon_social:
            cliente.razon_social = razon_social[:200]
        if cp_fiscal:
            cliente.codigo_postal_fiscal = cp_fiscal[:5]
        cliente.save(update_fields=['es_cliente_fiscal', 'rfc', 'razon_social', 'codigo_postal_fiscal'])

    # ── Nombre del evento ──────────────────────────────────────────────────────────────
    if servicio == 'EVENTO':
        nombre_evento = f"{tipo_ev} — {nombre}"
    elif servicio == 'PASADIA':
        nombre_evento = f"Pastadía — {nombre}"
    else:
        nombre_evento = f"Arrendamiento de Mobiliario — {nombre}"
    if notas:
        nombre_evento += f" | {notas[:60]}"
    if como_nos_encontro:
        nombre_evento += f" [{como_nos_encontro}]"

    # ── Crear Cotización ──────────────────────────────────────────────────────────────
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


    # ── Paquete seleccionado (si aplica) ─────────────────────────────────────────────────────────
    # La validación real del paquete la hace _lineas_cotizador(); aquí solo se
    # pasa el id.
    paquete_id = data.get('paquete_id')

    # ── Items según servicio ──────────────────────────────────────────────────────────────
    # La composición de las líneas vive en _lineas_cotizador(), compartida con
    # api_total_cotizador(): así el total que se le exhibió al cliente antes de
    # enviar es exactamente el de la cotización que se crea aquí.
    extras_ids = [int(x) for x in extras_ids_raw if str(x).isdigit()]
    lineas = _lineas_cotizador(
        servicio=servicio,
        paquete_id=paquete_id,
        extras_ids=extras_ids,
        num_personas=num_personas,
        horas_evento=horas_evento,
        tipo_ev=tipo_ev,
    )
    for prod, qty, desc in lineas:
        _agregar_item(cotizacion, prod, qty, desc)

    # ── Descuentos automáticos ───────────────────────────────────────────────────────────
    # Tras agregar los items (subtotal ya real), evalúa y aplica los descuentos
    # AUTOMATICO: gana un solo no-acumulable + todos los acumulables.
    descuentos_txt = ""
    try:
        from .services_descuentos import DescuentoService
        aplicados = DescuentoService.aplicar_automaticos(cotizacion, usuario=None)
        if aplicados:
            partes_desc = [f"{a.descuento.nombre} (-${a.monto_aplicado:,.2f})" for a in aplicados]
            descuentos_txt = "; ".join(partes_desc)
            logger.info("Descuentos automáticos en COT-%s: %s", cotizacion.id, descuentos_txt)
    except Exception:
        logger.exception("Error aplicando descuentos automáticos en COT-%s", cotizacion.id)

    # ── Portal del cliente ──────────────────────────────────────────────────────────────
    portal, _ = PortalCliente.objects.get_or_create(
        cotizacion=cotizacion,
        defaults={'activo': True},
    )
    portal_url = portal.get_full_url()

    # ── Notificaciones ──────────────────────────────────────────────────────────────────
    # Se disparan aquí, con la cotización, los items, los descuentos y el portal
    # ya persistidos, para que el total del mensaje sea el definitivo.
    #
    # No se usa transaction.on_commit(): esta vista corre en autocommit (no hay
    # atomic() ni ATOMIC_REQUESTS), así que el callback se ejecutaría igual de
    # inmediato y solo aparentaría una garantía que no existe.
    #
    # Cada canal aísla su propio fallo dentro del servicio: ni Meta ni Brevo
    # pueden impedir que el cliente reciba su URL del portal.
    from comunicacion.services_notificaciones import (
        alertar_equipo_nueva_cotizacion,
        notificar_cotizacion,
    )
    alertar_equipo_nueva_cotizacion(cotizacion)
    notificar_cotizacion(cotizacion, origen='WEB')

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


@rate_limit(key='api_disponibilidad_fecha', limit=60, window=60)
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
    except Exception:
        logger.exception("Error al verificar disponibilidad de la fecha %s.", fecha)
        return JsonResponse({'ok': False, 'error': 'No se pudo verificar la disponibilidad.'}, status=500)
    return JsonResponse({
        'ok': True,
        'fecha': fecha.strftime('%Y-%m-%d'),
        'disponible': disponible,
        'mensaje': mensaje or 'Fecha disponible',
    })


@rate_limit(key='api_fechas_ocupadas', limit=60, window=60)
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
    except Exception:
        logger.exception("Error al obtener las fechas bloqueadas (%s a %s).", hoy, fin)
        return JsonResponse({'ok': False, 'error': 'No se pudieron obtener las fechas ocupadas.'}, status=500)

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


@rate_limit(key='api_productos_cotizador', limit=60, window=60)
def api_productos_cotizador(request):
    """GET /api/cotizador/productos/?servicio=EVENTO|PASADIA|ARRENDAMIENTO
    Devuelve los productos visibles en el cotizador, agrupados por grupo_cotizador."""
    servicio = (request.GET.get('servicio') or '').upper()

    filtro = {'visible_cotizador': True}
    if servicio == 'EVENTO':
        filtro['cotizador_evento'] = True
    elif servicio == 'PASADIA':
        filtro['cotizador_pasadia'] = True
    elif servicio == 'ARRENDAMIENTO':
        filtro['cotizador_arrendamiento'] = True

    # Los paquetes se eligen en su propio paso; no deben aparecer como extras.
    productos = Producto.objects.filter(**filtro).exclude(es_paquete=True).order_by('grupo_cotizador', 'orden_cotizador', 'nombre')

    NOMBRES_GRUPO = dict(Producto.GRUPO_COTIZADOR_CHOICES)
    ICONOS_GRUPO = {
        'PAQUETE': '📦', 'ENTRETENIMIENTO': '🎵', 'COMIDA': '🍽️', 'MOBILIARIO': '🪑',
        'DECORACION': '💐', 'INFANTIL': '🎪', 'OTRO': '✨',
    }

    grupos_dict = {}
    for p in productos:
        clave = p.grupo_cotizador or 'OTRO'
        if clave not in grupos_dict:
            grupos_dict[clave] = {
                'clave': clave,
                'nombre': NOMBRES_GRUPO.get(clave, clave),
                'icono': ICONOS_GRUPO.get(clave, '✨'),
                'productos': [],
            }
        grupos_dict[clave]['productos'].append({
            'id': p.id,
            'nombre': p.nombre,
            'icono': p.icono,
            'descripcion': p.descripcion_corta,
            'grupo_exclusion': p.grupo_exclusion or ('LICORES' if p.nombre in ('Licores Nacionales', 'Licores Premium') else ''),
            'cantidad_por_persona': p.cantidad_por_persona,
            'factor_personas': p.factor_personas,
            'requiere_licor': p.requiere_licor,
            'es_base_licor': p.nombre in ('Licores Nacionales', 'Licores Premium'),
            'requiere_refrescos': p.nombre in ('Licores Nacionales', 'Licores Premium') or p.requiere_licor,
            'es_base_refrescos': p.nombre in ('Refrescos y Mezcladores',),
        })

    return JsonResponse({'ok': True, 'grupos': list(grupos_dict.values())})


def _lineas_cotizador(*, servicio, paquete_id, extras_ids, num_personas, horas_evento,
                      tipo_ev='Evento General'):
    """
    Lista de (producto, cantidad, descripcion) que compone una cotización del
    cotizador público.

    Es la ÚNICA definición de qué se cobra: la usan tanto `cotizador_enviar`
    (que crea los ItemCotizacion reales) como `api_total_cotizador` (que
    exhibe el total al cliente antes de enviar). Si las dos calcularan la
    lista por separado, el total exhibido podría quedar por debajo del
    cobrado, que es justo lo que prohíbe el art. 7 BIS de la LFPC.
    """
    lineas = []

    paquete = None
    if paquete_id and str(paquete_id).isdigit():
        paquete = Producto.objects.filter(
            id=int(paquete_id), es_paquete=True, visible_cotizador=True,
        ).first()

    if paquete:
        lineas.append((paquete, 1,
                       f"{paquete.nombre} ({num_personas} Pax, {horas_evento}hrs)"))
        if servicio == 'EVENTO' and horas_evento > 6:
            extra = horas_evento - 6
            prod = (_buscar_producto_por_nombre('Hora Extra De Arrendamiento')
                    or _buscar_producto_por_nombre('Hora Extra'))
            if prod:
                lineas.append((prod, extra,
                               f"Horas Extra de Arrendamiento ({extra} hrs adicionales)"))

    elif servicio == 'EVENTO':
        prod = _buscar_producto_por_nombre('Paquete Esencial')
        if prod:
            lineas.append((prod, 1,
                           f"Paquete Esencial QKT — {tipo_ev} ({num_personas} Pax, {horas_evento}hrs)"))
        if horas_evento > 6:
            extra = horas_evento - 6
            prod = (_buscar_producto_por_nombre('Hora Extra De Arrendamiento')
                    or _buscar_producto_por_nombre('Hora Extra'))
            if prod:
                lineas.append((prod, extra,
                               f"Horas Extra de Arrendamiento ({extra} hrs adicionales)"))

    elif servicio == 'PASADIA':
        prod = (_buscar_producto_por_nombre('Pastadía')
                or _buscar_producto_por_nombre('Pasadia'))
        if prod:
            lineas.append((prod, 1,
                           f"Paquete Pastadía QKT ({num_personas} Pax, 10am-7pm)"))

    if extras_ids:
        for prod in Producto.objects.filter(id__in=extras_ids, visible_cotizador=True):
            qty = 1
            if prod.cantidad_por_persona and prod.factor_personas > 0:
                qty = math.ceil(num_personas / prod.factor_personas)
            desc = f"{prod.nombre} ({num_personas} Pax)" if prod.cantidad_por_persona else None
            lineas.append((prod, qty, desc))

    return lineas


@rate_limit(key='api_total_cotizador', limit=60, window=60)
def api_total_cotizador(request):
    """GET /api/cotizador/total/?servicio=&paquete=&extras=&personas=&horas=

    Total con IVA de la selección, calculado EN EL SERVIDOR con exactamente las
    mismas líneas que creará `cotizador_enviar` (ver `_lineas_cotizador`).

    El navegador nunca suma importes: si convirtiera cada línea a IVA incluido
    y las sumara, el resultado diferiría en centavos del que produce
    `Cotizacion.calcular_totales()`, que convierte una sola vez sobre el
    subtotal.
    """
    def _entero(clave, defecto):
        try:
            return int(request.GET.get(clave, defecto))
        except (TypeError, ValueError):
            return defecto

    num_personas = max(1, _entero('personas', 50))
    horas_evento = max(1, _entero('horas', 6))
    servicio = (request.GET.get('servicio') or '').upper()
    extras_ids = [int(x) for x in (request.GET.get('extras') or '').split(',')
                  if x.strip().isdigit()]

    lineas = _lineas_cotizador(
        servicio=servicio,
        paquete_id=request.GET.get('paquete'),
        extras_ids=extras_ids,
        num_personas=num_personas,
        horas_evento=horas_evento,
    )
    bases = [Decimal(str(prod.sugerencia_precio())) * Decimal(qty)
             for prod, qty, _ in lineas]

    # Una sola conversión, sobre la suma de las bases (nunca por línea).
    total = impuestos.total_desde_bases(bases)
    return JsonResponse({
        'ok': True,
        'total': str(total),
        'total_formateado': f"${total:,.2f}",
        'leyenda': 'Precios en MXN, IVA incluido',
        'lineas': len(lineas),
    })


@rate_limit(key='api_paquetes_cotizador', limit=60, window=60)
def api_paquetes_cotizador(request):
    """GET /api/cotizador/paquetes/?servicio=EVENTO&personas=100
    Devuelve paquetes (Producto con es_paquete=True) visibles en el cotizador,
    filtrados por servicio y rango de personas."""
    servicio = (request.GET.get('servicio') or '').upper()
    try:
        personas = int(request.GET.get('personas', '50'))
    except ValueError:
        personas = 50

    filtro = {'visible_cotizador': True, 'es_paquete': True}
    if servicio == 'EVENTO':
        filtro['cotizador_evento'] = True
    elif servicio == 'PASADIA':
        filtro['cotizador_pasadia'] = True
    elif servicio == 'ARRENDAMIENTO':
        filtro['cotizador_arrendamiento'] = True

    paquetes = Producto.objects.filter(**filtro).order_by('orden_cotizador', 'nombre')

    resultado = []
    for paq in paquetes:
        # Precio mostrado en el portal CON IVA (16%) incluido, para que
        # coincida con el total del PDF. El item real se crea con el precio
        # sin IVA (sugerencia_precio) y calcular_totales() le suma el 16%.
        precio_con_iva = impuestos.con_iva(Decimal(str(paq.sugerencia_precio())))
        resultado.append({
            'id': paq.id,
            'nombre': paq.nombre,
            'icono': paq.icono,
            'descripcion': paq.descripcion_corta,
            'descripcion_larga': paq.descripcion,
            'precio': str(precio_con_iva),
        })

    return JsonResponse({'ok': True, 'paquetes': resultado})


def cotizador_gracias(request):
    # `portal` llega por query string y acaba en un href y en un
    # window.location: solo se acepta si apunta al propio portal. Sin ese
    # filtro, un `javascript:...` o un dominio ajeno convierten esta pantalla
    # en XSS y en redirección abierta. Sin `?portal=` válido no hay cotización
    # que enseñar: se cae al portal genérico y la plantilla usa `portal_base`
    # para reconocer ese caso y no autoredirigir.
    solicitado = request.GET.get('portal') or ''
    if solicitado.startswith(f'{settings.PORTAL_URL}/'):
        portal_url = solicitado
    else:
        portal_url = settings.PORTAL_URL
    return render(request, 'cotizador/gracias.html', {
        'portal_url': portal_url,
        'portal_base': settings.PORTAL_URL,
    })
