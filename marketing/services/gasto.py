"""Sincronización del gasto publicitario y avisos de salud (Issue #468, fase 2)."""
import logging
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from comunicacion.services_pendientes import registrar_pendiente

from ..models import Anuncio, Campana, GastoPublicitario
from .proveedores import ProveedorError, TokenVencidoError, proveedor_meta

logger = logging.getLogger(__name__)

DIAS_ATRAS = 3          # Meta reescribe los días recientes
DIAS_AVISO_TOKEN = 14   # el token vence en menos de esto = aviso


def _pendiente(titulo, detalle, clave, severidad='NORMAL'):
    registrar_pendiente(titulo=titulo, rol='DIRECCION', tipo='CRON', severidad=severidad,
                        detalle=detalle, clave_idempotencia=clave)


def revisar_credencial(proveedor, hoy=None) -> dict:
    """Avisa si el token ya no vale o vence en menos de 14 días."""
    hoy = hoy or timezone.localdate()
    estado = proveedor.estado_credencial()
    if not estado.valida:
        _pendiente('Meta Ads: el token ya no es válido', f'Genera otro token. {estado.detalle}',
                   f'marketing_token:invalido:{hoy:%Y-%m}', 'URGENTE')
        return {'valida': False}
    if estado.vence_en and estado.vence_en - timezone.now() < timedelta(days=DIAS_AVISO_TOKEN):
        _pendiente('Meta Ads: el token vence pronto', f'Vence el {estado.vence_en:%d/%m/%Y}. Genera otro token.',
                   f'marketing_token:vence:{estado.vence_en:%Y%m%d}', 'URGENTE')
    return {'valida': True, 'vence_en': estado.vence_en}


def _campana(plataforma, ext, nombre, estado=None, objetivo=None, presupuesto=None):
    campana, creada = Campana.objects.get_or_create(
        plataforma=plataforma, id_externo=ext.strip(), defaults={'nombre': nombre or ext})
    cambios = {}
    for campo, valor in (('nombre', nombre), ('estado', estado), ('objetivo_plataforma', objetivo),
                         ('presupuesto_diario', presupuesto)):
        if valor not in (None, '') and getattr(campana, campo) != valor:
            cambios[campo] = valor
    if cambios:
        Campana.objects.filter(pk=campana.pk).update(**cambios, updated_at=timezone.now())
        for k, v in cambios.items():
            setattr(campana, k, v)
    return campana


def _anuncio(campana, ext, nombre, estado=None):
    anuncio, _ = Anuncio.objects.get_or_create(campana=campana, id_externo=ext, defaults={'nombre': nombre or ''})
    cambios = {k: v for k, v in (('nombre', nombre), ('estado', estado)) if v and getattr(anuncio, k) != v}
    if cambios:
        Anuncio.objects.filter(pk=anuncio.pk).update(**cambios, updated_at=timezone.now())
    return anuncio


def sincronizar(desde: date = None, hasta: date = None, proveedor=None, hoy=None) -> dict:
    """Trae campañas, anuncios y gasto diario y los guarda con upsert idempotente
    (campaña/anuncio, fecha, fuente). Sin credenciales no hace nada. Un fallo de
    la plataforma deja un pendiente y se vuelve a lanzar para que el
    programador lo registre como error."""
    proveedor = proveedor or proveedor_meta()
    if not proveedor.configurado():
        return {'omitido': True, 'filas': 0}
    hoy = hoy or timezone.localdate()
    hasta = hasta or hoy
    desde = desde or hasta - timedelta(days=DIAS_ATRAS)
    plataforma = proveedor.plataforma
    try:
        if proveedor.moneda() != 'MXN':
            raise ProveedorError('La cuenta publicitaria no factura en MXN; no se importa el gasto.')
        campanas = list(proveedor.listar_campanas())
        anuncios = list(proveedor.listar_anuncios())
        gastos = list(proveedor.gasto_diario(desde, hasta))
    except TokenVencidoError as exc:
        _pendiente('Meta Ads: el token ya no es válido', f'Genera otro token. {exc}',
                   f'marketing_token:invalido:{hoy:%Y-%m}', 'URGENTE')
        raise
    except ProveedorError as exc:
        _pendiente('Meta Ads: falló la sincronización del gasto', str(exc), f'marketing_sync:{hoy:%Y-%m-%d}')
        raise

    filas = 0
    with transaction.atomic():
        por_campana = {}
        for c in campanas:
            por_campana[c.id_externo] = _campana(plataforma, c.id_externo, c.nombre, c.estado, c.objetivo,
                                                 c.presupuesto_diario)
        por_anuncio = {}
        for a in anuncios:
            campana = por_campana.get(a.campana_id) or _campana(plataforma, a.campana_id, '')
            por_campana[a.campana_id] = campana
            por_anuncio[a.id_externo] = _anuncio(campana, a.id_externo, a.nombre, a.estado)
        for g in gastos:
            campana = por_campana.get(g.campana_id) or _campana(plataforma, g.campana_id, g.campana_nombre)
            por_campana[g.campana_id] = campana
            anuncio = por_anuncio.get(g.anuncio_id) or _anuncio(campana, g.anuncio_id, g.anuncio_nombre)
            por_anuncio[g.anuncio_id] = anuncio
            existente = GastoPublicitario.objects.filter(
                anuncio=anuncio, fecha=g.fecha, fuente=GastoPublicitario.META_API).first()
            datos = {'campana': campana, 'monto': g.monto, 'impresiones': g.impresiones, 'clics': g.clics,
                     'frecuencia': g.frecuencia}
            if existente is None:
                GastoPublicitario.objects.create(anuncio=anuncio, fecha=g.fecha, fuente=GastoPublicitario.META_API,
                                                 **datos)
                filas += 1
            elif any(getattr(existente, k) != v for k, v in datos.items()):
                if existente.monto != g.monto:
                    datos['estado_conciliacion'] = GastoPublicitario.PENDIENTE
                for k, v in datos.items():
                    setattr(existente, k, v)
                existente.save()
                filas += 1
    return {'omitido': False, 'filas': filas, 'campanas': len(por_campana), 'anuncios': len(por_anuncio)}
