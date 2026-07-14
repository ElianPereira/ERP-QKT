"""
Carga de estados de cuenta BBVA y conciliación preliminar
===========================================================
Parser de PDF calibrado y validado contra dos estados de cuenta reales de
Elián (Libretón Básico y Maestra PYME): los totales extraídos (cargos,
abonos, conteo de movimientos, saldo inicial y saldo final) coinciden
exactamente con los totales que el propio PDF imprime en su sección
"Total de Movimientos".

BBVA no delimita la tabla de movimientos con bordes, así que no se puede
usar page.extract_tables(). La estrategia real que funciona:
1. Ubicar las columnas (OPER, LIQ, DESCRIPCION, REFERENCIA, CARGOS, ABONOS,
   SALDO OPERACION, SALDO LIQUIDACION) leyendo la posición X del encabezado
   en la primera página — la posición varía unos pixeles entre los dos
   formatos de BBVA, por eso se calcula por documento y no se hardcodea.
2. Agrupar palabras en renglones visuales por coordenada Y con tolerancia.
3. Un renglón es "inicio de movimiento" solo si tiene fecha (patrón DD/MES)
   en las columnas OPER y LIQ simultáneamente. Los renglones siguientes sin
   fecha son continuación de la descripción (RFC, referencia, nombre del
   ordenante).
4. El monto de cada renglón de inicio se clasifica como CARGO o ABONO según
   en qué bin de columna cae su coordenada X — nunca por posición/orden en
   la lista, porque cargo y abono son mutuamente excluyentes y su presencia
   o ausencia desplaza cuál "número de la fila" es cuál.
"""
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from django.db import transaction
from django.db.models import Sum
from .models import EstadoCuentaBancario, MovimientoEstadoCuenta, MovimientoContable, ConciliacionBancaria

FECHA_RE = re.compile(r'^\d{2}/[A-Z]{3}$')  # ej. 15/FEB
MESES = {
    'ENE': 1, 'FEB': 2, 'MAR': 3, 'ABR': 4, 'MAY': 5, 'JUN': 6,
    'JUL': 7, 'AGO': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DIC': 12,
}
FOOTER_MARCADORES = (
    'BBVA MEXICO, S.A.',
    'AV. PASEO DE LA REFORMA',
    'LE INFORMAMOS QUE PUEDE CONSULTAR',
    'CUIDA EL MEDIO AMBIENTE',
    'LA GAT REAL ES EL RENDIMIENTO',
)


def procesar_estado_cuenta(estado_cuenta: EstadoCuentaBancario):
    """
    Extrae movimientos del archivo (PDF o XML) y los guarda como MovimientoEstadoCuenta.
    Reprocesable: si ya existían movimientos de una corrida anterior, se reemplazan.

    Valida que el número de cuenta impreso en el PDF coincida con el
    CuentaBancaria.numero_cuenta seleccionado — esto es lo que evita cargar por
    error el estado de cuenta de una persona contra la cuenta bancaria de otra
    (ej. la cuenta de Ruby contra la cuenta de Elián), que es justo el tipo de
    error que este módulo existe para prevenir.
    """
    try:
        if estado_cuenta.formato == 'PDF':
            movimientos, saldo_inicial, saldo_final, numero_cuenta_pdf, fecha_corte_real = _parsear_pdf_bbva(estado_cuenta.archivo.path)
        elif estado_cuenta.formato == 'XML':
            movimientos, saldo_inicial, saldo_final, numero_cuenta_pdf, fecha_corte_real = _parsear_xml_bbva(estado_cuenta.archivo.path)
        else:
            raise ValueError(f"Formato no soportado: {estado_cuenta.formato}")

        numero_esperado = (estado_cuenta.cuenta_bancaria.numero_cuenta or '').strip()
        if numero_esperado and numero_cuenta_pdf and numero_esperado != numero_cuenta_pdf.strip():
            raise ValueError(
                f"El número de cuenta del PDF ({numero_cuenta_pdf}) no coincide con "
                f"el de la CuentaBancaria seleccionada ({numero_esperado}: "
                f"{estado_cuenta.cuenta_bancaria}). Verifica que estás subiendo el "
                f"estado de cuenta correcto para esta cuenta."
            )
    except Exception as e:
        estado_cuenta.estado = 'ERROR'
        estado_cuenta.error_detalle = str(e)
        estado_cuenta.save(update_fields=['estado', 'error_detalle'])
        raise

    with transaction.atomic():
        MovimientoEstadoCuenta.objects.filter(estado_cuenta=estado_cuenta).delete()
        for mov in movimientos:
            MovimientoEstadoCuenta.objects.create(estado_cuenta=estado_cuenta, **mov)
        estado_cuenta.saldo_inicial_estado = saldo_inicial
        estado_cuenta.saldo_final_estado = saldo_final
        estado_cuenta.fecha_corte_real = fecha_corte_real
        estado_cuenta.estado = 'PROCESADO'
        estado_cuenta.error_detalle = ''
        estado_cuenta.save(update_fields=['saldo_inicial_estado', 'saldo_final_estado', 'fecha_corte_real', 'estado', 'error_detalle'])

    _emparejar_automaticamente(estado_cuenta)
    return estado_cuenta


