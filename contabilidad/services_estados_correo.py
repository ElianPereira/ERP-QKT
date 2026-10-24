"""
Estado de cuenta BBVA que llega por correo (Issue #401).

Cloudflare Email Routing → Worker → webhook (`views_estados_correo`) →
`registrar_correo()` guarda el `.eml` en la bitácora (`CorreoEstadoCuenta`) y
`procesar_correo()` hace lo mismo que la carga manual del admin: crea el
`EstadoCuentaBancario` y llama a `procesar_estado_cuenta()`, sin tocarla.

Este módulo no lee estados de cuenta: los parsers de
`services_estados_cuenta` siguen siendo los únicos que lo hacen. Solo cambia
*cómo llega el archivo*.

Avisos: éxito → WhatsApp a Dirección con copia al contador (solo conteos,
nunca montos, saldos ni contrapartes). Error, «ignorado por liga» y «no llegó»
→ `Pendiente` URGENTE para Dirección. Nada de esto lanza hacia quien llama.
"""
import email
import hashlib
import io
import logging
import threading
import zlib
from email import policy

import pyzipper
from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.db.models import F
from django.utils import timezone

from .models import CorreoEstadoCuenta, CuentaBancaria, EstadoCuentaBancario
from .services_estados_cuenta import _parsear_pdf_bbva, _parsear_xml_bbva, procesar_estado_cuenta

logger = logging.getLogger(__name__)

MAX_ZIP_MIEMBROS = 20
MAX_BYTES_DESCOMPRIMIDOS = 40 * 1024 * 1024
DIA_AVISO_NO_LLEGO = 7
MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre',
         'noviembre', 'diciembre')


class ErrorCorreo(Exception):
    """Falla esperada: el mensaje es corto, sin datos bancarios, y se avisa tal cual."""

    resultado = 'ERROR'


class CorreoIgnorado(ErrorCorreo):
    resultado = 'IGNORADO'


# ─── Contraseña del estado de cuenta (cifrada en CuentaBancaria) ───────────

def guardar_password_estado(cuenta, password):
    """Cifra con la misma llave Fernet que la e.firma. Vacío no cambia nada."""
    if not password:
        return
    from .services_sat import _fernet
    cuenta.password_estado_cifrada = _fernet().encrypt(password.encode())


def password_estado(cuenta):
    if not cuenta.password_estado_cifrada:
        return ''
    from cryptography.fernet import InvalidToken

    from .services_sat import _fernet
    try:
        return _fernet().decrypt(bytes(cuenta.password_estado_cifrada)).decode()
    except InvalidToken as e:
        raise ErrorCorreo("La llave de cifrado no coincide con la que guardó la contraseña del estado de cuenta.") from e


def _contrasenas():
    """Contraseñas de las cuentas activas: el ZIP se abre antes de saber de qué
    cuenta es (el número viene dentro del propio estado de cuenta)."""
    candidatas = []
    for cuenta in CuentaBancaria.objects.filter(activa=True).exclude(password_estado_cifrada__isnull=True):
        valor = password_estado(cuenta)
        if valor and valor not in candidatas:
            candidatas.append(valor)
    return candidatas


# ─── Registro (síncrono, en el request) ────────────────────────────────────

def registrar_correo(mime_bytes):
    """Guarda el correo en la bitácora antes de procesarlo, así no se pierde.

    Un `Message-ID` ya visto devuelve el registro existente **marcado
    DUPLICADO solo en memoria**: en la base conserva su resultado real (si ya
    se procesó, sigue PROCESADO). Quien llama no debe procesarlo de nuevo.
    """
    mensaje = email.message_from_bytes(mime_bytes, policy=policy.default)
    message_id = (str(mensaje.get('Message-ID') or '')).strip()[:255]
    if not message_id:
        message_id = f"sha256:{hashlib.sha256(mime_bytes).hexdigest()}"
    existente = CorreoEstadoCuenta.objects.filter(message_id=message_id).first()
    if existente is None:
        try:
            with transaction.atomic():
                return CorreoEstadoCuenta.objects.create(
                    message_id=message_id, remitente=str(mensaje.get('From') or '')[:255],
                    asunto=str(mensaje.get('Subject') or '')[:500],
                    archivo_eml=ContentFile(mime_bytes, name=f"{hashlib.sha256(mime_bytes).hexdigest()[:24]}.eml"))
        except IntegrityError:
            existente = CorreoEstadoCuenta.objects.get(message_id=message_id)
    existente.resultado = 'DUPLICADO'
    return existente


