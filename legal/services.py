"""Capa de servicio del módulo legal. Las vistas no tocan los modelos directamente."""

import logging

from django.db import transaction

from .models import (
    AceptacionLegal, DocumentoLegal, Finalidad, TipoDocumento,
)

logger = logging.getLogger(__name__)

# Documentos que toda aceptación de cliente debe incluir obligatoriamente.
DOCUMENTOS_OBLIGATORIOS = (
    TipoDocumento.AVISO_PRIVACIDAD,
    TipoDocumento.TERMINOS,
    TipoDocumento.POLITICA_CANCELACION,
)


class LegalService:

    @staticmethod
    def documento_vigente(tipo: str):
        return DocumentoLegal.objects.filter(tipo=tipo, vigente=True).first()

    @staticmethod
    def documentos_vigentes(tipos=DOCUMENTOS_OBLIGATORIOS):
        return list(DocumentoLegal.objects.filter(tipo__in=tipos, vigente=True))

    @staticmethod
    def obtener_ip(request):
        """
        Railway pone un proxy delante de Django: la IP real viene en
        X-Forwarded-For. Se toma el PRIMER valor de la cadena, que es el
        cliente; el resto son los proxies intermedios.
        """
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')

    @classmethod
    @transaction.atomic
    def registrar_aceptacion(cls, *, request, correo, origen, cliente=None,
                             finalidades_aceptadas=None):
        """
        Registra evidencia de consentimiento.

        Congela un snapshot (tipo, versión, hash) de cada documento vigente,
        para que la evidencia sobreviva a futuras versiones de los documentos.
        """
        aceptadas = list(finalidades_aceptadas or [])
        documentos = cls.documentos_vigentes()

        faltantes = set(DOCUMENTOS_OBLIGATORIOS) - {d.tipo for d in documentos}
        if faltantes:
            raise RuntimeError(
                f"No hay versión vigente para: {', '.join(sorted(faltantes))}. "
                "Ejecute 'python manage.py seed_documentos_legales'."
            )

        opcionales = set(
            Finalidad.objects.filter(activa=True, requiere_consentimiento=True)
            .values_list('clave', flat=True)
        )
        rechazadas = sorted(opcionales - set(aceptadas))

        aceptacion = AceptacionLegal.objects.create(
            cliente=cliente,
            correo=correo,
            origen=origen,
            ip=cls.obtener_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000],
            finalidades_aceptadas=sorted(set(aceptadas) & opcionales),
            finalidades_rechazadas=rechazadas,
            snapshot_documentos=[
                {'tipo': d.tipo, 'version': d.version, 'hash': d.hash_contenido}
                for d in documentos
            ],
        )
        aceptacion.documentos.set(documentos)

        # Sin PII en logs: solo identificadores internos.
        logger.info("Aceptación legal registrada id=%s origen=%s", aceptacion.pk, origen)
        return aceptacion

    @staticmethod
    def cliente_acepto(cliente, clave_finalidad: str) -> bool:
        """
        ¿La aceptación más reciente del cliente incluye esta finalidad?

        Filtrar con esto ANTES de enviar cualquier campaña o de publicar fotos
        de un evento. Sin ese filtro, el registro de consentimiento no sirve.
        """
        ultima = (
            AceptacionLegal.objects.filter(cliente=cliente)
            .order_by('-aceptado_en')
            .values_list('finalidades_aceptadas', flat=True)
            .first()
        )
        return clave_finalidad in (ultima or [])