def _to_decimal(texto):
    texto = texto.replace(',', '').strip()
    try:
        return Decimal(texto)
    except InvalidOperation:
        return None


def _es_pie_de_pagina(texto):
    t = texto.upper()
    return any(m in t for m in FOOTER_MARCADORES)


def _agrupar_por_fila(words, tolerancia=2.0):
    """Agrupa palabras en renglones visuales por coordenada 'top', con tolerancia."""
    words_sorted = sorted(words, key=lambda w: w['top'])
    filas, fila_actual, top_actual = [], [], None
    for w in words_sorted:
        if top_actual is None or abs(w['top'] - top_actual) <= tolerancia:
            fila_actual.append(w)
            top_actual = w['top'] if top_actual is None else top_actual
        else:
            filas.append(fila_actual)
            fila_actual, top_actual = [w], w['top']
    if fila_actual:
        filas.append(fila_actual)
    return filas


def _localizar_columnas(page):
    """
    Ubica los límites X de cada columna a partir del encabezado real de la tabla
    de movimientos. Las posiciones varían entre formatos de BBVA (Libretón Básico
    vs Maestra PYME), así que se calculan por documento, nunca se hardcodean.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    header = {}
    for w in words:
        t = w['text'].upper()
        if t in ('OPER', 'LIQ', 'CARGOS', 'ABONOS'):
            header[t] = w
        elif t in ('DESCRIPCION', 'DESCRIPCIÓN'):
            header['DESCRIPCION'] = w
        elif t == 'REFERENCIA':
            header['REFERENCIA'] = w
        elif t in ('OPERACION', 'OPERACIÓN'):
            header['OPERACION'] = w
        elif t in ('LIQUIDACION', 'LIQUIDACIÓN') and w['top'] > 500:
            header['LIQUIDACION'] = w

    requeridas = ('OPER', 'LIQ', 'DESCRIPCION', 'REFERENCIA', 'CARGOS', 'ABONOS', 'OPERACION', 'LIQUIDACION')
    faltantes = [r for r in requeridas if r not in header]
    if faltantes:
        raise ValueError(f"No se pudieron localizar las columnas {faltantes} del encabezado de movimientos.")

    oper_x0, liq_x0 = header['OPER']['x0'], header['LIQ']['x0']
    desc_x0 = header['DESCRIPCION']['x0']
    cargos_x0, abonos_x0 = header['CARGOS']['x0'], header['ABONOS']['x0']
    saldo_op_x0, saldo_liq_x0 = header['OPERACION']['x0'], header['LIQUIDACION']['x0']

    return {
        'cargo': (cargos_x0 - 15, (cargos_x0 + abonos_x0) / 2),
        'abono': ((cargos_x0 + abonos_x0) / 2, (abonos_x0 + saldo_op_x0) / 2),
        'saldo_operacion': ((abonos_x0 + saldo_op_x0) / 2, (saldo_op_x0 + saldo_liq_x0) / 2),
        'saldo_liquidacion': ((saldo_op_x0 + saldo_liq_x0) / 2, saldo_liq_x0 + 80),
        'oper_date_max_x': (oper_x0 + liq_x0) / 2,
        'liq_date_max_x': (liq_x0 + desc_x0) / 2,
        'descripcion_min_x': desc_x0 - 5,
    }


def _clasificar_monto(x0, columnas):
    for nombre in ('cargo', 'abono', 'saldo_operacion', 'saldo_liquidacion'):
        lo, hi = columnas[nombre]
        if lo <= x0 < hi:
            return nombre
    return None


def _parsear_pdf_bbva(ruta_archivo):
    """
    Extrae movimientos de un estado de cuenta BBVA en PDF (Libretón Básico o
    Maestra PYME — mismo layout de columnas en ambos, se generaliza sin cambios).

    Devuelve: (lista_de_dicts, saldo_inicial: Decimal, saldo_final: Decimal, numero_cuenta: str, fecha_corte_real: date)
    Cada dict trae las claves que espera MovimientoEstadoCuenta: fecha, descripcion,
    referencia, cargo, abono, saldo_parcial.
    """
    import pdfplumber

    movimientos = []
    saldo_inicial = saldo_final = None
    numero_cuenta = None
    fecha_corte_real = None
    anio_referencia = None

    with pdfplumber.open(ruta_archivo) as pdf:
        texto_p1 = pdf.pages[0].extract_text()
        m_periodo = re.search(r'AL (\d{2})/(\d{2})/(\d{4})', texto_p1)
        if m_periodo:
            anio_referencia = int(m_periodo.group(3))
        m_saldo_ant = re.search(r'Saldo Anterior\s*\n?\s*([\d,]+\.\d{2})', texto_p1)
        if m_saldo_ant:
            saldo_inicial = _to_decimal(m_saldo_ant.group(1))
        m_saldo_fin = re.search(r'Saldo Final[^\d\n]*([\d,]+\.\d{2})', texto_p1)
        if m_saldo_fin:
            saldo_final = _to_decimal(m_saldo_fin.group(1))
        m_num_cuenta = re.search(r'No\. de Cuenta\s*\n?\s*(\d+)', texto_p1)
        if m_num_cuenta:
            numero_cuenta = m_num_cuenta.group(1)
        m_fecha_corte = re.search(r'Fecha de Corte\s*\n?\s*(\d{2})/(\d{2})/(\d{4})', texto_p1)
        if m_fecha_corte:
            dia_c, mes_c, anio_c = m_fecha_corte.groups()
            fecha_corte_real = date(int(anio_c), int(mes_c), int(dia_c))

        if not anio_referencia:
            raise ValueError("No se pudo determinar el año del periodo del estado de cuenta.")
        if not fecha_corte_real:
            raise ValueError(
                "No se pudo extraer la 'Fecha de Corte' del PDF. Es obligatoria — la "
                "conciliación depende de ella, no se asume fin de mes calendario."
            )

        columnas = _localizar_columnas(pdf.pages[0])
        ref_re = re.compile(r'Referencia\s+([^\s]+(?:\s+\d+)?)', re.IGNORECASE)

        for page in pdf.pages:
            texto_pagina = page.extract_text() or ''
            if 'Glosario de Abreviaturas' in texto_pagina:
                break  # páginas legales/glosario: no hay más movimientos

            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            filas = _agrupar_por_fila(words, tolerancia=2.0)

            mov_actual = None
            fin_de_tabla = False
            for fila in filas:
                if fin_de_tabla:
                    break
                fila = sorted(fila, key=lambda w: w['x0'])
                texto_fila_completo = ' '.join(w['text'] for w in fila)
                if _es_pie_de_pagina(texto_fila_completo) or 'Total de Movimientos' in texto_fila_completo:
                    fin_de_tabla = True
                    continue

                oper_w = next((w for w in fila if w['x0'] < columnas['oper_date_max_x'] and FECHA_RE.match(w['text'])), None)
                liq_w = next((w for w in fila if columnas['oper_date_max_x'] <= w['x0'] < columnas['liq_date_max_x'] and FECHA_RE.match(w['text'])), None)

                if oper_w and liq_w:
                    if mov_actual:
                        movimientos.append(mov_actual)
                    desc_palabras = [w['text'] for w in fila if columnas['descripcion_min_x'] <= w['x0'] < columnas['cargo'][0]]
                    mov_actual = {
                        'fecha_txt': oper_w['text'], 'descripcion': ' '.join(desc_palabras),
                        'cargo': Decimal('0.00'), 'abono': Decimal('0.00'), 'saldo_parcial': None,
                    }
                    for w in fila:
                        if w['x0'] < columnas['descripcion_min_x']:
                            continue
                        val = _to_decimal(w['text'])
                        if val is None:
                            continue
                        col = _clasificar_monto(w['x0'], columnas)
                        if col == 'cargo':
                            mov_actual['cargo'] = val
                        elif col == 'abono':
                            mov_actual['abono'] = val
                        elif col in ('saldo_liquidacion', 'saldo_operacion') and mov_actual['saldo_parcial'] is None:
                            mov_actual['saldo_parcial'] = val
                elif mov_actual is not None:
                    desc_parte = ' '.join(w['text'] for w in fila if columnas['descripcion_min_x'] <= w['x0'] < columnas['cargo'][0])
                    if desc_parte:
                        mov_actual['descripcion'] += ' ' + desc_parte

            if mov_actual:
                movimientos.append(mov_actual)

    resultado = []
    for mv in movimientos:
        dia, mes_txt = mv['fecha_txt'].split('/')
        mes = MESES.get(mes_txt.upper())
        try:
            fecha = date(anio_referencia, mes, int(dia))
        except (ValueError, TypeError):
            fecha = None
        desc = mv['descripcion'].strip()
        m_ref = ref_re.search(desc)
        resultado.append({
            'fecha': fecha,
            'descripcion': desc,
            'referencia': m_ref.group(1) if m_ref else '',
            'cargo': mv['cargo'],
            'abono': mv['abono'],
            'saldo_parcial': mv['saldo_parcial'],
        })

    return resultado, saldo_inicial, saldo_final, numero_cuenta, fecha_corte_real


def _parsear_xml_bbva(ruta_archivo):
    """
    BBVA no ofreció exportación XML de movimientos al momento de este brief —
    solo PDF. Si más adelante aparece esa opción en banca en línea, calibrar
    este parser contra un XML real antes de usarlo, con el mismo criterio que
    _parsear_pdf_bbva: nunca adivinar la estructura.
    """
    raise NotImplementedError(
        "Parser de XML BBVA no implementado: no había un XML real disponible para "
        "calibrar al momento de este brief. Por ahora, todos los estados de cuenta "
        "se cargan en PDF."
    )


def _emparejar_automaticamente(estado_cuenta: EstadoCuentaBancario, tolerancia_dias=5):
    """
    Empareja cada MovimientoEstadoCuenta sin emparejar contra un MovimientoContable
    de la misma cuenta bancaria, mismo monto exacto (Decimal) y fecha dentro de
    +/- tolerancia_dias. Un MovimientoContable solo puede quedar emparejado una vez.

    Vista del banco -> vista de libros:
        abono (entra dinero al banco)  <-> MovimientoContable.debe  (aumenta Bancos)
        cargo (sale dinero del banco)  <-> MovimientoContable.haber (disminuye Bancos)

    Marca match_automatico=True pero confirmado=False siempre — la confirmación
    la da el usuario en el admin, nunca se auto-confirma.
    """
    cuenta_contable = estado_cuenta.cuenta_bancaria.cuenta_contable
    if not cuenta_contable:
        return

    ya_emparejados_ids = set(
        MovimientoEstadoCuenta.objects.filter(
            estado_cuenta__cuenta_bancaria=estado_cuenta.cuenta_bancaria,
            movimiento_contable__isnull=False,
        ).values_list('movimiento_contable_id', flat=True)
    )

    pendientes = estado_cuenta.movimientos.filter(movimiento_contable__isnull=True)

    for mov_banco in pendientes:
        rango_inicio = mov_banco.fecha - timedelta(days=tolerancia_dias)
        rango_fin = mov_banco.fecha + timedelta(days=tolerancia_dias)

        candidatos = MovimientoContable.objects.filter(
            cuenta=cuenta_contable,
            poliza__estado='APLICADA',
            poliza__fecha__gte=rango_inicio,
            poliza__fecha__lte=rango_fin,
        ).exclude(id__in=ya_emparejados_ids)

        if mov_banco.abono > 0:
            candidatos = candidatos.filter(debe=mov_banco.abono)
        elif mov_banco.cargo > 0:
            candidatos = candidatos.filter(haber=mov_banco.cargo)
        else:
            continue

        match = candidatos.order_by(
            # Preferir la fecha más cercana al movimiento del banco
        ).first()
        if match:
            mov_banco.movimiento_contable = match
            mov_banco.match_automatico = True
            mov_banco.confirmado = False
            mov_banco.save(update_fields=['movimiento_contable', 'match_automatico', 'confirmado'])
            ya_emparejados_ids.add(match.id)


def generar_conciliacion_preliminar(estado_cuenta: EstadoCuentaBancario, usuario=None):
    """
    Crea o actualiza la ConciliacionBancaria del periodo con los datos ya
    procesados del estado de cuenta. No marca la conciliación como CONCILIADA
    automáticamente — eso lo hace el usuario después de revisar/confirmar los
    emparejamientos en el admin.

    Usa fecha_corte_real (la que imprime el propio banco), NUNCA el fin del mes
    calendario de periodo_mes/periodo_anio — distintas cuentas BBVA cortan en
    días distintos del mes (ej. Libretón Básico corta el día 14, Maestra PYME
    corta fin de mes). Forzar fin de mes calendario para todas produciría una
    diferencia que no es un error real, solo una comparación de fechas distintas.
    """
    if estado_cuenta.estado != 'PROCESADO':
        raise ValueError("El estado de cuenta debe estar en estado PROCESADO antes de generar la conciliación.")
    if not estado_cuenta.fecha_corte_real:
        raise ValueError("El estado de cuenta no tiene fecha_corte_real — vuelve a procesarlo.")

    saldo_libros = estado_cuenta.cuenta_bancaria.saldo_a_fecha(estado_cuenta.fecha_corte_real)

    no_emparejados = estado_cuenta.movimientos.filter(movimiento_contable__isnull=True)
    abonos_banco_no_registrados = no_emparejados.aggregate(t=Sum('abono'))['t'] or Decimal('0.00')
    cargos_banco_no_registrados = no_emparejados.aggregate(t=Sum('cargo'))['t'] or Decimal('0.00')

    conciliacion, _ = ConciliacionBancaria.objects.update_or_create(
        cuenta_bancaria=estado_cuenta.cuenta_bancaria,
        mes=estado_cuenta.periodo_mes,
        anio=estado_cuenta.periodo_anio,
        defaults=dict(
            saldo_segun_banco=estado_cuenta.saldo_final_estado or Decimal('0.00'),
            saldo_segun_libros=saldo_libros,
            cargos_banco_no_registrados=cargos_banco_no_registrados,
            abonos_banco_no_registrados=abonos_banco_no_registrados,
            estado='EN_PROCESO',
        )
    )
    estado_cuenta.conciliacion = conciliacion
    estado_cuenta.save(update_fields=['conciliacion'])
    return conciliacion
