"""
Manual de procedimientos (Issue #507): carga, filtrado por rol y render.

Fuente única: los archivos `manual/secciones/NN_nombre.md`. Cada uno declara en
su cabecera para quién es. El rol se deriva SIEMPRE del usuario autenticado
(`roles_de`); ninguna vista lo toma de un parámetro de la URL.

Formato de una sección::

    ---
    titulo: Registrar un cobro
    capitulo: Cobros
    orden: 420
    roles: [ENCARGADO, DIRECCION]
    ---
    Texto en Markdown. Los números del negocio se insertan con etiquetas
    (`{{ dias_pago_total.EVENTO }}`) que leen la fuente real.

    :::rol DIRECCION          (solo lo ve ese rol; el superusuario ve todo)
    ...
    :::
    :::correcto | :::nohagas | :::quehacer Título | :::nota

Una sección con `[CONFIRMAR: …]` es borrador: solo la ve el superusuario.
"""
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import markdown
import nh3
from django.conf import settings
from django.contrib.staticfiles.storage import staticfiles_storage
from django.template import engines
from django.utils.html import strip_tags

from core_erp.documentos import ruta_estatica

DIRECTORIO = Path(__file__).resolve().parent / 'secciones'
IMAGENES = Path(settings.BASE_DIR) / 'static' / 'manual' / 'img'

ROLES = ('DIRECCION', 'ENCARGADO', 'VENTAS', 'CONTADOR', 'OPERACION')
TODOS = 'TODOS'
ROLES_VALIDOS = ROLES + (TODOS,)
ETIQUETAS_ROL = {
    'DIRECCION': 'Dirección', 'ENCARGADO': 'Encargado', 'VENTAS': 'Ventas',
    'CONTADOR': 'Contador', 'OPERACION': 'Operación', 'TODOS': 'Todos',
}
# Grupo de Django -> rol del manual (los demás staff solo reciben TODOS).
GRUPO_A_ROL = {'Vendedor': 'VENTAS', 'Ventas': 'ENCARGADO', 'Contabilidad': 'CONTADOR'}
# Manuales que el superusuario puede descargar para quien no tiene usuario.
SELECTOR_PDF = ('COMPLETO', 'ENCARGADO', 'VENTAS', 'CONTADOR', 'OPERACION')

MARCADOR = re.compile(r'\[CONFIRMAR\s*:[^\]]*\]', re.IGNORECASE)
_APERTURA = re.compile(r'^:::(rol|correcto|nohagas|quehacer|nota)\b\s*(.*?)\s*$')
_COMPONENTES = {
    'correcto': ('manual-bloque manual-bloque--correcto', 'Correcto'),
    'nohagas': ('manual-bloque manual-bloque--nohagas', 'No hagas esto'),
    'quehacer': ('manual-bloque manual-bloque--quehacer', 'Qué hacer si'),
    'nota': ('manual-bloque manual-bloque--nota', 'Nota'),
}
_SLUG = re.compile(r'^[a-z0-9][a-z0-9-]*$')


class ErrorSeccion(ValueError):
    """Sección con cabecera o formato inválido (la detecta el guardián de tests)."""


@dataclass(frozen=True)
class Seccion:
    slug: str
    titulo: str
    capitulo: str
    orden: int
    roles: tuple
    cuerpo: str
    pendientes: tuple
    archivo: str

    @property
    def borrador(self):
        return bool(self.pendientes)


# ==========================================
# CARGA
# ==========================================

def _cabecera(texto, archivo):
    if not texto.startswith('---\n'):
        raise ErrorSeccion(f'{archivo}: falta la cabecera `---`.')
    try:
        fin = texto.index('\n---\n', 4)
    except ValueError:
        raise ErrorSeccion(f'{archivo}: la cabecera no se cierra con `---`.') from None
    datos = {}
    for linea in texto[4:fin].splitlines():
        if not linea.strip():
            continue
        if ':' not in linea:
            raise ErrorSeccion(f'{archivo}: línea de cabecera inválida «{linea}».')
        clave, valor = linea.split(':', 1)
        datos[clave.strip()] = valor.strip()
    return datos, texto[fin + 5:].lstrip('\n')


