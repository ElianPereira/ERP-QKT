"""
Avisos de vencimiento del módulo de Cumplimiento (Issue #403, fase 3).

Un mensaje consolidado por corrida y canal al propietario: WhatsApp a
los números de Dirección del directorio de responsables (plantilla `WA_TEMPLATE_OPERACIONES` con el resumen en el
parámetro, que sí llega fuera de la ventana de 24 h; sin plantilla, texto
libre) y correo interno. La idempotencia es `AvisoVencimiento`: el INSERT va
antes del envío, igual que `ComunicacionCliente`; un canal fallido se
reintenta en la corrida siguiente sin bloquear al otro.

Solo avisa la vigencia actual de cada trámite activo: si ya hay una
renovación `OBTENIDA` posterior, la vieja no avisa.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from comunicacion.services import (
    alertar_equipo_email,
    enviar_whatsapp,
    enviar_whatsapp_template,
)
from comunicacion.services_responsables import SIN_DESTINO, telefono_de

from . import services
from .models import AvisoVencimiento

logger = logging.getLogger(__name__)

UMBRAL_VENCIDO = -1
UMBRAL_CANCELACION = -2
UMBRALES_FIJOS = (30, 7, 0)
CANALES = ('WHATSAPP', 'EMAIL')
# Un PENDIENTE más viejo que esto se considera un envío que murió a medias
ESPERA_PENDIENTE = timedelta(hours=1)
MAX_PARAMETRO_WA = 900


def umbral_aplicable(tramite, dias):
    """El umbral que toca a `dias` del vencimiento: el más cercano que ya se
    alcanzó (si el cron faltó un día, no se mandan los anteriores juntos)."""
    if dias < 0:
        return UMBRAL_VENCIDO
    for umbral in sorted({tramite.dias_aviso, *UMBRALES_FIJOS}):
        if dias <= umbral:
            return umbral
    return None


def calcular_avisos(hoy=None):
    """Lista de avisos que corresponden hoy (sin consultar lo ya enviado)."""
    hoy = hoy or timezone.localdate()
    avisos = []
    for tramite in services._tramites_activos():
        vigencia = services.vigencia_actual(tramite)
        if vigencia is None or vigencia.fecha_vencimiento is None:
            continue
        dias = (vigencia.fecha_vencimiento - hoy).days
        umbral = umbral_aplicable(tramite, dias)
        if umbral is not None:
            avisos.append({'vigencia': vigencia, 'tramite': tramite, 'umbral': umbral, 'dias': dias,
                           'cancelar_antes_de': None})
        if tramite.tipo == 'CONTRATO' and tramite.renovacion_automatica and tramite.dias_preaviso_cancelacion:
            limite = vigencia.fecha_vencimiento - timedelta(days=tramite.dias_preaviso_cancelacion)
            if limite <= hoy <= vigencia.fecha_vencimiento:
                avisos.append({'vigencia': vigencia, 'tramite': tramite, 'umbral': UMBRAL_CANCELACION,
                               'dias': dias, 'cancelar_antes_de': limite})
    avisos.sort(key=lambda a: (not a['tramite'].critico, a['dias'], a['tramite'].nombre))
    return avisos


def _linea(aviso):
    v, t = aviso['vigencia'], aviso['tramite']
    marca = '[CRÍTICO] ' if t.critico else ''
    fecha = v.fecha_vencimiento.strftime('%d/%m/%Y')
    if aviso['umbral'] == UMBRAL_CANCELACION:
        return (f"{marca}{t.nombre}: último día para cancelar sin renovar el "
                f"{aviso['cancelar_antes_de']:%d/%m/%Y} (se renueva solo el {fecha})")
    if aviso['dias'] < 0:
        return f"{marca}{t.nombre}: VENCIDO el {fecha} ({services.dias_texto(aviso['dias']).lower()})"
    return f"{marca}{t.nombre}: vence el {fecha} ({services.dias_texto(aviso['dias']).lower()})"


def construir_mensaje(avisos):
    """(asunto, cuerpo con saltos de línea, resumen en una línea para la plantilla)."""
    lineas = [_linea(a) for a in avisos]
    asunto = f"Cumplimiento: {len(avisos)} trámite(s) requieren atención"
    cuerpo = asunto + '\n\n' + '\n'.join(f'- {x}' for x in lineas)
    resumen = '; '.join(lineas)
    return asunto, cuerpo, resumen[:MAX_PARAMETRO_WA]


def _reservar(aviso, canal):
    """Toma el derecho a enviar (vigencia, umbral, canal) o devuelve None si
    ya se envió o lo está enviando otra corrida."""
    ahora = timezone.now()
    with transaction.atomic():
        fila, creada = AvisoVencimiento.objects.get_or_create(
            vigencia=aviso['vigencia'], umbral=aviso['umbral'], canal=canal,
            defaults={'estado': 'PENDIENTE', 'actualizado_en': ahora})
    if creada:
        return fila
    if fila.estado == 'FALLIDO':
        tomado = AvisoVencimiento.objects.filter(pk=fila.pk, estado='FALLIDO').update(
            estado='PENDIENTE', actualizado_en=ahora)
    elif fila.estado == 'PENDIENTE':
        tomado = AvisoVencimiento.objects.filter(
            pk=fila.pk, estado='PENDIENTE', actualizado_en__lt=ahora - ESPERA_PENDIENTE
        ).update(actualizado_en=ahora)
    else:
        return None
    return fila if tomado else None


def _enviar_whatsapp(destino, cuerpo, resumen):
    plantilla = getattr(settings, 'WA_TEMPLATE_OPERACIONES', '') or ''
    if plantilla:
        return enviar_whatsapp_template(tipo='OTRO', telefono=destino, template_name=plantilla,
                                        parametros=[resumen], trigger='CRON')
    return enviar_whatsapp(tipo='OTRO', telefono=destino, mensaje=cuerpo, trigger='CRON')


def _enviar_email(avisos, asunto, cuerpo):
    comm = alertar_equipo_email(None, asunto=asunto, cuerpo=cuerpo)
    # Copia a los responsables con correo, solo con lo que les toca
    por_responsable = {}
    for a in avisos:
        r = a['tramite'].responsable
        if r and r.email:
            por_responsable.setdefault(r.email, []).append(a)
    for correo, suyos in por_responsable.items():
        try:
            _, cuerpo_suyo, _ = construir_mensaje(suyos)
            send_mail(asunto, cuerpo_suyo, settings.EMAIL_FROM_NOTIFICACIONES, [correo], fail_silently=False)
        except Exception:
            logger.exception("No se pudo copiar el aviso de cumplimiento a %s", correo)
    return comm


def procesar_avisos(hoy=None, aplicar=False):
    """Calcula y (con `aplicar`) envía los avisos de hoy.

    Devuelve `{'avisos': [...], 'canales': {canal: {'enviados': n, 'fallidos': n}}}`.
    Sin `aplicar` no escribe ni envía nada.
    """
    avisos = calcular_avisos(hoy)
    resultado = {'avisos': avisos, 'canales': {}}
    if not aplicar or not avisos:
        return resultado

    destino_wa = telefono_de('DIRECCION')
    for canal in CANALES:
        if canal == 'WHATSAPP' and not destino_wa:
            logger.warning("%s; avisos de cumplimiento solo por correo", SIN_DESTINO)
            continue
        tomados = [(a, fila) for a in avisos if (fila := _reservar(a, canal)) is not None]
        if not tomados:
            continue
        a_enviar = [a for a, _ in tomados]
        asunto, cuerpo, resumen = construir_mensaje(a_enviar)
        try:
            comm = (_enviar_whatsapp(destino_wa, cuerpo, resumen) if canal == 'WHATSAPP'
                    else _enviar_email(a_enviar, asunto, cuerpo))
            exito = bool(comm) and comm.estado == 'ENVIADO'
            detalle = '' if exito else (getattr(comm, 'error', '') or 'No se pudo enviar')
        except Exception as exc:
            logger.exception("Error enviando aviso de cumplimiento por %s", canal)
            exito, detalle = False, str(exc)[:1000]
        ahora = timezone.now()
        AvisoVencimiento.objects.filter(pk__in=[f.pk for _, f in tomados]).update(
            estado='ENVIADO' if exito else 'FALLIDO', detalle=detalle,
            enviado_en=ahora if exito else None, actualizado_en=ahora)
        stats = resultado['canales'].setdefault(canal, {'enviados': 0, 'fallidos': 0})
        stats['enviados' if exito else 'fallidos'] += len(tomados)
    return resultado
