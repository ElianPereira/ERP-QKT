"""
Formato de los avisos de WhatsApp al equipo (pendientes, visitas, llegadas).

Antes cada aviso era un renglón corrido («URGENTE: título. detalle Abrir: url»)
que, fuera de la ventana de 24 h, viajaba aplanado dentro de la plantilla
«Tienes un aviso nuevo: {{1}}». `Aviso` separa las partes para armar:

- con la ventana abierta (texto libre o botones), un mensaje en bloques:
  encabezado, título en negritas, datos en viñetas y el enlace en su renglón;
- sin ventana, los 4 parámetros de `META_WA_PLANTILLA_AVISO_EQUIPO`
  (encabezado, título, datos, enlace; texto en `docs/whatsapp_plantilla_aviso_equipo.md`),
  porque Meta no admite saltos de línea dentro de un parámetro. Sin esa
  plantilla se cae a la de operaciones de siempre, con un solo parámetro.
"""
from dataclasses import dataclass, field

from .services import texto_plano_wa

MARCA_URGENTE = '🔴'
MARCA_NORMAL = '🔵'
LIMITE_PARAMETRO = 900


@dataclass
class Aviso:
    titulo: str
    categoria: str = 'Aviso'
    urgente: bool = False
    lineas: list = field(default_factory=list)
    enlace: str = ''
    cierre: str = ''

    @property
    def encabezado(self) -> str:
        marca = MARCA_URGENTE if self.urgente else MARCA_NORMAL
        nivel = 'Urgente' if self.urgente else 'Aviso'
        return f'{marca} {nivel} · {self.categoria}' if self.categoria != 'Aviso' else f'{marca} {nivel}'

    def _datos(self) -> list:
        return [str(linea).strip() for linea in self.lineas if str(linea or '').strip()]

    def texto(self) -> str:
        """Mensaje en bloques para texto libre o botones (máx. 1024 en botones)."""
        bloques = [f'{self.encabezado}\n*{self.titulo.strip()}*']
        datos = self._datos()
        if datos:
            bloques.append('\n'.join(f'• {d}' for d in datos))
        if self.cierre:
            bloques.append(self.cierre.strip())
        if self.enlace:
            bloques.append(f'Abrir en el ERP:\n{self.enlace}')
        return '\n\n'.join(bloques)

    def parametros(self) -> list:
        """Los 4 parámetros de la plantilla del equipo, ya en una línea cada uno."""
        datos = ' · '.join(self._datos() + ([self.cierre] if self.cierre else []))
        return [texto_plano_wa(v)[:LIMITE_PARAMETRO] for v in
                (self.encabezado, self.titulo, datos or '—', self.enlace or '—')]

    def plano(self) -> str:
        """Una sola línea para la plantilla de operaciones de un parámetro."""
        partes = [self.encabezado, self.titulo] + self._datos() + ([self.cierre] if self.cierre else [])
        if self.enlace:
            partes.append(f'Abrir: {self.enlace}')
        return texto_plano_wa(' · '.join(partes))[:LIMITE_PARAMETRO]


def bloques(*parrafos: str, datos=(), enlace: str = '', pregunta: str = '') -> str:
    """Mensaje fijo al cliente (texto libre) en bloques, la misma regla que sigue
    el prompt de Kooxi: párrafos cortos separados por una línea en blanco, viñetas
    «• » desde 3 datos (con menos van en renglones sueltos), el enlace solo en su
    renglón y una sola pregunta o paso al final. El orden es párrafos, datos,
    enlace y pregunta; lo vacío se omite. Las negritas van en el texto que se pasa."""
    partes = [str(p).strip() for p in parrafos if str(p or '').strip()]
    filas = [str(d).strip() for d in datos if str(d or '').strip()]
    if filas:
        partes.append('\n'.join(f'• {d}' for d in filas) if len(filas) >= 3 else '\n'.join(filas))
    if enlace:
        partes.append(enlace.strip())
    if pregunta:
        partes.append(pregunta.strip())
    return '\n\n'.join(partes)