def _leer(ruta: Path) -> Seccion:
    archivo = ruta.name
    datos, cuerpo = _cabecera(ruta.read_text(encoding='utf-8'), archivo)
    for campo in ('titulo', 'capitulo', 'orden', 'roles'):
        if not datos.get(campo):
            raise ErrorSeccion(f'{archivo}: falta `{campo}` en la cabecera.')
    try:
        orden = int(datos['orden'])
    except ValueError:
        raise ErrorSeccion(f'{archivo}: `orden` debe ser un número.') from None
    crudo = datos['roles']
    if not (crudo.startswith('[') and crudo.endswith(']')):
        raise ErrorSeccion(f'{archivo}: `roles` debe ir como [A, B].')
    roles = tuple(r.strip() for r in crudo[1:-1].split(',') if r.strip())
    desconocidos = [r for r in roles if r not in ROLES_VALIDOS]
    if not roles or desconocidos:
        raise ErrorSeccion(f'{archivo}: roles inválidos {desconocidos or "(vacío)"}. Válidos: {ROLES_VALIDOS}.')
    slug = re.sub(r'^\d+[_-]', '', ruta.stem).replace('_', '-').lower()
    if not _SLUG.match(slug):
        raise ErrorSeccion(f'{archivo}: nombre de archivo inválido para la URL.')
    return Seccion(slug=slug, titulo=datos['titulo'], capitulo=datos['capitulo'], orden=orden,
                   roles=roles, cuerpo=cuerpo, pendientes=tuple(MARCADOR.findall(cuerpo)), archivo=archivo)


def cargar_secciones():
    """Todas las secciones del repositorio, ordenadas por `orden` (lee los archivos)."""
    secciones = [_leer(r) for r in sorted(DIRECTORIO.glob('*.md'))]
    vistos = {}
    for s in secciones:
        if s.slug in vistos:
            raise ErrorSeccion(f'{s.archivo}: el nombre repite el de {vistos[s.slug]}.')
        vistos[s.slug] = s.archivo
    return sorted(secciones, key=lambda s: (s.orden, s.slug))


# ==========================================
# QUIÉN VE QUÉ
# ==========================================

def roles_de(user) -> frozenset:
    """Roles del manual de un usuario, derivados de él y de nadie más."""
    if not (user and user.is_authenticated and user.is_active and user.is_staff):
        return frozenset()
    if user.is_superuser:
        return frozenset(ROLES_VALIDOS)
    roles = {TODOS}
    for nombre in user.groups.values_list('name', flat=True):
        if nombre in GRUPO_A_ROL:
            roles.add(GRUPO_A_ROL[nombre])
    return frozenset(roles)


def etiqueta_de_roles(roles) -> str:
    """Nombre corto del manual que recibe alguien con esos roles (`VENTAS`, `TODOS`…)."""
    if set(ROLES) <= set(roles):
        return 'COMPLETO'
    propios = [r for r in ROLES if r in roles]
    return '+'.join(propios) if propios else TODOS


def secciones_para(roles, *, borradores=False, secciones=None):
    """Secciones que alguien con `roles` puede leer. Los borradores solo si se piden."""
    roles = set(roles)
    visibles = []
    for s in (cargar_secciones() if secciones is None else secciones):
        if s.borrador and not borradores:
            continue
        if roles & set(s.roles):
            visibles.append(s)
    return visibles


def secciones_visibles(user):
    """Lo que `user` ve en pantalla (el superusuario ve además los borradores)."""
    return secciones_para(roles_de(user), borradores=user.is_superuser)


def capitulos(secciones):
    """[(capítulo, [secciones])] en el orden de la primera sección de cada uno."""
    orden, grupos = [], {}
    for s in secciones:
        if s.capitulo not in grupos:
            grupos[s.capitulo] = []
            orden.append(s.capitulo)
        grupos[s.capitulo].append(s)
    return [(c, grupos[c]) for c in orden]


def anterior_y_siguiente(seccion, visibles):
    indice = next((i for i, s in enumerate(visibles) if s.slug == seccion.slug), None)
    if indice is None:
        return None, None
    return (visibles[indice - 1] if indice else None,
            visibles[indice + 1] if indice + 1 < len(visibles) else None)


# ==========================================
# VALORES DEL NEGOCIO (nunca escritos a mano en el Markdown)
# ==========================================

