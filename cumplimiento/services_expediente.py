"""
Expediente para inspección (Issue #403, fase 4): PDF resumen y ZIP con los
documentos oficiales vigentes, para cuando llegue Protección Civil o el
Ayuntamiento. Solo entra lo vigente (Vigente o Por vencer): un permiso
vencido no se entrega como si sirviera.
"""
import io
import logging
import zipfile
from pathlib import Path

from django.utils.text import slugify

from core_erp.documentos import nombre_archivo, render_pdf

from . import services

logger = logging.getLogger(__name__)

# Tope del ZIP armado en memoria; si se rebasa, se pide un expediente más chico
MAX_BYTES_ZIP = 100 * 1024 * 1024
ESTADOS_VIGENTES = (services.VIGENTE, services.POR_VENCER)


class ExpedienteDemasiadoGrande(Exception):
    pass


def pdf_expediente(request=None, hoy=None):
    """(bytes, nombre) del PDF «Expediente de cumplimiento»."""
    resumen = services.resumen_tablero(hoy)
    contexto = {**resumen, 'titulo': 'Expediente de cumplimiento'}
    return (render_pdf('cumplimiento/pdf_expediente.html', contexto, request=request),
            nombre_archivo('Cumplimiento', resumen['hoy']))


def _nombre_en_zip(*partes, nombre_original):
    base = '_'.join(slugify(str(p)) for p in partes if p)
    return f'{base}{Path(nombre_original).suffix.lower()}'


def _leer(campo):
    try:
        with campo.open('rb') as f:
            return f.read()
    except (FileNotFoundError, OSError, ValueError):
        return None


def armar_zip(request=None, hoy=None, incluir_requisitos=False):
    """Devuelve (bytes, nombre, omitidos): el ZIP con el expediente en PDF, el
    documento oficial de cada vigencia vigente y, opcionalmente, los
    documentos entregados. `omitidos` lista lo que no se pudo leer del storage."""
    resumen = services.resumen_tablero(hoy)
    vigentes = [f for f in resumen['filas'] if f['estado'] in ESTADOS_VIGENTES and f['vigencia']
                and f['vigencia'].documento]

    total = 0
    for f in vigentes:
        try:
            total += f['vigencia'].documento.size
        except (FileNotFoundError, OSError):
            continue
        if incluir_requisitos:
            for d in f['vigencia'].documentos.all():
                if d.activo and d.archivo:
                    try:
                        total += d.archivo.size
                    except (FileNotFoundError, OSError):
                        continue
    if total > MAX_BYTES_ZIP:
        raise ExpedienteDemasiadoGrande(total)

    pdf, nombre_pdf = pdf_expediente(request, hoy)
    omitidos = []
    buffer = io.BytesIO()
    # Los PDF ya vienen comprimidos: almacenar evita CPU sin ganar tamaño
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_STORED) as zf:
        zf.writestr(nombre_pdf, pdf)
        for f in vigentes:
            v, t = f['vigencia'], f['tramite']
            contenido = _leer(v.documento)
            nombre = _nombre_en_zip(t.nombre, v.folio, v.fecha_vencimiento, nombre_original=v.documento.name)
            if contenido is None:
                omitidos.append(nombre)
                continue
            zf.writestr(nombre, contenido)
            if incluir_requisitos:
                for d in v.documentos.all():
                    if not (d.activo and d.archivo):
                        continue
                    contenido = _leer(d.archivo)
                    ruta = 'requisitos/' + _nombre_en_zip(t.nombre, d.nombre, nombre_original=d.archivo.name)
                    if contenido is None:
                        omitidos.append(ruta)
                    else:
                        zf.writestr(ruta, contenido)
        if omitidos:
            zf.writestr('LEEME.txt', 'No se pudieron leer del almacenamiento:\n' + '\n'.join(omitidos))
    return buffer.getvalue(), nombre_archivo('Cumplimiento', resumen['hoy'], extension='zip'), omitidos
