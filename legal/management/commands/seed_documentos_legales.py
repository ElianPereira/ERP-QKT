"""
Carga inicial de documentos legales y del catálogo de finalidades.

Idempotente: si ya existe (tipo, versión) compara el hash y no hace nada.

    python manage.py seed_documentos_legales
    python manage.py seed_documentos_legales --publicar   # marca vigente=True

Un documento con marcadores [CONFIRMAR:] o [PENDIENTE:] se carga pero NO se
publica: saldrían tal cual a la vista del cliente.
"""

import re
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from legal.models import DocumentoLegal, Finalidad, TipoDocumento

DIRECTORIO = Path(__file__).resolve().parent.parent.parent / 'documentos_iniciales'

# archivo -> (tipo, título)
DOCUMENTOS = {
    'aviso_privacidad_v2.0.md': (TipoDocumento.AVISO_PRIVACIDAD, 'Aviso de Privacidad'),
    'aviso_simplificado_v2.0.md': (TipoDocumento.AVISO_SIMPLIFICADO,
                                   'Aviso de Privacidad Simplificado'),
    'terminos_v2.0.md': (TipoDocumento.TERMINOS, 'Términos y Condiciones'),
    'politica_cancelacion_v2.0.md': (TipoDocumento.POLITICA_CANCELACION,
                                     'Política de Cancelación y Reembolso'),
    'reglamento_v1.0.md': (TipoDocumento.REGLAMENTO, 'Reglamento Interno'),
}

FINALIDADES = [
    ('OPERACION',  'Cotización, contratación y ejecución del servicio', False, 10),
    ('PAGOS',      'Procesamiento y conciliación de pagos',             False, 20),
    ('FISCAL',     'Emisión de CFDI y cumplimiento fiscal',             False, 30),
    ('SEGURIDAD',  'Seguridad de personas e instalaciones',             False, 40),
    ('MARKETING',  'Promociones y disponibilidad de fechas',            True,  50),
    ('USO_IMAGEN', 'Uso de fotografías y videos con fines promocionales', True, 60),
    ('ENCUESTAS',  'Encuestas de satisfacción',                         True,  70),
]


class Command(BaseCommand):
    help = "Carga los documentos legales iniciales y el catálogo de finalidades."

    def add_arguments(self, parser):
        parser.add_argument(
            '--publicar', action='store_true',
            help='Marca como vigentes los documentos que no tengan marcadores pendientes.',
        )

    @transaction.atomic
    def handle(self, *args, **opciones):
        self._finalidades()
        self.stdout.write('')
        self._documentos(publicar=opciones['publicar'])

    def _finalidades(self):
        self.stdout.write(self.style.MIGRATE_HEADING('Catálogo de finalidades'))
        for clave, nombre, requiere, orden in FINALIDADES:
            obj, creada = Finalidad.objects.update_or_create(
                clave=clave,
                defaults={'nombre': nombre, 'requiere_consentimiento': requiere,
                          'orden': orden, 'activa': True},
            )
            marca = 'creada' if creada else 'actualizada'
            consent = 'requiere consentimiento' if requiere else 'sin consentimiento'
            self.stdout.write(f'  {clave:<12} {marca:<12} ({consent})')

    def _documentos(self, *, publicar):
        self.stdout.write(self.style.MIGRATE_HEADING('Documentos legales'))
        if not DIRECTORIO.exists():
            self.stdout.write(self.style.ERROR(f'  No existe {DIRECTORIO}'))
            return

        for archivo, (tipo, titulo) in DOCUMENTOS.items():
            ruta = DIRECTORIO / archivo
            if not ruta.exists():
                self.stdout.write(f'  {archivo:<34} (no existe, se omite)')
                continue

            contenido = ruta.read_text(encoding='utf-8')
            version = self._version_desde_nombre(archivo)
            pendientes = DocumentoLegal(contenido_md=contenido).marcadores_pendientes()

            doc = DocumentoLegal.objects.filter(tipo=tipo, version=version).first()
            if doc:
                if doc.hash_contenido == DocumentoLegal.calcular_hash(contenido):
                    estado = 'sin cambios'
                else:
                    estado = self.style.WARNING(
                        'EL ARCHIVO CAMBIÓ — cree una versión nueva, el contenido '
                        'publicado es inmutable'
                    )
                self.stdout.write(f'  {archivo:<34} v{version} {estado}')
            else:
                doc = DocumentoLegal(
                    tipo=tipo, version=version, titulo=titulo,
                    contenido_md=contenido, vigente_desde=date.today(),
                )
                doc.save()
                self.stdout.write(f'  {archivo:<34} v{version} creado')

            if pendientes:
                self.stdout.write(self.style.WARNING(
                    f'      {len(pendientes)} marcador(es) [CONFIRMAR:]/[PENDIENTE:] '
                    'sin resolver — NO se publica'
                ))
            elif doc.vigente:
                self.stdout.write('      ya vigente')
            elif publicar:
                # Solo se publica si NO hay ninguna versión vigente de ese tipo.
                # El comando corre en cada deploy, y sin esta condición una
                # versión más nueva publicada desde el admin (v3.0) quedaría
                # degradada al volver a marcar vigente la v2.0 del repositorio.
                otra = DocumentoLegal.objects.filter(tipo=tipo, vigente=True).first()
                if otra:
                    self.stdout.write(
                        f'      no se publica: ya está vigente la v{otra.version}'
                    )
                else:
                    doc.vigente = True
                    doc.save()
                    self.stdout.write(self.style.SUCCESS('      publicado como vigente'))

    @staticmethod
    def _version_desde_nombre(archivo: str) -> str:
        m = re.search(r'_v([\d.]+)\.md$', archivo)
        return m.group(1) if m else '1.0'