def _sin_ceros(valor):
    """`Decimal('50.00')` -> `50`; `Decimal('37.5')` -> `37.5`."""
    return int(valor) if valor == valor.to_integral_value() else valor.normalize()


def contexto_negocio() -> dict:
    """Valores que el texto inserta con `{{ … }}`; se resuelven al usarse."""
    from comercial import reglas_contrato
    from comercial.models import Cotizacion
    from comunicacion.models import HorarioVisita

    def horarios_visita():
        filas = HorarioVisita.objects.filter(activo=True)
        return [f'{h.get_dia_semana_display()} de {h.hora_inicio:%H:%M} a {h.hora_fin:%H:%M}' for h in filas]

    return {
        'dias_pago_total': Cotizacion.DIAS_PAGO_TOTAL,
        'porcentaje_anticipo': lambda: _sin_ceros(Cotizacion().porcentaje_anticipo_confirmacion()),
        'tabla_cancelacion': reglas_contrato.TABLA_CANCELACION,
        'horarios_visita': horarios_visita,
        'sesion_minutos': settings.ERP_SESION_INACTIVIDAD_SEGUNDOS // 60,
        'login_ventana_minutos': settings.ERP_ADMIN_LOGIN_VENTANA_SEGUNDOS // 60,
        'login_max_usuario': settings.ERP_ADMIN_LOGIN_MAX_USUARIO,
    }


# ==========================================
# RENDER
# ==========================================

def _filtrar_por_rol(texto, roles):
    """Quita los bloques `:::rol X` que `roles` no debe ver; deja intactos los demás."""
    salida, pila = [], []   # un elemento por bloque abierto: (tipo, visible)
    for linea in texto.splitlines():
        apertura = _APERTURA.match(linea)
        if apertura:
            tipo, argumento = apertura.groups()
            if tipo == 'rol':
                pedidos = {r.strip() for r in argumento.split(',') if r.strip()}
                if not pedidos or pedidos - set(ROLES_VALIDOS):
                    raise ErrorSeccion(f'Bloque :::rol con roles inválidos: {argumento!r}.')
                pila.append((tipo, bool(pedidos & set(roles))))
                continue
            pila.append((tipo, True))
        elif linea.strip() == ':::':
            if not pila:
                raise ErrorSeccion('`:::` de cierre sin bloque abierto.')
            if pila.pop()[0] == 'rol':
                continue
        if all(visible for _, visible in pila):
            salida.append(linea)
    if pila:
        raise ErrorSeccion('Hay un bloque `:::` sin cerrar.')
    return '\n'.join(salida)


def _md(texto):
    return markdown.markdown(texto, extensions=['tables', 'sane_lists', 'attr_list'], output_format='html')


def _a_html(texto):
    """Markdown con los componentes `:::tipo` convertidos en bloques con clase."""
    piezas, plano, interior, tipo, titulo = [], [], [], None, ''
    for linea in texto.splitlines():
        apertura = _APERTURA.match(linea)
        if tipo is None and apertura and apertura.group(1) in _COMPONENTES:
            if plano:
                piezas.append(_md('\n'.join(plano)))
                plano = []
            tipo, titulo = apertura.group(1), apertura.group(2)
        elif tipo is not None and linea.strip() == ':::':
            clase, rotulo = _COMPONENTES[tipo]
            cabecera = titulo or rotulo
            piezas.append(f'<div class="{clase}"><p class="manual-bloque__titulo">{nh3.clean(cabecera, tags=set())}</p>'
                          f'{_md(chr(10).join(interior))}</div>')
            tipo, interior, titulo = None, [], ''
        elif tipo is not None:
            interior.append(linea)
        else:
            plano.append(linea)
    if tipo is not None:
        raise ErrorSeccion('Hay un componente `:::` sin cerrar.')
    if plano:
        piezas.append(_md('\n'.join(plano)))
    return ''.join(piezas)


_TAGS = nh3.ALLOWED_TAGS | {'div', 'span', 'img', 'figure', 'figcaption', 'mark'}
_ATRIBUTOS = {**{k: set(v) for k, v in nh3.ALLOWED_ATTRIBUTES.items()},
              'div': {'class'}, 'span': {'class'}, 'p': {'class'}, 'ol': {'class'}, 'ul': {'class'},
              'img': {'src', 'alt', 'class', 'loading'}, 'mark': {'class'}}
