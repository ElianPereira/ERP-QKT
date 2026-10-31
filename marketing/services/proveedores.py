"""Proveedores de anuncios (Issue #468, fase 2).

`ProveedorAnuncios` es la interfaz que cumple cada plataforma; hoy solo Meta
(Marketing API por REST con `requests`, sin SDK). Sin credenciales el proveedor
queda apagado: `configurado()` es False y ningún método sale a la red.

Los métodos de acción (`pausar`, `reanudar`, `ajustar_presupuesto`) y la lectura
del estado vivo (`leer_estado`) los usa `services/acciones.py`, que pone los topes,
la simulación y la bitácora; el proveedor solo habla con la plataforma.

Permisos de Meta: leer pide `ads_read` (`META_ADS_TOKEN`); cambiar estado o
presupuesto pide `ads_management` y va con un token aparte
(`META_ADS_TOKEN_ESCRITURA`). Sin ese token ninguna escritura sale a la red.

Dónde vive el presupuesto: con presupuesto de campaña (CBO) `daily_budget` está
en la campaña y se ajusta ahí. Con presupuesto por conjunto de anuncios la
campaña no trae `daily_budget`: `leer_estado` lo detecta (`nivel_presupuesto`
= CONJUNTO) y `ajustar_presupuesto` se niega; se ajusta a mano en Meta. Los
montos de Meta van en la unidad menor de la moneda de la cuenta (centavos).
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from datetime import timezone as dt_timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterator, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

CENTAVOS = Decimal('0.01')
TIMEOUT = 30
MAX_PAGINAS = 200
CODIGOS_TOKEN = {190, 102}  # token vencido, revocado o inválido


class ProveedorError(Exception):
    """Fallo al hablar con la plataforma (el mensaje nunca lleva el token)."""


class TokenVencidoError(ProveedorError):
    """La plataforma rechazó la credencial: hay que generar otra."""


@dataclass(frozen=True)
class CampanaExterna:
    id_externo: str
    nombre: str
    estado: str = ''
    objetivo: str = ''
    presupuesto_diario: Optional[Decimal] = None


@dataclass(frozen=True)
class AnuncioExterno:
    id_externo: str
    campana_id: str
    nombre: str = ''
    estado: str = ''


@dataclass(frozen=True)
class GastoDiario:
    fecha: date
    campana_id: str
    anuncio_id: str
    monto: Decimal
    impresiones: int = 0
    clics: int = 0
    frecuencia: Optional[Decimal] = None
    campana_nombre: str = ''
    anuncio_nombre: str = ''


@dataclass(frozen=True)
class EstadoObjeto:
    """Estado vivo de una campaña o un anuncio leído de la plataforma."""
    id_externo: str
    estado: str = ''
    presupuesto_diario: Optional[Decimal] = None
    nivel_presupuesto: str = ''  # CAMPANA | CONJUNTO | '' (anuncio o sin presupuesto diario)


@dataclass(frozen=True)
class EstadoCredencial:
    valida: bool
    vence_en: Optional[datetime] = None  # None = no vence (o no se sabe)
    detalle: str = ''


class ProveedorAnuncios(ABC):
    plataforma = ''

    @abstractmethod
    def configurado(self) -> bool: ...

    @abstractmethod
    def moneda(self) -> str: ...

    @abstractmethod
    def listar_campanas(self) -> Iterator[CampanaExterna]: ...

    @abstractmethod
    def listar_anuncios(self) -> Iterator[AnuncioExterno]: ...

    @abstractmethod
    def gasto_diario(self, desde: date, hasta: date) -> Iterator[GastoDiario]: ...

    @abstractmethod
    def estado_credencial(self) -> EstadoCredencial: ...

    # Acciones (fase 5). `puede_escribir` es False sin credencial de escritura.
    def puede_escribir(self) -> bool:
        return False

    def leer_estado(self, id_externo: str, con_presupuesto: bool = False) -> 'EstadoObjeto':
        raise NotImplementedError

    def pausar(self, id_externo: str):
        raise NotImplementedError

    def reanudar(self, id_externo: str):
        raise NotImplementedError

    def ajustar_presupuesto(self, id_externo: str, presupuesto_diario: Decimal):
        raise NotImplementedError


def _decimal(valor, default=Decimal('0.00')) -> Decimal:
    try:
        return Decimal(str(valor)).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
    except Exception:
        return default


def _entero(valor) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


class ProveedorMeta(ProveedorAnuncios):
    plataforma = 'META'

    def __init__(self, token=None, cuenta_id=None, version=None, token_escritura=None):
        self.token = token if token is not None else settings.META_ADS_TOKEN
        self.token_escritura = (token_escritura if token_escritura is not None
                                else settings.META_ADS_TOKEN_ESCRITURA)
        cuenta = (cuenta_id if cuenta_id is not None else settings.META_ADS_CUENTA_ID).strip()
        self.cuenta = cuenta if not cuenta or cuenta.startswith('act_') else f'act_{cuenta}'
        self.version = version or settings.META_ADS_API_VERSION

    @property
    def base(self):
        return f'https://graph.facebook.com/{self.version}'

    def configurado(self) -> bool:
        return bool(self.token and self.cuenta)

    def puede_escribir(self) -> bool:
        return bool(self.configurado() and self.token_escritura)

    def _limpiar(self, texto):
        texto = str(texto)
        for secreto in (self.token, self.token_escritura):
            if secreto:
                texto = texto.replace(secreto, '***')
        return texto

    def _get(self, url, params=None):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ProveedorError(f'Sin respuesta de Meta: {self._limpiar(exc)}') from exc
        return self._leer(r)

    def _post(self, ruta, datos):
        """POST de escritura: el token va en el cuerpo (nunca en la URL, que acaba en logs)."""
        if not self.puede_escribir():
            raise ProveedorError('Sin credencial de escritura de Meta (META_ADS_TOKEN_ESCRITURA): no se envió nada.')
        try:
            r = requests.post(f'{self.base}/{ruta}', data={**datos, 'access_token': self.token_escritura},
                              timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise ProveedorError(f'Sin respuesta de Meta: {self._limpiar(exc)}') from exc
        cuerpo = self._leer(r)
        if cuerpo.get('success') is not True:
            raise ProveedorError('Meta no confirmó el cambio (sin success).')
        return cuerpo

    def _leer(self, r):
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = {}
        error = cuerpo.get('error') if isinstance(cuerpo, dict) else None
        if error or r.status_code >= 400:
            error = error or {}
            codigo = error.get('code')
            mensaje = self._limpiar(error.get('message') or f'HTTP {r.status_code}')
            if codigo in CODIGOS_TOKEN or r.status_code == 401:
                raise TokenVencidoError(f'Meta rechazó el token ({codigo}): {mensaje}')
            raise ProveedorError(f'Meta respondió con error ({codigo or r.status_code}): {mensaje}')
        return cuerpo

    def _paginar(self, ruta, params):
        """Sigue `paging.next` hasta agotar los resultados."""
        url, params = f'{self.base}/{ruta}', {**params, 'access_token': self.token}
        for _ in range(MAX_PAGINAS):
            cuerpo = self._get(url, params)
            yield from cuerpo.get('data', [])
            url, params = (cuerpo.get('paging') or {}).get('next'), None
            if not url:
                return
        raise ProveedorError('Meta devolvió demasiadas páginas.')

    def moneda(self) -> str:
        cuerpo = self._get(f'{self.base}/{self.cuenta}', {'fields': 'currency', 'access_token': self.token})
        return cuerpo.get('currency', '')

    def listar_campanas(self):
        for c in self._paginar(f'{self.cuenta}/campaigns', {
                'fields': 'id,name,objective,effective_status,daily_budget', 'limit': 200}):
            presupuesto = _decimal(c['daily_budget']) / 100 if c.get('daily_budget') else None
            yield CampanaExterna(
                id_externo=str(c['id']), nombre=c.get('name', ''), estado=c.get('effective_status', ''),
                objetivo=c.get('objective', ''),
                presupuesto_diario=presupuesto.quantize(CENTAVOS, rounding=ROUND_HALF_UP) if presupuesto else None)

    def listar_anuncios(self):
        for a in self._paginar(f'{self.cuenta}/ads', {'fields': 'id,name,campaign_id,effective_status', 'limit': 200}):
            yield AnuncioExterno(id_externo=str(a['id']), campana_id=str(a.get('campaign_id', '')),
                                 nombre=a.get('name', ''), estado=a.get('effective_status', ''))

    def gasto_diario(self, desde, hasta):
        import json
        params = {
            'level': 'ad', 'time_increment': 1, 'limit': 500,
            'fields': 'campaign_id,campaign_name,ad_id,ad_name,spend,impressions,clicks,frequency',
            'time_range': json.dumps({'since': desde.isoformat(), 'until': hasta.isoformat()}),
        }
        for fila in self._paginar(f'{self.cuenta}/insights', params):
            frecuencia = _decimal(fila['frequency'], None) if fila.get('frequency') else None
            yield GastoDiario(
                fecha=date.fromisoformat(fila['date_start']), campana_id=str(fila['campaign_id']),
                anuncio_id=str(fila['ad_id']), monto=_decimal(fila.get('spend', 0)),
                impresiones=_entero(fila.get('impressions')), clics=_entero(fila.get('clicks')),
                frecuencia=frecuencia, campana_nombre=fila.get('campaign_name', ''),
                anuncio_nombre=fila.get('ad_name', ''))

    def leer_estado(self, id_externo, con_presupuesto=False):
        """`con_presupuesto` solo para campañas: detecta si el presupuesto vive en ella o en sus conjuntos."""
        campos = 'effective_status,daily_budget' if con_presupuesto else 'effective_status'
        cuerpo = self._get(f'{self.base}/{id_externo}', {'fields': campos, 'access_token': self.token})
        presupuesto, nivel = None, ''
        if cuerpo.get('daily_budget'):
            presupuesto = (_decimal(cuerpo['daily_budget']) / 100).quantize(CENTAVOS, rounding=ROUND_HALF_UP)
            nivel = 'CAMPANA'
        elif con_presupuesto and self._tiene_presupuesto_en_conjuntos(id_externo):
            nivel = 'CONJUNTO'
        return EstadoObjeto(str(id_externo), cuerpo.get('effective_status', ''), presupuesto, nivel)

    def _tiene_presupuesto_en_conjuntos(self, campana_id):
        try:
            cuerpo = self._get(f'{self.base}/{campana_id}/adsets', {
                'fields': 'daily_budget', 'limit': 50, 'access_token': self.token})
        except ProveedorError:
            return False
        return any(c.get('daily_budget') for c in cuerpo.get('data', []))

    def pausar(self, id_externo):
        self._post(str(id_externo), {'status': 'PAUSED'})

    def reanudar(self, id_externo):
        self._post(str(id_externo), {'status': 'ACTIVE'})

    def ajustar_presupuesto(self, id_externo, presupuesto_diario):
        """Solo presupuesto de campaña (CBO); `presupuesto_diario` en pesos, Meta lo recibe en centavos."""
        centavos = int((Decimal(presupuesto_diario) * 100).to_integral_value(rounding=ROUND_HALF_UP))
        if centavos <= 0:
            raise ProveedorError('El presupuesto diario debe ser mayor que cero.')
        self._post(str(id_externo), {'daily_budget': centavos})

    def estado_credencial(self) -> EstadoCredencial:
        """`debug_token`: un token de usuario del sistema no vence (`expires_at` = 0)."""
        try:
            cuerpo = self._get(f'{self.base}/debug_token',
                               {'input_token': self.token, 'access_token': self.token})
        except TokenVencidoError as exc:
            return EstadoCredencial(False, detalle=str(exc))
        datos = cuerpo.get('data') or {}
        if not datos.get('is_valid', False):
            return EstadoCredencial(False, detalle='Meta marca el token como no válido.')
        expira = _entero(datos.get('expires_at'))
        vence = datetime.fromtimestamp(expira, tz=dt_timezone.utc) if expira else None
        return EstadoCredencial(True, vence_en=vence)


def proveedor_meta() -> ProveedorMeta:
    return ProveedorMeta()
