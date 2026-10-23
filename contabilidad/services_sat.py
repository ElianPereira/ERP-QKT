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
VIDA_PAQUETE = timedelta(hours=70)       # el SAT los borra a las 72 h
VIDA_SOLICITUD = timedelta(hours=72)     # más que esto sin terminar: se da por perdida
ESPERA_TRAS_ERROR = timedelta(hours=6)   # no repetir el mismo tramo fallido antes de esto
MAX_DIAS_TRAMO = 31                      # una solicitud por mes: el SAT topa en 200 mil CFDI
MAX_SOLICITUDES_POR_CORRIDA = 10         # el historial largo se pide poco a poco
MAX_REINTENTOS_TRAMO = 8                 # tras tantos errores idénticos se deja de insistir
MAX_DESPLAZAMIENTO_RANGO = 5             # días que se corre un rango cuya cuota de por vida se agotó
INTENTOS_PARA_AVISAR = 24                # ~1 día de fallos transitorios seguidos


class ConfiguracionSATError(Exception):
    """Falta algo del lado del ERP (llave de cifrado, e.firma)."""


# ─── Credencial (e.firma cifrada) ────────────────────────────

def _limpiar_llave(llave):
    """Quita lo que se cuela al copiar la llave: espacios, saltos de línea, comillas
    y el prefijo b'...' con que Python imprime los bytes."""
    llave = (llave.decode() if isinstance(llave, bytes) else llave).strip()
    if llave[:2] in ("b'", 'b"'):
        llave = llave[1:]
    return llave.strip('\'"').strip()