def lanzar_procesamiento(bitacora_id):
    """Procesa en segundo plano: el Worker espera un 2xx rápido."""
    def _trabajo():
        try:
            procesar_correo(CorreoEstadoCuenta.objects.get(pk=bitacora_id))
        except Exception:
            logger.exception("Estado de cuenta por correo: error procesando la bitácora %s", bitacora_id)
        finally:
            connection.close()
    threading.Thread(target=_trabajo, daemon=True, name=f'estado-correo-{bitacora_id}').start()


# ─── Adjuntos ──────────────────────────────────────────────────────────────

def _adjuntos(mensaje):
    """[(nombre, bytes)] de los adjuntos reales del correo."""
    salida = []
    for parte in mensaje.walk():
        if parte.is_multipart():
            continue
        nombre = parte.get_filename() or ''
        tipo = parte.get_content_type()
        if not nombre and tipo not in ('application/pdf', 'application/zip', 'text/xml', 'application/xml'):
            continue
        contenido = parte.get_payload(decode=True)
        if contenido:
            salida.append((nombre, contenido))
    return salida


def _formato(nombre, contenido):
    n = nombre.lower()
    if n.endswith('.zip') or contenido[:4] == b'PK\x03\x04':
        return 'ZIP'
    if n.endswith('.pdf') or contenido[:5] == b'%PDF-':
        return 'PDF'
    if n.endswith('.xml') or contenido.lstrip()[:5] == b'<?xml':
        return 'XML'
    return None


def _leer_zip(contenido, contrasenas):
    """Archivos PDF/XML del ZIP. Prueba sin contraseña y luego con cada una."""
    try:
        zf = pyzipper.AESZipFile(io.BytesIO(contenido))
        miembros = [i for i in zf.infolist() if not i.is_dir()]
    except (pyzipper.BadZipFile, NotImplementedError, EOFError) as e:
        raise ErrorCorreo("El ZIP adjunto no se pudo abrir.") from e
    if len(miembros) > MAX_ZIP_MIEMBROS or sum(i.file_size for i in miembros) > MAX_BYTES_DESCOMPRIMIDOS:
        raise ErrorCorreo("El ZIP adjunto es demasiado grande.")
    cifrado = any(i.flag_bits & 0x1 for i in miembros)
    if cifrado and not contrasenas:
        raise ErrorCorreo("El ZIP trae contraseña y la cuenta bancaria no tiene una capturada.")
    intentos = contrasenas if cifrado else [None]
    for clave in intentos:
        zf.setpassword(clave.encode() if clave else None)
        try:
            return [(i.filename, zf.read(i)) for i in miembros if _formato(i.filename, b'') in ('PDF', 'XML')]
        except RuntimeError:  # contraseña incorrecta (o archivo inválido)
            continue
        except (pyzipper.BadZipFile, NotImplementedError, EOFError, zlib.error) as e:
            raise ErrorCorreo("El ZIP adjunto está dañado.") from e
    raise ErrorCorreo("Contraseña incorrecta del ZIP.")