_IMG = re.compile(r'src="(?!https?:|/|data:)([^"]+)"')
_ENLACE_INTERNO = re.compile(r'href="\.\./([a-z0-9-]+)/"')
_ENCABEZADO = re.compile(r'<(/?)h([1-6])\b')


def _con_imagenes(html, *, para_pdf):
    def reemplazo(m):
        relativa = m.group(1)
        if para_pdf:
            return f'src="{ruta_estatica("manual/" + relativa)}"'
        return f'src="{staticfiles_storage.url("manual/" + relativa)}"'
    return _IMG.sub(reemplazo, html)


def _bajar_titulos(html, niveles):
    if not niveles:
        return html
    return _ENCABEZADO.sub(lambda m: f'<{m.group(1)}h{min(6, int(m.group(2)) + niveles)}', html)


def render_cuerpo(seccion, roles, *, para_pdf=False, bajar_titulos=0):
    """HTML ya saneado del cuerpo de `seccion`, solo con lo que `roles` debe ver."""
    plantilla = engines['django'].from_string(seccion.cuerpo)
    texto = plantilla.render(contexto_negocio())
    texto = _filtrar_por_rol(texto, roles)
    html = _a_html(texto)
    html = nh3.clean(html, tags=_TAGS, attributes=_ATRIBUTOS, link_rel='noopener noreferrer')
    html = MARCADOR.sub(lambda m: f'<mark class="manual-pendiente">{m.group(0)}</mark>', html)
    if para_pdf:   # en el PDF, el enlace a otra sección salta a su ancla
        html = _ENLACE_INTERNO.sub(lambda m: f'href="#s-{m.group(1)}"', html)
    return _con_imagenes(_bajar_titulos(html, bajar_titulos), para_pdf=para_pdf)


def enlaces_de(seccion):
    """Slugs de otras secciones que el cuerpo enlaza con `[texto](../slug/)` (para el guardián)."""
    return re.findall(r'\]\(\.\./([a-z0-9-]+)/\)', seccion.cuerpo)


def imagenes_de(seccion):
    """Rutas `img/…` que el cuerpo referencia (para el guardián)."""
    return re.findall(r'!\[[^\]]*\]\(([^)\s]+)\)', seccion.cuerpo)


# ==========================================
# BÚSQUEDA
# ==========================================

def _normal(texto):
    texto = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in texto if not unicodedata.combining(c)).lower()


def buscar(consulta, secciones, roles):
    """[(sección, fragmento)] de las `secciones` donde aparecen todas las palabras."""
    palabras = [p for p in _normal(consulta).split() if len(p) > 1]
    if not palabras:
        return []
    resultados = []
    for s in secciones:
        plano = re.sub(r'\s+', ' ', strip_tags(render_cuerpo(s, roles))).strip()
        corpus = _normal(f'{s.titulo} {s.capitulo} {plano}')
        if not all(p in corpus for p in palabras):
            continue
        posicion = max(_normal(plano).find(palabras[0]), 0)
        inicio = max(posicion - 50, 0)
        resultados.append((s, ('…' if inicio else '') + plano[inicio:inicio + 160] + '…'))
    return resultados


# ==========================================
# VERSIÓN Y PDF
# ==========================================

def version_manual() -> str:
    return (getattr(settings, 'STATIC_VERSION', '') or '')[:7] or 'desarrollo'


def roles_para_pdf(user, seleccion=''):
    """(roles, borradores) del PDF que `user` descarga.

    Un usuario normal recibe siempre el suyo: `seleccion` solo la respeta un
    superusuario, que además es el único cuyo «Completo» incluye borradores.
    """
    if not user.is_superuser:
        return roles_de(user), False
    seleccion = (seleccion or 'COMPLETO').upper()
    if seleccion not in SELECTOR_PDF:
        raise ValueError(seleccion)
    if seleccion == 'COMPLETO':
        return frozenset(ROLES_VALIDOS), True
    return frozenset({seleccion, TODOS}), False


def css_cadena(texto: str) -> str:
    """Texto seguro para un `content: "…"` de CSS dentro de un `<style>`."""
    limpio = re.sub(r'[\r\n<>]', ' ', str(texto)).replace('\\', '\\\\').replace('"', '\\"')
    return limpio
