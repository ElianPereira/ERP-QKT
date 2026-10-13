"""Descarga masiva de CFDI del SAT: orquestación y parseo.

El SAT no responde en tiempo real: una solicitud pasa por Por enviar → Enviada
→ Preparando → Lista → Descargada, y cada paso es una llamada distinta. Por eso
`avanzar()` da UN paso y es seguro llamarlo las veces que sea (cron cada hora,
botón del admin): cada paso es idempotente y los comprobantes se deduplican por
UUID.

- Recibidos → `comercial.Compra`, con las mismas reglas de la carga masiva
  manual (`analizar_xml_compra`).
- Emitidos → se ligan a la `SolicitudFactura` que los originó (RFC receptor,
  monto y fecha); los que no se pueden ligar sin ambigüedad quedan «Emitido sin
  solicitud» para que el contador los asigne.
"""
import io
import logging
import zipfile
from datetime import datetime, time, timedelta
from decimal import Decimal

import defusedxml.ElementTree as ET
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import services_sat_ws as ws
from .models import CfdiSAT, CredencialSAT, SolicitudDescargaSAT

logger = logging.getLogger(__name__)

MAX_ARCHIVOS_ZIP = 20000
MAX_BYTES_XML = 5 * 1024 * 1024
MAX_BYTES_ZIP_DESCOMPRIMIDO = 1024 * 1024 * 1024
SOLAPE_DIAS = 3  # una sincronización repite los últimos días por si el SAT publicó tarde
TOLERANCIA_MONTO = Decimal('0.01')
VENTANA_SOLICITUD_DIAS = 45


class ConfiguracionSATError(Exception):
    """Falta algo del lado del ERP (llave de cifrado, e.firma)."""


# ─── Credencial (e.firma cifrada) ────────────────────────────

