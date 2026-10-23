"""
Lista de compras automática por evento (Fase 3, Issue #434).

Tres días antes del evento se le manda al rol COMPRAS la lista que ya genera
`comercial.views.generar_lista_compras_barra`: aviso por WhatsApp con botón
«Ya compré» (cierra su pendiente) y el PDF por correo si el responsable tiene
email. Sin responsable de Compras, la recibe el ENCARGADO. Sin artículos en la
lista no se manda nada.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core_erp.documentos import nombre_archivo, render_pdf

from .models import Pendiente
from .services import enviar_email
from .services_pendientes import BOTON_OK, _enviar_aviso, id_boton, registrar_pendiente
from .services_responsables import SIN_DESTINO, responsable, telefono_de

logger = logging.getLogger(__name__)

DIAS_ANTICIPACION = 3
ESTADOS = ('CONFIRMADA',)
TIPOS_SERVICIO = ('EVENTO',)


def rol_destino() -> str:
    """COMPRAS si hay quien lo atienda; si no, el ENCARGADO."""
    return 'COMPRAS' if telefono_de('COMPRAS') else 'ENCARGADO'


def cotizaciones_para_compras(hoy=None):
    from comercial.models import Cotizacion

    hoy = hoy or timezone.localdate()
    return (Cotizacion.objects
            .filter(estado__in=ESTADOS, tipo_servicio__in=TIPOS_SERVICIO,
                    fecha_evento__gte=hoy, fecha_evento__lte=hoy + timedelta(days=DIAS_ANTICIPACION))
            .select_related('cliente').order_by('fecha_evento', 'id'))


def _resumen(contexto) -> str:
    lista = contexto['lista']
    articulos = sum(len(items) for items in lista.values())
    texto = f'{articulos} artículo(s) en {len(lista)} categoría(s)'
    if contexto['total_estimado']:
        texto += f', total estimado ${contexto["total_estimado"]:,.2f}'
    if contexto['pendientes']:
        texto += f'; {contexto["pendientes"]} sin proveedor asignado'
    return texto


def enviar_lista(cotizacion):
    """Manda la lista de una cotización. Idempotente (un pendiente por cotización).
    Devuelve el pendiente creado o None si no hacía falta."""
    from comercial.views import contexto_lista_compras

    clave = f'compras:{cotizacion.pk}'
    if Pendiente.objects.filter(clave_idempotencia=clave).exists():
        return None
    contexto = contexto_lista_compras(cotizacion)
    if not contexto['lista']:
        return None
    rol = rol_destino()
    destino = telefono_de(rol)
    if not destino:
        logger.error("%s; la lista de compras de COT-%s no se envió", SIN_DESTINO, cotizacion.pk)
        return None
    folio = f'COT-{cotizacion.pk:03d}'
    titulo = f'Comprar para {folio}: {cotizacion.fecha_evento:%d/%m/%Y}, {cotizacion.num_personas} personas'
    pendiente = registrar_pendiente(
        titulo=titulo, rol=rol, tipo='COMPRAS', objeto=cotizacion, detalle=_resumen(contexto),
        clave_idempotencia=clave)
    if pendiente is None:
        return None
    from django.urls import reverse
    enlace = _base() + reverse('cotizacion_lista_compras', args=[cotizacion.pk])
    _enviar_aviso(destino, f'Lista de compras de {folio} para el {cotizacion.fecha_evento:%d/%m/%Y}: '
                           f'{_resumen(contexto)}. PDF: {enlace}',
                  botones=[(id_boton(pendiente.pk, BOTON_OK), 'Ya compré')], clave=f'{clave}:whatsapp')
    _enviar_pdf(cotizacion, contexto, rol)
    return pendiente


def _base() -> str:
    from .services_pendientes import _base_url
    return _base_url()


def _enviar_pdf(cotizacion, contexto, rol) -> None:
    resp = responsable(rol)
    if resp is None or not resp.email:
        return
    try:
        pdf = render_pdf('pdf_lista_compras.html', contexto)
        enviar_email(
            cotizacion=cotizacion, tipo='COMPRAS', trigger='CRON', destinatario=resp.email,
            asunto=f'Lista de compras COT-{cotizacion.pk:03d} — {cotizacion.fecha_evento:%d/%m/%Y}',
            template='comunicacion/email/lista_compras.html',
            context={'cotizacion': cotizacion, 'resumen': _resumen(contexto), 'responsable': resp},
            adjuntos=[(nombre_archivo('ListaCompras', f'COT-{cotizacion.pk:03d}'), pdf, 'application/pdf')],
            clave_idempotencia=f'compras:{cotizacion.pk}:email')
    except Exception:
        logger.exception("No se pudo enviar por correo la lista de compras de COT-%s", cotizacion.pk)