def _fernet():
    llave = getattr(settings, 'ERP_SAT_FIEL_CIFRADO_SECRET', '')
    if not llave:
        raise ConfiguracionSATError(
            "Falta ERP_SAT_FIEL_CIFRADO_SECRET en las variables de entorno. Genérala con "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`.")
    llave = _limpiar_llave(llave)
    try:
        return Fernet(llave.encode())
    except ValueError as e:
        raise ConfiguracionSATError(
            f"ERP_SAT_FIEL_CIFRADO_SECRET no es una llave Fernet válida (tiene {len(llave)} caracteres; debe tener "
            "44, terminar en «=» y usar solo letras, números, «-» y «_»). Genérala con "
            "`openssl rand -base64 32 | tr '+/' '-_'` y pégala sin comillas ni espacios.") from e


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
        raise ConfiguracionSATError("ERP_SAT_FIEL_CIFRADO_SECRET no coincide con la que cifró esta e.firma.") from e


# ─── Solicitudes ─────────────────────────────────────────────

def _limites(solicitud):
    """Fechas con hora que espera el SAT; FechaFinal nunca puede ser futura."""
    zona = timezone.get_current_timezone()
    inicio = datetime.combine(solicitud.desde, time.min)
    fin = datetime.combine(solicitud.hasta, time(23, 59, 59))
    ahora = timezone.localtime(timezone.now(), zona).replace(tzinfo=None)
    return inicio, min(fin, ahora)


def _dias(desde, hasta):
    d = desde
    while d <= hasta:
        yield d
        d += timedelta(days=1)


def _huecos(credencial, tipo, desde, hasta):
    """Rangos de [desde, hasta] que ninguna solicitud vigente cubre.

    Una solicitud con error no cubre nada, así que lo que el SAT rechazó o dejó
    vencer reaparece como hueco y se vuelve a pedir. Las vigentes cubren hasta
    SOLAPE_DIAS antes de su fin: los últimos días se piden otra vez porque el SAT
    publica con retraso. Si la solicitud se hizo cuando ese retraso ya había pasado
    (SOLAPE_DIAS o más después de su fin), cubre su rango completo: así un mes
    cerrado no deja sus últimos días como hueco para siempre."""
    cubiertos = set()
    zona = timezone.get_current_timezone()
    for s in SolicitudDescargaSAT.objects.filter(credencial=credencial, tipo=tipo).exclude(estado='ERROR'):
        pedida = timezone.localtime(s.created_at, zona).date()
        publicado = (pedida - s.hasta).days >= SOLAPE_DIAS
        cubiertos.update(_dias(s.desde, s.hasta if publicado else s.hasta - timedelta(days=SOLAPE_DIAS)))
    rangos, inicio = [], None
    for d in _dias(desde, hasta):
        if d not in cubiertos and inicio is None:
            inicio = d
        elif d in cubiertos and inicio is not None:
            rangos.append((inicio, d - timedelta(days=1)))
            inicio = None
    if inicio is not None:
        rangos.append((inicio, hasta))
    return rangos


def _tramos(rangos):
    """Parte los huecos largos en tramos de MAX_DIAS_TRAMO, desde su inicio (así el
    mismo hueco produce siempre los mismos tramos y el control de reintentos funciona)."""
    for desde, hasta in rangos:
        while desde <= hasta:
            fin = min(hasta, desde + timedelta(days=MAX_DIAS_TRAMO - 1))
            yield desde, fin
            desde = fin + timedelta(days=1)


def _en_espera(credencial, tipo, desde, hasta, ahora):
    """¿Ese mismo tramo falló hace poco o ya agotó sus reintentos? Evita
    martillar al SAT cuando está caído o rechaza siempre la misma petición."""
    fallidas = SolicitudDescargaSAT.objects.filter(
        credencial=credencial, tipo=tipo, desde=desde, hasta=hasta, estado='ERROR')
    if fallidas.count() >= MAX_REINTENTOS_TRAMO:
        return True
    return fallidas.filter(updated_at__gte=ahora - ESPERA_TRAS_ERROR).exists()


def _sin_cuota(credencial, tipo, desde, hasta):
    """¿El SAT ya agotó las solicitudes de por vida de ESTE rango exacto (5002)?"""
    return SolicitudDescargaSAT.objects.filter(
        credencial=credencial, tipo=tipo, desde=desde, hasta=hasta, codigo_sat=ws.COD_CUOTA_AGOTADA).exists()


def _rango_nuevo(credencial, tipo, desde, hasta):
    """Para el SAT la cuota de por vida es por rango exacto: si el tramo ya la agotó se
    pide un día antes (otro rango, misma información). None si ya se corrió demasiado."""
    corrido = 0
    while _sin_cuota(credencial, tipo, desde, hasta):
        if corrido >= MAX_DESPLAZAMIENTO_RANGO:
            return None
        desde -= timedelta(days=1)
        corrido += 1
    return desde, hasta


def planear_sincronizacion(credencial, hoy=None, dias_iniciales=35):
    """Pide al SAT todo lo que falte entre el alta de la e.firma (menos
    `dias_iniciales`) y ayer. Se autorrepara: si una solicitud falla de forma
    definitiva, su tramo vuelve a ser hueco y se pide de nuevo."""
    hoy = hoy or timezone.localdate()
    ayer = hoy - timedelta(days=1)
    inicio = timezone.localtime(credencial.created_at).date() - timedelta(days=dias_iniciales)
    if credencial.historial_desde:
        inicio = min(inicio, credencial.historial_desde)
    ahora = timezone.now()
    hoy_00 = timezone.make_aware(datetime.combine(hoy, time.min))
    creadas = []
    for tipo in ('EMITIDAS', 'RECIBIDAS'):
        for desde, hasta in _tramos(_huecos(credencial, tipo, inicio, ayer)):
            if len(creadas) >= MAX_SOLICITUDES_POR_CORRIDA:
                return creadas
            # El solape (últimos días) se repite una vez por día, no en cada corrida.
            rango = _rango_nuevo(credencial, tipo, desde, hasta)
            if rango is None:
                continue
            desde, hasta = rango
            ya_hoy = SolicitudDescargaSAT.objects.filter(
                credencial=credencial, tipo=tipo, created_at__gte=hoy_00, desde__lte=desde, hasta__gte=hasta,
            ).exclude(estado='ERROR').exists()
            if not ya_hoy and not _en_espera(credencial, tipo, desde, hasta, ahora):
                creadas.append(SolicitudDescargaSAT.objects.create(
                    credencial=credencial, tipo=tipo, desde=desde, hasta=hasta))
    return creadas


def _error(solicitud, mensaje, codigo=''):
    """Fallo definitivo de ESTA solicitud (no de la información: su tramo se vuelve
    a pedir en la siguiente planeación). Avisa al equipo."""
    solicitud.estado = 'ERROR'
    solicitud.mensaje = mensaje[:2000]
    solicitud.codigo_sat = codigo
    solicitud.save()
    _alertar(solicitud, f"Solicitud con error definitivo: {mensaje}", 'error')


def _alertar(solicitud, motivo, clave):
    """Pendiente URGENTE para Dirección y correo al equipo, una sola vez por
    solicitud y motivo. Nunca rompe el proceso."""
    from comunicacion.services import alertar_equipo_email
    from comunicacion.services_pendientes import registrar_pendiente
    registrar_pendiente(
        titulo=f"Descarga del SAT con problemas: {solicitud}", tipo='SAT', severidad='URGENTE',
        rol='DIRECCION', detalle=f"{motivo} Revisa Contabilidad, Descargas SAT.",
        clave_idempotencia=f"sat:{solicitud.pk}:{clave}")
    try:
        alertar_equipo_email(
            None, pendiente=False, asunto=f"Descarga del SAT: {solicitud}",
            cuerpo=f"{solicitud.get_tipo_display()} {solicitud.desde:%d/%m/%Y} – {solicitud.hasta:%d/%m/%Y} "
                   f"({solicitud.credencial.rfc})\n{motivo}\nCódigo SAT: {solicitud.codigo_sat or '—'}\n"
                   "El ERP la reintentará solo; revisa Contabilidad → Descargas si persiste.",
            clave_idempotencia=f"sat:{solicitud.pk}:{clave}")
    except Exception:
        logger.exception("No se pudo avisar de la descarga SAT #%s", solicitud.pk)


def avanzar(solicitud, cliente=None):
    """Da un paso de la máquina de estados. Devuelve el estado resultante.

    Un fallo de comunicación o del SAT (intermitente) conserva el estado y se
    reintenta en la siguiente corrida; solo es definitivo lo que el SAT rechaza o
    deja vencer, y de eso se encarga la planeación (vuelve a pedir el tramo)."""
    if solicitud.terminada:
        return solicitud.estado
    solicitud.intentos += 1
    try:
        if _caducada(solicitud):
            return solicitud.estado
        if cliente is None:
            cliente = ws.ClienteSAT(fiel_de(solicitud.credencial))
        if solicitud.estado == 'PENDIENTE':
            _enviar(solicitud, cliente)
        elif solicitud.estado in ('ENVIADA', 'EN_PROCESO'):
            _verificar(solicitud, cliente)
        if solicitud.estado == 'TERMINADA':
            _descargar(solicitud, cliente)
    except (ws.SatError, ws.FielError, ConfiguracionSATError) as e:
        solicitud.mensaje = str(e)[:2000]
        logger.warning("Descarga SAT #%s: %s", solicitud.pk, e)
        solicitud.save()
        if solicitud.intentos == INTENTOS_PARA_AVISAR:
            _alertar(solicitud, f"Lleva {solicitud.intentos} intentos sin avanzar: {e}", 'atascada')
    return solicitud.estado


def _caducada(solicitud):
    """El SAT no entrega lo que tarda demasiado, y los paquetes se borran a las
    72 horas: se da por perdida para que su tramo se pida de nuevo."""
    ahora = timezone.now()
    if solicitud.estado == 'TERMINADA' and solicitud.terminada_en and ahora - solicitud.terminada_en > VIDA_PAQUETE:
        _error(solicitud, "Los paquetes vencieron en el SAT antes de poder descargarlos.")
        return True
    if solicitud.estado in ('PENDIENTE', 'ENVIADA', 'EN_PROCESO') and ahora - solicitud.created_at > VIDA_SOLICITUD:
        _error(solicitud, "El SAT no la terminó en el plazo esperado.")
        return True
    return False


def _enviar(solicitud, cliente):
    inicio, fin = _limites(solicitud)
    if fin <= inicio:
        return _error(solicitud, "El rango pedido es futuro o vacío.")
    r = cliente.solicitar(solicitud.tipo, inicio, fin)
    solicitud.codigo_sat, solicitud.mensaje = r['codigo'], r['mensaje']
    if r['codigo'] in ws.CODIGOS_TRANSITORIOS or not r['codigo']:
        raise ws.SatError(f"El SAT no pudo recibir la solicitud ({r['codigo'] or 'sin código'}): {r['mensaje']}")
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
    if r['codigo'] in ws.CODIGOS_TRANSITORIOS or not r['codigo']:
        raise ws.SatError(f"El SAT no pudo verificar la solicitud ({r['codigo'] or 'sin código'}): {r['mensaje']}")
    if r['codigo'] != ws.COD_OK:
        return _error(solicitud, f"El SAT rechazó la verificación: {r['mensaje']}", r['codigo'])
    if not r['estado']:
        raise ws.SatError(f"El SAT respondió sin estado de solicitud: {r['mensaje']}")
    if r['estado'] in (ws.SAT_ACEPTADA, ws.SAT_EN_PROCESO):
        solicitud.estado = 'EN_PROCESO'
    elif r['estado'] == ws.SAT_TERMINADA:
        solicitud.numero_cfdis = r['numero_cfdis']
        solicitud.paquetes = r['paquetes']
        solicitud.estado = 'TERMINADA' if r['paquetes'] else 'SIN_INFORMACION'
        solicitud.terminada_en = timezone.now()
    elif r['estado'] == ws.SAT_VENCIDA:
        return _error(solicitud, "La solicitud venció en el SAT; genera una nueva.", r['codigo_estado'])
    elif r['estado'] == ws.SAT_RECHAZADA and r['codigo_estado'] == ws.COD_SIN_INFORMACION:
        # Rechazada con 5004 = el SAT no tiene CFDI en ese periodo: resultado normal.
        solicitud.numero_cfdis = 0
        solicitud.estado = 'SIN_INFORMACION'
        solicitud.terminada_en = timezone.now()
    else:
        return _error(solicitud,
                       f"El SAT no pudo generar la solicitud (estado {r['estado']}, código {r['codigo_estado'] or 'sin código'}): "
                       f"{r['mensaje']}", r['codigo_estado'])
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
    """Da un paso a toda solicitud no terminada. Lo llama el cron. Una solicitud
    que truene por un error inesperado no detiene a las demás."""
    n = 0
    for s in SolicitudDescargaSAT.objects.exclude(estado__in=SolicitudDescargaSAT.FINALES).select_related('credencial'):
        if not s.credencial.activa:
            continue
        try:
            avanzar(s, (cliente_por_credencial or {}).get(s.credencial_id))
        except Exception:
            logger.exception("Descarga SAT #%s: error inesperado", s.pk)
            continue
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
    """Un comprobante que truene no frena el resto del paquete ni se pierde: queda
    registrado con resultado ERROR (con su XML) y se reintenta en cada corrida."""
    datos = leer_cfdi(xml)
    if not datos:
        solicitud.errores += 1
        return
    previo = CfdiSAT.objects.filter(uuid=datos['uuid']).first()
    if previo and previo.resultado not in ('SIN_VINCULAR', 'ERROR'):
        solicitud.duplicados += 1
        return
    try:
        with transaction.atomic():
            _procesar_cfdi(solicitud, datos, xml)
    except Exception as e:
        logger.exception("CFDI %s de la descarga SAT #%s no se pudo procesar", datos['uuid'], solicitud.pk)
        _registrar_error(solicitud, datos, xml, e)


def _procesar_cfdi(solicitud, datos, xml):
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
    registro.detalle = ''
    (_procesar_recibido if recibida else _procesar_emitido)(solicitud, registro, datos, xml)
    registro.save()


def _registrar_error(solicitud, datos, xml, error):
    recibida = solicitud.tipo == 'RECIBIDAS'
    try:
        with transaction.atomic():
            registro, _ = CfdiSAT.objects.get_or_create(uuid=datos['uuid'], defaults={
                'direccion': 'RECIBIDO' if recibida else 'EMITIDO', 'tipo_comprobante': datos['tipo'],
                'rfc_emisor': datos['rfc_emisor'], 'rfc_receptor': datos['rfc_receptor'],
                'total': datos['total'], 'fecha': datos['fecha'], 'descarga': solicitud,
                'nombre_contraparte': (datos['nombre_emisor'] if recibida else datos['nombre_receptor'])[:300]})
            registro.resultado, registro.detalle = 'ERROR', f"{type(error).__name__}: {error}"[:300]
            if not registro.archivo_xml:
                registro.archivo_xml.save(f"{datos['uuid']}.xml", ContentFile(xml), save=False)
            registro.save()
    except Exception as e:
        # Ni siquiera se pudo guardar el XML (p. ej. el storage está caído): se
        # aborta el paquete para que se descargue completo en la siguiente corrida.
        raise ws.SatError(f"No se pudo guardar el comprobante {datos['uuid']}: {e}") from e
    solicitud.errores += 1


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


def candidatas_emitido(rfc_receptor, total, fecha, rfc_emisor):
    """Solicitudes de factura sin timbrar que podrían ser este CFDI emitido: mismo RFC
    receptor, monto (±TOLERANCIA_MONTO) y fecha dentro de la ventana. Una sola es ligue
    automático; 0 o varias quedan para que el contador decida."""
    from facturacion.models import SolicitudFactura

    return [
        s for s in SolicitudFactura.objects.filter(
            rfc=rfc_receptor, estado__in=['PENDIENTE', 'ENVIADA'],
            monto__gte=total - TOLERANCIA_MONTO, monto__lte=total + TOLERANCIA_MONTO,
            fecha_solicitud__date__gte=fecha - timedelta(days=VENTANA_SOLICITUD_DIAS),
            fecha_solicitud__date__lte=fecha + timedelta(days=1),
        ).filter(Q(archivo_xml__isnull=True) | Q(archivo_xml=''), Q(uuid_factura=''))
        if not s.rfc_emisor or s.rfc_emisor == rfc_emisor
    ]


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
    candidatas = candidatas_emitido(datos['rfc_receptor'], datos['total'], datos['fecha'], datos['rfc_emisor'])
    if len(candidatas) == 1:
        vincular(registro, candidatas[0], xml)
        solicitud.vinculados += 1
    else:
        registro.resultado = 'SIN_VINCULAR'
        registro.detalle = (
            "Sin solicitud que coincida (RFC, monto y fecha)." if not candidatas else
            "Varias solicitudes coinciden: " + ", ".join(f"SOL-{c.pk:04d}" for c in candidatas))
        solicitud.omitidos += 1


def _adjuntar_pdf(solicitud_factura, xml):
    """PDF de la factura armado desde el XML, salvo que el contador ya subiera uno.
    Un fallo al generarlo no impide ligar el XML: queda sin PDF y se puede subir a mano."""
    if solicitud_factura.archivo_pdf or solicitud_factura.archivo_zip:
        return
    from facturacion.services_cfdi_pdf import CfdiInvalidoError, generar_pdf_cfdi
    try:
        pdf, nombre = generar_pdf_cfdi(xml)
    except (CfdiInvalidoError, OSError, ValueError):
        logger.exception("No se pudo generar el PDF de SOL-%s", solicitud_factura.pk)
        return
    solicitud_factura.archivo_pdf.save(nombre, ContentFile(pdf), save=False)


def vincular(registro, solicitud_factura, xml=None):
    """Adjunta el XML timbrado a la solicitud, genera el PDF desde él y la marca
    FACTURADA; con XML + PDF la solicitud ya cuenta como facturada y sale el aviso al cliente."""
    if xml is None:
        registro.archivo_xml.open('rb')
        xml = registro.archivo_xml.read()
    datos = leer_cfdi(xml)
    sf = solicitud_factura
    sf.archivo_xml.save(f"{datos['uuid']}.xml", ContentFile(xml), save=False)
    _adjuntar_pdf(sf, xml)
    sf.uuid_factura, sf.fecha_factura, sf.estado = datos['uuid'], datos['fecha'], 'FACTURADA'
    sf.save()
    registro.solicitud_factura, registro.resultado = sf, 'VINCULADO'
    registro.detalle = f"Ligado automáticamente a SOL-{sf.pk:04d}."
    registro.save()


def reintentar_comprobantes(credencial=None):
    """Vuelve a procesar, desde su XML guardado, los comprobantes que fallaron o que
    quedaron sin vincular (p. ej. la solicitud de factura se creó después de bajar
    el CFDI). Devuelve cuántos se resolvieron."""
    from types import SimpleNamespace

    resueltos = 0
    for registro in CfdiSAT.objects.filter(resultado__in=['SIN_VINCULAR', 'ERROR']):
        if credencial and registro.direccion == 'EMITIDO' and credencial.rfc != registro.rfc_emisor:
            continue
        try:
            registro.archivo_xml.open('rb')
            xml = registro.archivo_xml.read()
            registro.archivo_xml.close()
            datos = leer_cfdi(xml)
            if not datos:
                continue
            antes = registro.resultado
            contadores = SimpleNamespace(
                credencial=SimpleNamespace(rfc=registro.rfc_emisor), importados=0, vinculados=0,
                duplicados=0, omitidos=0, errores=0)
            with transaction.atomic():
                registro.detalle = ''
                (_procesar_emitido if registro.direccion == 'EMITIDO' else _procesar_recibido)(
                    contadores, registro, datos, xml)
                registro.save()
        except Exception as e:
            logger.exception("Reintento del CFDI %s falló", registro.uuid)
            CfdiSAT.objects.filter(pk=registro.pk).update(resultado='ERROR', detalle=f"{type(e).__name__}: {e}"[:300])
            continue
        if registro.resultado not in ('SIN_VINCULAR', 'ERROR') and registro.resultado != antes:
            resueltos += 1
    return resueltos
