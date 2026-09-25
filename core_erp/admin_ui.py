"""
Componentes visuales compartidos del admin (Issue #322).

Fuente única del HTML que pintan las columnas de `list_display`: badges de
estado, montos, botones por fila y el menú "más acciones". Todo sale con
clases de `static/css/qkt_ui.css`, nunca con `style=` en línea — así un mismo
estado (pagado, aplicada, confirmada) se ve idéntico en todo el ERP y un
cambio de diseño se hace en un solo lugar.

Los textos se escapan siempre (`format_html`/`format_html_join`); ningún
helper recibe HTML crudo salvo `acciones()`, que solo concatena la salida
segura de los demás helpers.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

# Los cinco tonos semánticos de la guía visual. Ningún badge usa otro color.
EXITO = 'exito'
ALERTA = 'alerta'
ERROR = 'error'
INFO = 'info'
NEUTRO = 'neutro'
TONOS = (EXITO, ALERTA, ERROR, INFO, NEUTRO)


def badge(texto, tono=NEUTRO, *, categoria=False):
    """Etiqueta de estado. `categoria=True` quita el punto de semáforo: es
    para clasificaciones (Evento, Ingreso/Egreso) que no son un estado y no
    deben competir visualmente con él."""
    if tono not in TONOS:
        tono = NEUTRO
    clase = f'qkt-badge qkt-badge--{tono}'
    if categoria:
        clase += ' qkt-badge--categoria'
    return format_html('<span class="{}">{}</span>', clase, texto)


def badge_por_valor(valor, tonos, etiqueta, *, categoria=False):
    """Badge de un campo con choices: `tonos` mapea el valor al tono;
    un valor no mapeado cae a neutro."""
    return badge(etiqueta, tonos.get(valor, NEUTRO), categoria=categoria)


def vacio():
    """Guion para celdas sin dato, en el mismo gris en todas las listas."""
    return mark_safe('<span class="qkt-vacio">—</span>')


def monto(valor, *, tono=None, sufijo=''):
    """Importe en pesos con 2 decimales, alineado a la derecha por la tabla.
    `Decimal` + ROUND_HALF_UP: nunca pasa por float."""
    if valor is None:
        return vacio()
    importe = Decimal(valor).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    clase = 'qkt-num'
    if tono in TONOS:
        clase += f' qkt-num--{tono}'
    signo = '-' if importe < 0 else ''
    return format_html('<span class="{}">{}${}{}</span>', clase, signo, f'{abs(importe):,.2f}', sufijo)


def avance(porcentaje):
    """Barra de avance de pago (0-100) con su porcentaje."""
    pct = max(0, min(100, int(round(float(porcentaje or 0)))))
    tono = EXITO if pct >= 100 else (ALERTA if pct > 0 else ERROR)
    return format_html(
        '<span class="qkt-avance qkt-avance--{}"><span class="qkt-avance__barra">'
        '<span style="width:{}%"></span></span><span class="qkt-avance__pct">{}%</span></span>',
        tono, pct, pct,
    )


def _atributos(nueva_pestana, confirmar):
    partes = []
    if nueva_pestana:
        partes.append(mark_safe(' target="_blank" rel="noopener"'))
    if confirmar:
        partes.append(format_html(' data-qkt-confirmar="{}"', confirmar))
    return format_html_join('', '{}', ((p,) for p in partes))


def boton(texto, url, tono='secundario', *, icono=None, nueva_pestana=False, confirmar=None, compacto=True):
    """Botón de texto. Tonos: primario, secundario, peligro, enlace."""
    clase = f'qkt-btn qkt-btn--{tono}'
    if compacto:
        clase += ' qkt-btn--sm'
    icono_html = format_html('<i class="fas fa-{}" aria-hidden="true"></i>', icono) if icono else ''
    return format_html(
        '<a href="{}" class="{}"{}>{}{}</a>',
        url, clase, _atributos(nueva_pestana, confirmar), icono_html, texto,
    )


def boton_icono(url, icono, titulo, *, nueva_pestana=False, confirmar=None):
    """Botón cuadrado de 26 px para acciones por fila; el texto va en el
    tooltip y en aria-label."""
    return format_html(
        '<a href="{}" class="qkt-btn qkt-btn--icono" title="{}" aria-label="{}"{}>'
        '<i class="fas fa-{}" aria-hidden="true"></i></a>',
        url, titulo, titulo, _atributos(nueva_pestana, confirmar), icono,
    )


def hueco_icono():
    """Espacio vacío del tamaño de un botón de icono, para que las columnas
    de acciones queden alineadas aunque una fila no tenga ese botón."""
    return mark_safe('<span class="qkt-btn qkt-btn--icono qkt-btn--hueco" aria-hidden="true"></span>')


def menu_acciones(items, *, titulo='Más acciones'):
    """Menú desplegable (`<details>`, sin JS) para acciones secundarias.

    `items` es una lista de dicts: `texto`, `url` y opcionales `icono`,
    `nueva_pestana`, `confirmar`, o `copiar` (texto que se copia al
    portapapeles en vez de navegar). Un item `{'separador': True}` pinta una
    línea divisoria.
    """
    filas = []
    for item in items:
        if item.get('separador'):
            filas.append(mark_safe('<hr class="qkt-menu__sep">'))
            continue
        icono = format_html('<i class="fas fa-{}" aria-hidden="true"></i>', item['icono']) if item.get('icono') else ''
        if item.get('copiar'):
            filas.append(format_html(
                '<button type="button" class="qkt-menu__item" data-qkt-copiar="{}">{}{}</button>',
                item['copiar'], icono, item['texto'],
            ))
        else:
            filas.append(format_html(
                '<a href="{}" class="qkt-menu__item"{}>{}{}</a>',
                item['url'], _atributos(item.get('nueva_pestana'), item.get('confirmar')), icono, item['texto'],
            ))
    if not filas:
        return hueco_icono()
    return format_html(
        '<details class="qkt-menu"><summary class="qkt-btn qkt-btn--icono" title="{}" aria-label="{}">'
        '<i class="fas fa-ellipsis" aria-hidden="true"></i></summary>'
        '<div class="qkt-menu__panel">{}</div></details>',
        titulo, titulo, format_html_join('', '{}', ((f,) for f in filas)),
    )


def acciones(*partes):
    """Fila de botones de una celda de acciones."""
    return format_html(
        '<div class="qkt-acciones">{}</div>',
        format_html_join('', '{}', ((p,) for p in partes if p)),
    )