def _fernet():
    llave = getattr(settings, 'SAT_FIEL_ENCRYPTION_KEY', '')
    if not llave:
        raise ConfiguracionSATError(
            "Falta SAT_FIEL_ENCRYPTION_KEY en las variables de entorno. Genérala con "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`.")
    try:
        return Fernet(llave.encode() if isinstance(llave, str) else llave)
    except ValueError as e:
        raise ConfiguracionSATError("SAT_FIEL_ENCRYPTION_KEY no es una llave Fernet válida.") from e


def guardar_credencial(cer, key, password, usuario=None):
    """Valida la e.firma (pareja, contraseña, vigencia) y la guarda cifrada.
    Si ya existía el RFC, la reemplaza (renovación de e.firma)."""
    fernet = _fernet()
    fiel = ws.Fiel(cer, key, password)
    if not fiel.rfc:
        raise ws.FielError("El certificado no trae RFC: ¿es realmente una e.firma?")
    if fiel.vencida:
        raise ws.FielError(f"La e.firma venció el {fiel.vigente_hasta:%d/%m/%Y}.")
    nombre = ''
    for attr in fiel.cert.subject:
        if attr.oid.dotted_string == '2.5.4.3':
            nombre = attr.value
    credencial, _ = CredencialSAT.objects.update_or_create(
        rfc=fiel.rfc,
        defaults={
            'nombre': nombre,
            'cer': bytes(cer),
            'key_cifrada': fernet.encrypt(bytes(key)),
            'password_cifrada': fernet.encrypt(password.encode() if isinstance(password, str) else password),
            'vigente_hasta': fiel.vigente_hasta,
            'activa': True,
            **({'created_by': usuario} if usuario else {}),
        },
    )
    return credencial


def fiel_de(credencial):
    fernet = _fernet()
    try:
        return ws.Fiel(bytes(credencial.cer), fernet.decrypt(bytes(credencial.key_cifrada)),
                       fernet.decrypt(bytes(credencial.password_cifrada)))
    except InvalidToken as e:
        raise ConfiguracionSATError("SAT_FIEL_ENCRYPTION_KEY no coincide con la que cifró esta e.firma.") from e


# ─── Solicitudes ─────────────────────────────────────────────

def _limites(solicitud):
    """Fechas con hora que espera el SAT; FechaFinal nunca puede ser futura."""
    zona = timezone.get_current_timezone()
    inicio = datetime.combine(solicitud.desde, time.min)
    fin = datetime.combine(solicitud.hasta, time(23, 59, 59))
    ahora = timezone.localtime(timezone.now(), zona).replace(tzinfo=None)
    return inicio, min(fin, ahora)


def crear_solicitudes(credencial, desde, hasta, usuario=None):
    """Una solicitud de emitidas y una de recibidas, salvo que ya exista una
    igual (el SAT rechaza peticiones idénticas repetidas)."""
    creadas = []
    for tipo in ('EMITIDAS', 'RECIBIDAS'):
        existe = SolicitudDescargaSAT.objects.filter(
            credencial=credencial, tipo=tipo, desde=desde, hasta=hasta,
        ).exclude(estado='ERROR').exists()
        if existe:
            continue
        creadas.append(SolicitudDescargaSAT.objects.create(
            credencial=credencial, tipo=tipo, desde=desde, hasta=hasta, created_by=usuario))
    return creadas


def planear_sincronizacion(credencial, hoy=None, dias_iniciales=35):
    """Crea las solicitudes del tramo aún no cubierto (hasta ayer, con solape)."""
    hoy = hoy or timezone.localdate()
    ayer = hoy - timedelta(days=1)
    if credencial.ultima_fecha_cubierta and credencial.ultima_fecha_cubierta >= ayer:
        return []
    if credencial.ultima_fecha_cubierta:
        desde = credencial.ultima_fecha_cubierta - timedelta(days=SOLAPE_DIAS)
    else:
        desde = hoy - timedelta(days=dias_iniciales)
    if desde > ayer:
        return []
    creadas = crear_solicitudes(credencial, desde, ayer)
    if creadas:
        credencial.ultima_fecha_cubierta = ayer
        credencial.save(update_fields=['ultima_fecha_cubierta', 'updated_at'])
    return creadas


def _error(solicitud, mensaje, codigo=''):
    solicitud.estado = 'ERROR'
    solicitud.mensaje = mensaje[:2000]
    solicitud.codigo_sat = codigo
    solicitud.save()


def avanzar(solicitud, cliente=None):
    """Da un paso de la máquina de estados. Devuelve el estado resultante."""
    if solicitud.terminada:
        return solicitud.estado
    solicitud.intentos += 1
    try:
        if cliente is None:
            cliente = ws.ClienteSAT(fiel_de(solicitud.credencial))
        if solicitud.estado == 'PENDIENTE':
            _enviar(solicitud, cliente)
        elif solicitud.estado in ('ENVIADA', 'EN_PROCESO'):
            _verificar(solicitud, cliente)
        if solicitud.estado == 'TERMINADA':
            _descargar(solicitud, cliente)
    except (ws.SatError, ws.FielError, ConfiguracionSATError) as e:
        # Fallo transitorio o de configuración: se conserva el estado y se reintenta.
        solicitud.mensaje = str(e)[:2000]
        logger.warning("Descarga SAT #%s: %s", solicitud.pk, e)
        solicitud.save()
    return solicitud.estado


def _enviar(solicitud, cliente):
    inicio, fin = _limites(solicitud)
    if fin <= inicio:
        return _error(solicitud, "El rango pedido es futuro o vacío.")
    r = cliente.solicitar(solicitud.tipo, inicio, fin)
    solicitud.codigo_sat, solicitud.mensaje = r['codigo'], r['mensaje']
    if r['codigo'] == ws.COD_OK and r['id_solicitud']:
        solicitud.id_solicitud_sat = r['id_solicitud']
        solicitud.estado = 'ENVIADA'
        solicitud.save()
    elif r['codigo'] == ws.COD_SIN_INFORMACION:
        solicitud.estado = 'SIN_INFORMACION'
        solicitud.save()
    else:
        _error(solicitud, f"El SAT no aceptó la solicitud: {r['mensaje']}", r['codigo'])


def _verificar(solicitud, cliente):
    r = cliente.verificar(solicitud.id_solicitud_sat)
    solicitud.codigo_sat, solicitud.mensaje = r['codigo_estado'] or r['codigo'], r['mensaje']
    if r['codigo'] != ws.COD_OK:
        return _error(solicitud, f"El SAT rechazó la verificación: {r['mensaje']}", r['codigo'])
    if r['estado'] in (ws.SAT_ACEPTADA, ws.SAT_EN_PROCESO):
        solicitud.estado = 'EN_PROCESO'
    elif r['estado'] == ws.SAT_TERMINADA:
        solicitud.numero_cfdis = r['numero_cfdis']
        solicitud.paquetes = r['paquetes']
        solicitud.estado = 'TERMINADA' if r['paquetes'] else 'SIN_INFORMACION'
    elif r['estado'] == ws.SAT_VENCIDA:
        return _error(solicitud, "La solicitud venció en el SAT; genera una nueva.", r['codigo_estado'])
    else:
        return _error(solicitud, f"El SAT no pudo generar la solicitud: {r['mensaje']}", r['codigo_estado'])
    solicitud.save()


def _descargar(solicitud, cliente):
    for id_paquete in solicitud.paquetes:
        if id_paquete in solicitud.paquetes_descargados:
            continue
        r = cliente.descargar(id_paquete)
        if r['codigo'] != ws.COD_OK or not r['zip']:
            raise ws.SatError(f"Paquete {id_paquete}: {r['mensaje'] or 'sin contenido'}")
        with transaction.atomic():
            importar_zip(solicitud, r['zip'])
            solicitud.paquetes_descargados = [*solicitud.paquetes_descargados, id_paquete]
            solicitud.save()
    if set(solicitud.paquetes) <= set(solicitud.paquetes_descargados):
        solicitud.estado = 'DESCARGADA'
        solicitud.save()


def procesar_pendientes(cliente_por_credencial=None):
    """Da un paso a toda solicitud no terminada. Lo llama el cron."""
    n = 0
    for s in SolicitudDescargaSAT.objects.exclude(estado__in=SolicitudDescargaSAT.FINALES).select_related('credencial'):
        if not s.credencial.activa:
            continue
        cliente = (cliente_por_credencial or {}).get(s.credencial_id)
        avanzar(s, cliente)
        n += 1
    return n


# ─── Paquetes y comprobantes ─────────────────────────────────

def importar_zip(solicitud, contenido):
    try:
        z = zipfile.ZipFile(io.BytesIO(contenido))
    except zipfile.BadZipFile as e:
        raise ws.SatError("El paquete descargado no es un ZIP válido.") from e
    infos = [i for i in z.infolist() if i.filename.lower().endswith('.xml')]
    if len(infos) > MAX_ARCHIVOS_ZIP or sum(i.file_size for i in infos) > MAX_BYTES_ZIP_DESCOMPRIMIDO:
        raise ws.SatError("El paquete excede los límites de seguridad (posible ZIP bomba).")
    for info in infos:
        if info.file_size > MAX_BYTES_XML:
            solicitud.errores += 1
            continue
        _importar_xml(solicitud, z.read(info))


def leer_cfdi(xml):
    """Campos que el ERP necesita de un CFDI 3.3/4.0 (None si no es un CFDI)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    ns = 'http://www.sat.gob.mx/cfd/3' if 'cfd/3' in root.tag else 'http://www.sat.gob.mx/cfd/4'
    emisor, receptor = root.find(f'{{{ns}}}Emisor'), root.find(f'{{{ns}}}Receptor')
    if emisor is None or receptor is None:
        return None
    uuid = ''
    for el in root.iter():
        if el.tag.endswith('}TimbreFiscalDigital'):
            uuid = el.attrib.get('UUID', '').upper()
    if not uuid:
        return None
    try:
        total = Decimal(root.attrib.get('Total', '0'))
        fecha = datetime.strptime(root.attrib.get('Fecha', '')[:10], '%Y-%m-%d').date()
    except (ArithmeticError, ValueError):
        return None
    return {
        'uuid': uuid, 'tipo': root.attrib.get('TipoDeComprobante', ''), 'total': total, 'fecha': fecha,
        'rfc_emisor': emisor.attrib.get('Rfc', ''), 'nombre_emisor': emisor.attrib.get('Nombre', ''),
        'rfc_receptor': receptor.attrib.get('Rfc', ''), 'nombre_receptor': receptor.attrib.get('Nombre', ''),
    }


def _importar_xml(solicitud, xml):
    datos = leer_cfdi(xml)
    if not datos:
        solicitud.errores += 1
        return
    if CfdiSAT.objects.filter(uuid=datos['uuid']).exclude(resultado='SIN_VINCULAR').exists():
        solicitud.duplicados += 1
        return
    recibida = solicitud.tipo == 'RECIBIDAS'
    registro, _ = CfdiSAT.objects.get_or_create(uuid=datos['uuid'], defaults={
        'direccion': 'RECIBIDO' if recibida else 'EMITIDO', 'resultado': 'OMITIDO',
        'tipo_comprobante': datos['tipo'], 'rfc_emisor': datos['rfc_emisor'],
        'rfc_receptor': datos['rfc_receptor'], 'total': datos['total'], 'fecha': datos['fecha'],
        'nombre_contraparte': (datos['nombre_emisor'] if recibida else datos['nombre_receptor'])[:300],
        'descarga': solicitud,
    })
    if not registro.archivo_xml:
        registro.archivo_xml.save(f"{datos['uuid']}.xml", ContentFile(xml), save=False)
    (_procesar_recibido if recibida else _procesar_emitido)(solicitud, registro, datos, xml)
    registro.save()


def _procesar_recibido(solicitud, registro, datos, xml):
    from django.core.files.uploadedfile import SimpleUploadedFile

    from comercial.models import Compra
    from comercial.services import analizar_xml_compra

    valido, motivo, *_, es_duplicado = analizar_xml_compra(xml)
    if es_duplicado:
        registro.resultado, registro.detalle = 'IMPORTADO', "Ya existía como compra (carga manual)."
        registro.compra = Compra.objects.filter(uuid=datos['uuid']).first()
        solicitud.duplicados += 1
    elif not valido:
        registro.resultado, registro.detalle = 'OMITIDO', (motivo or '')[:300]
        solicitud.omitidos += 1
    else:
        compra = Compra.objects.create(archivo_xml=SimpleUploadedFile(
            f"{datos['uuid']}.xml", xml, content_type='application/xml'))
        registro.resultado, registro.compra = 'IMPORTADO', compra
        solicitud.importados += 1


def _procesar_emitido(solicitud, registro, datos, xml):
    from facturacion.models import SolicitudFactura

    if datos['rfc_emisor'] != solicitud.credencial.rfc:
        registro.resultado, registro.detalle = 'OMITIDO', "El emisor no es el RFC de la e.firma."
        solicitud.omitidos += 1
        return
    if datos['tipo'] != 'I':
        registro.resultado, registro.detalle = 'OMITIDO', f"Comprobante tipo «{datos['tipo']}» (no es de ingreso)."
        solicitud.omitidos += 1
        return
    ya = SolicitudFactura.objects.filter(uuid_factura=datos['uuid']).first()
    if ya:
        registro.resultado, registro.solicitud_factura = 'VINCULADO', ya
        registro.detalle = "El contador ya había subido esta factura."
        solicitud.vinculados += 1
        return
    candidatas = [
        s for s in SolicitudFactura.objects.filter(
            rfc=datos['rfc_receptor'], estado__in=['PENDIENTE', 'ENVIADA'],
            monto__gte=datos['total'] - TOLERANCIA_MONTO, monto__lte=datos['total'] + TOLERANCIA_MONTO,
            fecha_solicitud__date__gte=datos['fecha'] - timedelta(days=VENTANA_SOLICITUD_DIAS),
            fecha_solicitud__date__lte=datos['fecha'] + timedelta(days=1),
        ).filter(Q(archivo_xml__isnull=True) | Q(archivo_xml=''), Q(uuid_factura=''))
        if not s.rfc_emisor or s.rfc_emisor == datos['rfc_emisor']
    ]
    if len(candidatas) == 1:
        vincular(registro, candidatas[0], xml)
        solicitud.vinculados += 1
    else:
        registro.resultado = 'SIN_VINCULAR'
        registro.detalle = (
            "Sin solicitud que coincida (RFC, monto y fecha)." if not candidatas else
            "Varias solicitudes coinciden: " + ", ".join(f"SOL-{c.pk:04d}" for c in candidatas))
        solicitud.omitidos += 1


def vincular(registro, solicitud_factura, xml=None):
    """Adjunta el XML timbrado a la solicitud y la marca FACTURADA. El PDF no
    viaja en la descarga del SAT: el aviso al cliente sale cuando se suba."""
    if xml is None:
        registro.archivo_xml.open('rb')
        xml = registro.archivo_xml.read()
    datos = leer_cfdi(xml)
    sf = solicitud_factura
    sf.archivo_xml.save(f"{datos['uuid']}.xml", ContentFile(xml), save=False)
    sf.uuid_factura, sf.fecha_factura, sf.estado = datos['uuid'], datos['fecha'], 'FACTURADA'
    sf.save()
    registro.solicitud_factura, registro.resultado = sf, 'VINCULADO'
    registro.detalle = f"Ligado automáticamente a SOL-{sf.pk:04d}."
    registro.save()


def reintentar_vinculos(credencial=None):
    """Vuelve a buscar solicitud para los emitidos que quedaron sin vincular
    (p. ej. la solicitud de factura se creó después de bajar el CFDI)."""
    from types import SimpleNamespace

    ligados = 0
    qs = CfdiSAT.objects.filter(direccion='EMITIDO', resultado='SIN_VINCULAR')
    for registro in qs:
        rfc = registro.rfc_emisor
        if credencial and credencial.rfc != rfc:
            continue
        registro.archivo_xml.open('rb')
        xml = registro.archivo_xml.read()
        datos = leer_cfdi(xml)
        if not datos:
            continue
        contadores = SimpleNamespace(credencial=SimpleNamespace(rfc=rfc), vinculados=0, omitidos=0)
        _procesar_emitido(contadores, registro, datos, xml)
        registro.save()
        ligados += contadores.vinculados
    return ligados