def _descifrar_pdf(contenido, contrasenas):
    """Si el PDF trae contraseña lo guarda sin ella (los parsers no abren PDF cifrados)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_raw
    try:
        pdfium.PdfDocument(contenido).close()
        return contenido
    except pdfium.PdfiumError as e:
        if 'password' not in str(e).lower():
            raise ErrorCorreo("El PDF adjunto no se pudo abrir.") from e
    if not contrasenas:
        raise ErrorCorreo("El PDF trae contraseña y la cuenta bancaria no tiene una capturada.")
    for clave in contrasenas:
        try:
            doc = pdfium.PdfDocument(contenido, password=clave)
        except pdfium.PdfiumError:
            continue
        salida = io.BytesIO()
        doc.save(salida, flags=pdfium_raw.FPDF_REMOVE_SECURITY)
        doc.close()
        return salida.getvalue()
    raise ErrorCorreo("Contraseña incorrecta del PDF.")


def _elegir_archivo(mensaje, contrasenas):
    """(formato, bytes, datos_del_parser). El XML gana si su parser lo lee; si no, el PDF."""
    por_formato = {'XML': [], 'PDF': []}
    hubo_adjunto = False
    for nombre, contenido in _adjuntos(mensaje):
        formato = _formato(nombre, contenido)
        if formato == 'ZIP':
            hubo_adjunto = True
            for n, c in _leer_zip(contenido, contrasenas):
                por_formato[_formato(n, c)].append(c)
        elif formato in por_formato:
            hubo_adjunto = True
            por_formato[formato].append(contenido)
    if not hubo_adjunto:
        raise CorreoIgnorado("El correo no trae adjunto (solo una liga): hay que descargar el estado de cuenta "
                             "desde BBVA y subirlo a mano.")
    parsers = {'XML': _parsear_xml_bbva, 'PDF': _parsear_pdf_bbva}
    fallo = None
    for formato in ('XML', 'PDF'):
        for contenido in por_formato[formato]:
            try:
                if formato == 'PDF':
                    contenido = _descifrar_pdf(contenido, contrasenas)
                return formato, contenido, parsers[formato](io.BytesIO(contenido))
            except NotImplementedError:
                continue
            except ErrorCorreo as e:
                fallo = fallo or e
            except Exception:
                logger.exception("Estado de cuenta por correo: no se pudo leer el %s adjunto", formato)
                fallo = fallo or ErrorCorreo("El archivo adjunto no tiene el formato de un estado de cuenta BBVA.")
    raise fallo or ErrorCorreo("El correo no trae un PDF o XML de estado de cuenta que se pueda leer.")


# ─── Procesamiento ─────────────────────────────────────────────────────────

def _mes_texto(mes, anio):
    return f"{MESES[mes - 1]} {anio}"


def _procesar(bitacora):
    with bitacora.archivo_eml.open('rb') as fh:
        mensaje = email.message_from_bytes(fh.read(), policy=policy.default)
    formato, contenido, datos = _elegir_archivo(mensaje, _contrasenas())
    _movs, _saldo_ini, _saldo_fin, numero_cuenta, fecha_corte = datos
    numero_cuenta = (numero_cuenta or '').strip()
    cuentas = list(CuentaBancaria.objects.filter(activa=True, numero_cuenta=numero_cuenta)) if numero_cuenta else []
    if len(cuentas) != 1:
        raise ErrorCorreo("El número de cuenta del estado de cuenta no corresponde a una cuenta bancaria activa.")
    cuenta = cuentas[0]
    if not fecha_corte:
        raise ErrorCorreo("No se pudo leer la fecha de corte del estado de cuenta.")
    # Mismo criterio que la carga manual de una cuenta que corta a fin de mes
    # (Maestra PYME): el periodo es el mes de la fecha de corte.
    mes, anio = fecha_corte.month, fecha_corte.year

    existente = EstadoCuentaBancario.objects.filter(
        cuenta_bancaria=cuenta, periodo_mes=mes, periodo_anio=anio).first()
    nombre = f"estado_cuenta_{cuenta.pk}_{anio}{mes:02d}.{formato.lower()}"
    if existente is not None:
        # Reintento de un intento propio que no terminó: se reutiliza; lo demás ya estaba cargado.
        propio = existente.origen == 'CORREO' and (
            existente.estado == 'ERROR'
            or (existente.pk == bitacora.estado_cuenta_id and existente.estado != 'CONCILIADO'))
        if not propio:
            bitacora.estado_cuenta = existente
            raise CorreoIgnorado(
                f"El estado de cuenta de {_mes_texto(mes, anio)} ya estaba cargado "
                f"({timezone.localtime(existente.created_at):%d/%m/%Y}). No se cambió.")
        estado = existente
        estado.archivo.save(nombre, ContentFile(contenido), save=False)
        estado.formato = formato
        estado.save(update_fields=['archivo', 'formato', 'updated_at'])
    else:
        estado = EstadoCuentaBancario.objects.create(
            cuenta_bancaria=cuenta, banco='BBVA', periodo_mes=mes, periodo_anio=anio, formato=formato,
            origen='CORREO', archivo=ContentFile(contenido, name=nombre))
    bitacora.estado_cuenta = estado
    try:
        procesar_estado_cuenta(estado)
    except Exception as e:
        logger.exception("Estado de cuenta por correo: falló procesar_estado_cuenta (estado %s)", estado.pk)
        raise ErrorCorreo("El estado de cuenta se guardó pero no se pudo procesar; se reintentará solo.") from e
    estado.refresh_from_db()
    bitacora.advertencia = _advertencia_continuidad(estado)
    _cadena_post_proceso(estado)
    return estado


def _advertencia_continuidad(estado):
    """Saldos que no encadenan con el mes anterior. No bloquea: puede faltar un mes."""
    mes, anio = (12, estado.periodo_anio - 1) if estado.periodo_mes == 1 else (estado.periodo_mes - 1, estado.periodo_anio)
    previo = EstadoCuentaBancario.objects.filter(
        cuenta_bancaria=estado.cuenta_bancaria, periodo_mes=mes, periodo_anio=anio).first()
    if (previo and previo.saldo_final_estado is not None and estado.saldo_inicial_estado is not None
            and previo.saldo_final_estado != estado.saldo_inicial_estado):
        return (f"El saldo inicial no coincide con el saldo final del estado de {_mes_texto(mes, anio)}: "
                "puede faltar un mes o haber un movimiento fuera de los estados cargados.")
    return ''


# Punto de enganche de la cadena posterior (Issue #401, ajuste 4). Hoy
# `procesar_estado_cuenta()` ya corrió las reglas existentes (emparejar,
# CFDI, reglas por texto). Las capas nuevas —identificación de abonos por
# folio en el concepto (fase 2), gastos sin factura (fase 4) y preguntas al
# contador— se agregan aquí como `callable(estado_cuenta)`, en ese orden.
CADENA_POST_PROCESO = []


def _cadena_post_proceso(estado):
    for capa in CADENA_POST_PROCESO:
        try:
            capa(estado)
        except Exception:
            logger.exception("Estado de cuenta por correo: falló la capa %s", getattr(capa, '__name__', capa))


def procesar_correo(bitacora):
    """Procesa una bitácora PENDIENTE o con ERROR. Reutilizable por el reproceso."""
    if bitacora.resultado not in ('PENDIENTE', 'ERROR') or bitacora.intentos >= CorreoEstadoCuenta.MAX_INTENTOS:
        return bitacora
    # Reserva optimista: si el hilo del webhook y el comando coinciden, gana uno solo.
    if not CorreoEstadoCuenta.objects.filter(pk=bitacora.pk, intentos=bitacora.intentos).update(
            intentos=F('intentos') + 1):
        return bitacora
    bitacora.refresh_from_db()
    try:
        estado = _procesar(bitacora)
    except ErrorCorreo as e:
        bitacora.resultado, bitacora.detalle = e.resultado, str(e)[:500]
    except Exception:
        logger.exception("Estado de cuenta por correo: error inesperado (bitácora %s)", bitacora.pk)
        bitacora.resultado, bitacora.detalle = 'ERROR', "Error inesperado al procesar el correo."
    else:
        bitacora.resultado, bitacora.detalle = 'PROCESADO', ''
        bitacora.estado_cuenta = estado
    bitacora.save()
    _avisar(bitacora)
    return bitacora


# ─── Avisos ────────────────────────────────────────────────────────────────

def _telefonos_aviso():
    from comunicacion.services_responsables import telefonos_aviso
    return list(dict.fromkeys(telefonos_aviso('DIRECCION') + telefonos_aviso('CONTADOR')))


def _pendiente_por_clave(clave):
    from comunicacion.models import Pendiente
    return Pendiente.objects.filter(clave_idempotencia=clave, estado='ABIERTO').first()


def _avisar(bitacora):
    """Éxito → WhatsApp; error o liga → Pendiente URGENTE. Nunca lanza."""
    try:
        from comunicacion import services_pendientes as pend
        clave = f'estado_correo:{bitacora.pk}'
        if bitacora.resultado == 'PROCESADO':
            abierto = _pendiente_por_clave(clave)
            if abierto is not None:
                pend.resolver(abierto)
            estado = bitacora.estado_cuenta
            total = estado.movimientos.count()
            sin = estado.movimientos.filter(movimiento_contable__isnull=True).count()
            texto = (f"Estado de cuenta BBVA de {_mes_texto(estado.periodo_mes, estado.periodo_anio)} cargado: "
                     f"{total} movimientos, {sin} sin conciliar.")
            if bitacora.advertencia:
                texto += " Revisa la advertencia en Correos."
            for telefono in _telefonos_aviso():
                pend._enviar_aviso(telefono, texto, clave=f'{clave}:ok:{telefono}')
        elif bitacora.resultado == 'ERROR' or (bitacora.resultado == 'IGNORADO' and not bitacora.estado_cuenta_id):
            hubo_mas = bitacora.resultado == 'ERROR' and bitacora.intentos < CorreoEstadoCuenta.MAX_INTENTOS
            sufijo = " Se reintentará solo." if hubo_mas else " Ya no se reintentará: revísalo en Correos."
            pend.registrar_pendiente(
                titulo="Estado de cuenta BBVA por correo: no se pudo cargar", rol='DIRECCION', tipo='OTRO',
                severidad='URGENTE', detalle=f"{bitacora.detalle}{sufijo}", objeto=bitacora,
                clave_idempotencia=clave)
    except Exception:
        logger.exception("Estado de cuenta por correo: no se pudo avisar")


def avisar_si_no_llego(hoy=None):
    """Desde el día 7 del mes, una vez por mes y cuenta: aún no hay estado de cuenta del mes anterior."""
    from comunicacion.services_pendientes import registrar_pendiente
    hoy = hoy or timezone.localdate()
    if hoy.day < DIA_AVISO_NO_LLEGO:
        return 0
    mes, anio = (12, hoy.year - 1) if hoy.month == 1 else (hoy.month - 1, hoy.year)
    avisos = 0
    cuentas = CuentaBancaria.objects.filter(activa=True, banco__icontains='bbva').exclude(numero_cuenta='')
    for cuenta in cuentas:
        if EstadoCuentaBancario.objects.filter(cuenta_bancaria=cuenta, periodo_mes=mes, periodo_anio=anio).exists():
            continue
        pendiente = registrar_pendiente(
            titulo=f"No ha llegado el estado de cuenta BBVA de {_mes_texto(mes, anio)}", rol='DIRECCION',
            tipo='OTRO', severidad='URGENTE',
            detalle="Debió llegar por correo el día 1 o 2. Revisa el filtro de Gmail y la bitácora de Correos; "
                    "si no aparece, descárgalo de BBVA y súbelo a mano.",
            clave_idempotencia=f'estado_no_llego:{cuenta.pk}:{anio}-{mes:02d}')
        avisos += 1 if pendiente else 0
    return avisos


def reprocesar_pendientes(aplicar=False):
    """Bitácoras PENDIENTE o ERROR con intentos disponibles. Devuelve (candidatas, procesadas_ok)."""
    candidatas = list(CorreoEstadoCuenta.objects.filter(
        resultado__in=('PENDIENTE', 'ERROR'), intentos__lt=CorreoEstadoCuenta.MAX_INTENTOS).order_by('recibido_en'))
    if not aplicar:
        return len(candidatas), 0
    ok = 0
    for bitacora in candidatas:
        if procesar_correo(bitacora).resultado == 'PROCESADO':
            ok += 1
    return len(candidatas), ok
