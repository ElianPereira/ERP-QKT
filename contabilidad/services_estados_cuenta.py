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
import io
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import combinations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from .models import ConciliacionBancaria, EstadoCuentaBancario, MovimientoContable, MovimientoEstadoCuenta

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

# Conceptos de comisión de banca por internet (SPEI/CECOBAN a bancos externos)
# que, según el Contrato de Banca en Línea BBVA (Cláusula Cuarta, "Comisiones
# y Gastos"), se cargan "a mes vencido": lo que aparece cobrado en el estado
# de cuenta de un mes corresponde al servicio prestado el mes anterior.
CONCEPTOS_COMISION_DIFERIDA = ('SERV BANCA INTERNET', 'COM SERV BCA INTERNET')


def _es_comision_diferida(descripcion: str) -> bool:
    d = (descripcion or '').upper()
    return any(concepto in d for concepto in CONCEPTOS_COMISION_DIFERIDA)


def _periodo_devengo_mes_anterior(fecha_corte: date) -> date:
    """Primer día del mes anterior al de fecha_corte (maneja cambio de año)."""
    primer_dia_mes_corte = fecha_corte.replace(day=1)
    return (primer_dia_mes_corte - timedelta(days=1)).replace(day=1)


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
        # El storage por defecto es Cloudflare R2 (S3): no implementa path(),
        # el archivo nunca existe en el disco del contenedor. Se lee por el
        # FieldFile y se pasa un buffer en memoria a los parsers.
        with estado_cuenta.archivo.open('rb') as fh:
            contenido = io.BytesIO(fh.read())

        if estado_cuenta.formato == 'PDF':
            movimientos, saldo_inicial, saldo_final, numero_cuenta_pdf, fecha_corte_real = _parsear_pdf_bbva(contenido)
        elif estado_cuenta.formato == 'XML':
            movimientos, saldo_inicial, saldo_final, numero_cuenta_pdf, fecha_corte_real = _parsear_xml_bbva(contenido)
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
            periodo_devengo = None
            if fecha_corte_real and _es_comision_diferida(mov.get('descripcion', '')):
                periodo_devengo = _periodo_devengo_mes_anterior(fecha_corte_real)
            MovimientoEstadoCuenta.objects.create(estado_cuenta=estado_cuenta, periodo_devengo=periodo_devengo, **mov)
        estado_cuenta.saldo_inicial_estado = saldo_inicial
        estado_cuenta.saldo_final_estado = saldo_final
        estado_cuenta.fecha_corte_real = fecha_corte_real
        estado_cuenta.estado = 'PROCESADO'
        estado_cuenta.error_detalle = ''
        estado_cuenta.save(update_fields=['saldo_inicial_estado', 'saldo_final_estado', 'fecha_corte_real', 'estado', 'error_detalle'])

    emparejar_y_asentar(estado_cuenta)
    return estado_cuenta


def emparejar_y_asentar(estado_cuenta, usuario=None):
    """
    Orden de precedencia (lo más documentado gana, nunca se asienta dos veces):
    1. Asiento que ya existe: pólizas BANCO aplicadas después, luego cualquier
       asiento de bancos por importe y fecha (Pago, Compra, manual).
    2. CFDI que llegó después de un asiento provisional por palabra clave: el
       provisional se cancela y lo sustituye la Compra (IVA correcto).
    3. CFDI cargado (carga masiva de XML) que calza con el cargo: ya marcado
       pagado desde esta cuenta (se liga su línea de bancos) o sin cuenta de
       pago (se completa y aplica su póliza).
    4. Reglas por texto del concepto o cuenta del tercero.
    Devuelve el resumen de `aplicar_reglas`.
    """
    from .services_reglas_banco import (
        aplicar_reglas,
        emparejar_compras_ya_pagadas,
        sustituir_asientos_provisionales_por_cfdi,
        usuario_sistema,
        vincular_polizas_propias,
    )

    usuario = usuario or usuario_sistema()
    vincular_polizas_propias(estado_cuenta)
    _emparejar_automaticamente(estado_cuenta)
    sustituir_asientos_provisionales_por_cfdi(estado_cuenta, usuario)
    emparejar_compras_ya_pagadas(estado_cuenta)
    aplicar_sugerencias_compras(estado_cuenta, usuario)
    return aplicar_reglas(estado_cuenta, usuario=usuario)


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


def _parsear_pdf_bbva(archivo):
    """
    Extrae movimientos de un estado de cuenta BBVA en PDF (Libretón Básico o
    Maestra PYME — mismo layout de columnas en ambos, se generaliza sin cambios).
    `archivo` puede ser una ruta o un objeto file-like abierto en binario;
    los tests con fixtures locales pasan rutas.

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

    with pdfplumber.open(archivo) as pdf:
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


def _parsear_xml_bbva(archivo):
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


# Pólizas cuya línea de bancos es un cobro que Openpay o la terminal depositan
# neto de comisión: un pago de la cotización o un depósito en garantía.
ORIGENES_COBRO = ('PAGO_CLIENTE', 'DEPOSITO_GARANTIA')


def comisiones_por_cobro(lineas_cobro):
    """
    {id de la línea de bancos de un cobro: [líneas de bancos de sus comisiones]}.

    Openpay y la terminal depositan el NETO: el cobro entra a bancos por el
    total y la comisión sale aparte (COMISION_OPENPAY, ligada a la
    OpenpayTransaccion; COMISION_TPV, ligada al Pago). El depósito del banco
    es la suma de ambas líneas. El cobro es la póliza PAGO_CLIENTE del Pago o,
    en un depósito en garantía, la DEPOSITO_GARANTIA de su recepción.
    """
    from django.contrib.contenttypes.models import ContentType

    from comercial.models import MovimientoDeposito, OpenpayTransaccion, Pago

    ct_pago = ContentType.objects.get_for_model(Pago)
    ct_deposito = ContentType.objects.get_for_model(MovimientoDeposito)
    ct_transaccion = ContentType.objects.get_for_model(OpenpayTransaccion)

    cobro_por_pago, cobro_por_deposito = {}, {}
    for linea in lineas_cobro:
        if linea.debe <= 0:
            continue
        if linea.poliza.origen == 'PAGO_CLIENTE' and linea.poliza.content_type_id == ct_pago.id:
            cobro_por_pago[linea.poliza.object_id] = linea
        elif linea.poliza.origen == 'DEPOSITO_GARANTIA' and linea.poliza.content_type_id == ct_deposito.id:
            cobro_por_deposito[linea.poliza.object_id] = linea
    if not cobro_por_pago and not cobro_por_deposito:
        return {}

    cobro_por_transaccion = {
        transaccion_id: cobro_por_pago[pago_id]
        for transaccion_id, pago_id in OpenpayTransaccion.objects.filter(
            pago_id__in=cobro_por_pago).values_list('pk', 'pago_id')
    }
    # Un Pago capturado a mano por un cargo de Openpay no queda ligado a su
    # transacción, pero lleva el openpay_id en la referencia.
    pago_por_referencia = {
        referencia: pago_id
        for pago_id, referencia in Pago.objects.filter(
            pk__in=cobro_por_pago).exclude(referencia='').values_list('pk', 'referencia')
    }
    cobro_por_transaccion.update({
        transaccion_id: cobro_por_pago[pago_por_referencia[openpay_id]]
        for transaccion_id, openpay_id in OpenpayTransaccion.objects.filter(
            pago__isnull=True, openpay_id__in=pago_por_referencia,
        ).values_list('pk', 'openpay_id')
    })
    cobro_por_transaccion.update({
        transaccion_id: cobro_por_deposito[mov_id]
        for mov_id, transaccion_id in MovimientoDeposito.objects.filter(
            pk__in=cobro_por_deposito, transaccion_openpay__isnull=False,
        ).values_list('pk', 'transaccion_openpay_id')
    })

    comisiones = MovimientoContable.objects.filter(
        haber__gt=0, poliza__estado='APLICADA',
    ).filter(
        Q(poliza__origen='COMISION_TPV', poliza__content_type=ct_pago,
          poliza__object_id__in=list(cobro_por_pago))
        | Q(poliza__origen='COMISION_OPENPAY', poliza__content_type=ct_transaccion,
            poliza__object_id__in=list(cobro_por_transaccion))
    ).select_related('poliza')

    resultado = {}
    for comision in comisiones:
        cobro = (
            cobro_por_pago[comision.poliza.object_id] if comision.poliza.origen == 'COMISION_TPV'
            else cobro_por_transaccion[comision.poliza.object_id]
        )
        if comision.cuenta_id == cobro.cuenta_id:
            resultado.setdefault(cobro.id, []).append(comision)
    return resultado


# Openpay junta en un solo depósito los cobros que liquida juntos y retiene los
# importes chicos hasta completar uno, así que un abono puede traer cobros de
# días o semanas distintos.
VENTANA_DEPOSITO_AGRUPADO_DIAS = 60
MAX_COBROS_POR_DEPOSITO = 12


def _neto(cobro, comisiones):
    return cobro.debe - sum(c.haber for c in comisiones.get(cobro.id, []))


def _cobros_por_depositar(cuenta_contable, fecha, excluidos, tolerancia_dias=5):
    """Cobros con comisión (Openpay, terminal; pagos y depósitos en garantía)
    sin depósito todavía que pudieron
    entrar en un abono de esa fecha, y {cobro: comisiones} de cada uno."""
    cobros = list(
        MovimientoContable.objects.filter(
            cuenta=cuenta_contable, debe__gt=0, poliza__estado='APLICADA',
            poliza__origen__in=ORIGENES_COBRO,
            poliza__fecha__gte=fecha - timedelta(days=VENTANA_DEPOSITO_AGRUPADO_DIAS),
            poliza__fecha__lte=fecha + timedelta(days=tolerancia_dias),
        ).exclude(id__in=excluidos).select_related('poliza')
    )
    comisiones = comisiones_por_cobro(cobros)
    sueltos = [
        c for c in cobros
        if c.id in comisiones and not any(x.id in excluidos for x in comisiones[c.id])
    ]
    return sueltos, comisiones


def _combinacion_unica(objetivo, elementos, valor, minimo=1):
    """La única combinación de `elementos` cuyos valores suman `objetivo`.
    None si no hay ninguna, si hay varias o si son demasiados para probarlas
    todas: no se adivina."""
    if len(elementos) > MAX_COBROS_POR_DEPOSITO:
        return None
    encontrados = []
    for tamano in range(minimo, len(elementos) + 1):
        for combinacion in combinations(elementos, tamano):
            if sum((valor(e) for e in combinacion), Decimal('0.00')) == objetivo:
                encontrados.append(combinacion)
                if len(encontrados) > 1:
                    return None
    return list(encontrados[0]) if encontrados else None


def _grupo_del_deposito(abono, cobros, comisiones, ancla=None):
    """
    La única combinación de cobros cuyo neto (cobro − comisiones) suma el
    abono, incluido `ancla` si se da (`_combinacion_unica`).
    """
    cobros = [c for c in cobros if ancla is None or c.id != ancla.id]
    objetivo = abono - (_neto(ancla, comisiones) if ancla is not None else 0)
    resto = _combinacion_unica(
        objetivo, cobros, lambda c: _neto(c, comisiones), minimo=0 if ancla is not None else 1)
    if resto is None:
        return None
    return ([ancla] if ancla is not None else []) + resto


# Líneas de bancos sin cobro detrás que también viajan dentro de un depósito de
# Openpay: comisiones (de este cobro o de un cargo sin Pago) y lo capturado a
# mano, como el ingreso de un cargo de prueba que no nació de una cotización.
ORIGENES_SUELTOS = ('COMISION_OPENPAY', 'COMISION_TPV', 'MANUAL')


def _lineas_sueltas(cuenta_contable, fecha, excluidos, tolerancia_dias=5):
    return list(
        MovimientoContable.objects.filter(
            cuenta=cuenta_contable, poliza__estado='APLICADA',
            poliza__origen__in=ORIGENES_SUELTOS,
            poliza__fecha__gte=fecha - timedelta(days=VENTANA_DEPOSITO_AGRUPADO_DIAS),
            poliza__fecha__lte=fecha + timedelta(days=tolerancia_dias),
        ).exclude(id__in=excluidos)
    )


def _ids_del_grupo(grupo, comisiones, ancla):
    """Lo que el depósito explica además del asiento ligado: sus comisiones y
    los otros cobros del grupo."""
    return {c.id for c in grupo if c.id != ancla.id} | {
        x.id for c in grupo for x in comisiones.get(c.id, [])
    }


def cobertura_por_deposito(cuenta_bancaria):
    """
    {id del movimiento del banco: ids de las líneas de bancos que explica
    además del asiento ligado}, aunque el modelo solo ligue uno por movimiento.

    Primero se buscan cobros: las comisiones del cobro ligado y, si Openpay
    juntó varios cobros en el depósito, los demás con sus comisiones. Si así no
    cuadra, la diferencia entre el abono y el cobro ligado se busca entre
    comisiones y pólizas manuales sin ligar (`ORIGENES_SUELTOS`). En los dos
    casos solo cuenta una combinación exacta y única; si no, todo sigue
    saliendo como partida en tránsito. Vale igual para lo emparejado a mano que
    para lo automático.
    """
    cuenta_contable = cuenta_bancaria.cuenta_contable
    if not cuenta_contable:
        return {}
    emparejados = MovimientoEstadoCuenta.objects.filter(
        estado_cuenta__cuenta_bancaria=cuenta_bancaria, movimiento_contable__isnull=False,
    )
    ocupados = set(emparejados.values_list('movimiento_contable_id', flat=True))
    cobertura, cubiertos = {}, set()
    for mov_banco in emparejados.filter(abono__gt=0).select_related(
        'movimiento_contable__poliza'
    ).order_by('fecha', 'id'):
        ancla = mov_banco.movimiento_contable
        if (ancla.debe == mov_banco.abono or ancla.cuenta_id != cuenta_contable.id
                or ancla.poliza.origen not in ORIGENES_COBRO):
            continue
        excluidos = ocupados | cubiertos
        sueltos, comisiones = _cobros_por_depositar(cuenta_contable, mov_banco.fecha, excluidos)
        comisiones.update(comisiones_por_cobro([ancla]))
        grupo = _grupo_del_deposito(mov_banco.abono, sueltos, comisiones, ancla)
        if grupo:
            ids = _ids_del_grupo(grupo, comisiones, ancla)
        else:
            lineas = _combinacion_unica(
                mov_banco.abono - ancla.debe,
                _lineas_sueltas(cuenta_contable, mov_banco.fecha, excluidos),
                lambda linea: linea.debe - linea.haber,
            )
            ids = {linea.id for linea in lineas or []}
        if ids:
            cobertura[mov_banco.id] = ids
            cubiertos |= ids
    return cobertura


def ids_cubiertos_por_depositos(cuenta_bancaria):
    """Todas las líneas de bancos que explica algún depósito (`cobertura_por_deposito`)."""
    return set().union(*cobertura_por_deposito(cuenta_bancaria).values())


def _emparejar_automaticamente(estado_cuenta: EstadoCuentaBancario, tolerancia_dias=5):
    """
    Empareja cada MovimientoEstadoCuenta sin emparejar contra un MovimientoContable
    de la misma cuenta bancaria, mismo monto exacto (Decimal) y fecha dentro de
    +/- tolerancia_dias. Un MovimientoContable solo puede quedar emparejado una vez.

    Vista del banco -> vista de libros:
        abono (entra dinero al banco)  <-> MovimientoContable.debe  (aumenta Bancos)
        cargo (sale dinero del banco)  <-> MovimientoContable.haber (disminuye Bancos)

    Un abono sin coincidencia exacta se intenta contra cobros de Openpay o
    terminal netos de su comisión (`comisiones_por_cobro`): uno solo o varios
    que Openpay haya juntado en el mismo depósito (`_grupo_del_deposito`).

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
    ) | ids_cubiertos_por_depositos(estado_cuenta.cuenta_bancaria)

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

        cubiertos = set()
        if mov_banco.abono > 0:
            match = candidatos.filter(debe=mov_banco.abono).first()
            if not match:
                sueltos, comisiones = _cobros_por_depositar(
                    cuenta_contable, mov_banco.fecha, ya_emparejados_ids, tolerancia_dias)
                grupo = _grupo_del_deposito(mov_banco.abono, sueltos, comisiones)
                if grupo:
                    # Se liga el cobro principal; el resto queda cubierto.
                    match = max(grupo, key=lambda c: (c.debe, c.poliza.fecha))
                    cubiertos = _ids_del_grupo(grupo, comisiones, match)
        elif mov_banco.cargo > 0:
            match = candidatos.filter(haber=mov_banco.cargo).first()
        else:
            continue

        if match:
            mov_banco.movimiento_contable = match
            mov_banco.match_automatico = True
            mov_banco.confirmado = False
            mov_banco.save(update_fields=['movimiento_contable', 'match_automatico', 'confirmado'])
            ya_emparejados_ids.add(match.id)
            ya_emparejados_ids.update(cubiertos)


def sugerir_compras_pendientes(estado_cuenta: EstadoCuentaBancario, tolerancia_dias=5):
    """
    Para los cargos del estado de cuenta que sigan sin emparejar después de
    _emparejar_automaticamente() (esa función solo busca en pólizas ya
    APLICADA), busca candidatas en comercial.Compra: BORRADOR, de la misma
    unidad de negocio que esta cuenta bancaria, con el mismo importe exacto
    y fecha de emisión dentro de +/- tolerancia_dias. Es el mismo criterio
    de tolerancia que ya usa _emparejar_automaticamente, para no tener dos
    definiciones distintas de "qué cuenta como match" en el módulo.

    No decide por sí sola en caso de ambigüedad: un cargo con más de una
    Compra candidata del mismo importe/fecha se marca como ambiguo (no
    adivinar cuál de varias, dejar la decisión a quien concilia).

    Devuelve {movimiento_estado_cuenta_id: {'candidata': Compra|None, 'ambiguas': [Compra,...]}}.
    Un cargo sin ninguna candidata simplemente no aparece en el dict.
    """
    from comercial.models import Compra

    unidad = estado_cuenta.cuenta_bancaria.unidad_negocio
    if not unidad:
        return {}

    pendientes = estado_cuenta.movimientos.filter(movimiento_contable__isnull=True, cargo__gt=0)

    sugerencias = {}
    for mov_banco in pendientes:
        candidatas = compras_candidatas(mov_banco, unidad, tolerancia_dias=tolerancia_dias)
        if len(candidatas) == 1:
            sugerencias[mov_banco.id] = {'candidata': candidatas[0], 'ambiguas': []}
        elif len(candidatas) > 1:
            sugerencias[mov_banco.id] = {'candidata': None, 'ambiguas': candidatas}
    return sugerencias


RFC_EN_CONCEPTO_RE = re.compile(r'RFC: ?([A-Z&]{3,4}) ?(\d{6}[A-Z0-9]{3})')
TOLERANCIA_DIAS_CON_RFC = 45


def compras_candidatas(mov_banco, unidad, tolerancia_dias=5, cuenta_bancaria=None):
    """
    Compras (CFDI cargado) que pueden ser este cargo del banco. Si BBVA
    imprimió el RFC del comercio (pagos con tarjeta), RFC + importe exacto
    identifican la factura aunque se haya pagado semanas después de emitida;
    sin RFC solo queda importe + fecha cercana, el criterio de siempre.

    Sin `cuenta_bancaria`: solo las que aún no tienen cuenta de pago (póliza
    en BORRADOR). Con `cuenta_bancaria`: también las que ya se marcaron
    pagadas desde esa cuenta —`Compra.save()` la asigna sola cuando la unidad
    tiene una única cuenta activa— y cuya línea de bancos todavía no está
    emparejada con ningún movimiento del banco.
    """
    from comercial.models import Compra

    # Openpay no se paga desde el banco: descuenta de los depósitos.
    base = Compra.objects.filter(unidad_negocio=unidad, total=mov_banco.cargo).exclude(
        proveedor__descuenta_de_depositos=True
    )
    if cuenta_bancaria is None:
        base = base.filter(cuenta_pago__isnull=True)
    else:
        base = base.filter(Q(cuenta_pago__isnull=True) | Q(cuenta_pago=cuenta_bancaria))

    rfc = RFC_EN_CONCEPTO_RE.search((mov_banco.descripcion or '').upper())
    if rfc:
        dias = timedelta(days=TOLERANCIA_DIAS_CON_RFC)
        base = base.filter(rfc_emisor__iexact=rfc.group(1) + rfc.group(2))
    else:
        dias = timedelta(days=tolerancia_dias)
    candidatas = base.filter(fecha_emision__gte=mov_banco.fecha - dias, fecha_emision__lte=mov_banco.fecha + dias)

    resultado = []
    for compra in candidatas:
        if compra.cuenta_pago_id:
            linea = linea_banco_de_compra(compra, cuenta_bancaria)
            if not linea or linea.movimientos_banco_emparejados.exists():
                continue
        resultado.append(compra)
    return resultado


def linea_banco_de_compra(compra, cuenta_bancaria):
    """Línea de bancos de la póliza APLICADA de una Compra ya pagada."""
    from django.contrib.contenttypes.models import ContentType

    from .models import Poliza

    if not cuenta_bancaria or not cuenta_bancaria.cuenta_contable_id:
        return None
    poliza = Poliza.objects.filter(
        content_type=ContentType.objects.get_for_model(compra), object_id=compra.pk,
        origen='COMPRA', estado='APLICADA',
    ).first()
    if not poliza:
        return None
    return poliza.movimientos.filter(cuenta_id=cuenta_bancaria.cuenta_contable_id, haber=compra.total).first()


def aplicar_sugerencias_compras(estado_cuenta: EstadoCuentaBancario, usuario, tolerancia_dias=5):
    """
    Aplica lo que propone sugerir_compras_pendientes(): para cada cargo con
    una única Compra candidata, le asigna cuenta_pago, completa su póliza
    BORRADOR con `contabilidad.services.completar_poliza_compra()` (ya
    existente — agrega el movimiento de banco que faltaba, sin duplicar
    nada si se corre dos veces), la aplica con `Poliza.aplicar(usuario)`, y
    liga el MovimientoContable de banco resultante al MovimientoEstadoCuenta
    correspondiente. Nunca marca confirmado=True: sigue exigiendo revisión
    humana individual en el inline de conciliación, igual que
    _emparejar_automaticamente().

    Si dos cargos distintos del mismo importe/fecha comparten como única
    candidata la misma Compra (posible porque sugerir_compras_pendientes
    evalúa cada cargo por separado, sin conocer a los demás), solo el primero
    en procesarse (por fecha, luego id) se queda con ella — el segundo se
    reporta como sin candidata en vez de reclamarla también, para no asignar
    la misma Compra a dos movimientos del banco.

    Devuelve (aplicadas, ambiguas, sin_candidata): listas de
    MovimientoEstadoCuenta, para el resumen que ve quien concilia.
    """
    from django.contrib.contenttypes.models import ContentType

    from comercial.models import Compra

    from .models import Poliza
    from .services import completar_poliza_compra

    sugerencias = sugerir_compras_pendientes(estado_cuenta, tolerancia_dias=tolerancia_dias)
    cuenta_bancaria = estado_cuenta.cuenta_bancaria
    content_type_compra = ContentType.objects.get_for_model(Compra)

    pendientes = list(
        estado_cuenta.movimientos
        .filter(movimiento_contable__isnull=True, cargo__gt=0)
        .order_by('fecha', 'id')
    )

    aplicadas, ambiguas, sin_candidata = [], [], []
    compras_asignadas = set()
    for mov_banco in pendientes:
        sugerencia = sugerencias.get(mov_banco.id)
        if not sugerencia:
            sin_candidata.append(mov_banco)
            continue
        if sugerencia['ambiguas']:
            ambiguas.append(mov_banco)
            continue

        compra = sugerencia['candidata']
        if compra.pk in compras_asignadas:
            sin_candidata.append(mov_banco)
            continue

        poliza = Poliza.objects.filter(
            content_type=content_type_compra, object_id=compra.pk,
            origen='COMPRA', estado='BORRADOR',
        ).first()
        if not poliza:
            sin_candidata.append(mov_banco)
            continue

        compra.cuenta_pago = cuenta_bancaria
        compra.save()
        compras_asignadas.add(compra.pk)

        try:
            completar_poliza_compra(poliza)
            poliza.aplicar(usuario)
        except (ValueError, ValidationError):
            sin_candidata.append(mov_banco)
            continue

        movimiento_banco = None
        if cuenta_bancaria.cuenta_contable:
            movimiento_banco = poliza.movimientos.filter(
                cuenta=cuenta_bancaria.cuenta_contable, haber=compra.total,
            ).first()

        if not movimiento_banco:
            sin_candidata.append(mov_banco)
            continue

        mov_banco.movimiento_contable = movimiento_banco
        mov_banco.match_automatico = True
        mov_banco.confirmado = False
        mov_banco.save(update_fields=['movimiento_contable', 'match_automatico', 'confirmado'])
        aplicadas.append(mov_banco)

    return aplicadas, ambiguas, sin_candidata


def movimientos_contables_del_periodo(estado_cuenta: EstadoCuentaBancario, fecha_inicio: date):
    """
    Renglones de póliza APLICADA que tocan la cuenta de bancos dentro del
    periodo del estado de cuenta y que NO están emparejados con ningún
    movimiento bancario de esa cuenta fechado hasta el corte (de este estado de
    cuenta o de uno anterior), ni cubiertos por un depósito así.

    Son las "partidas del lado de libros": lo que la contabilidad ya registró y
    el banco todavía no refleja.
    """
    cuenta_contable = estado_cuenta.cuenta_bancaria.cuenta_contable
    if not cuenta_contable:
        return MovimientoContable.objects.none()

    # Solo sale de las partidas lo que el banco liquidó hasta este corte: lo
    # que liquida un estado de cuenta posterior seguía en tránsito aquí.
    liquidados = MovimientoEstadoCuenta.objects.filter(
        estado_cuenta__cuenta_bancaria=estado_cuenta.cuenta_bancaria,
        movimiento_contable__isnull=False,
        fecha__lte=estado_cuenta.fecha_corte_real,
    )
    emparejados_ids = set(liquidados.values_list('movimiento_contable_id', flat=True))
    cobertura = cobertura_por_deposito(estado_cuenta.cuenta_bancaria)
    for mov_id in liquidados.filter(id__in=cobertura).values_list('id', flat=True):
        emparejados_ids |= cobertura[mov_id]

    return MovimientoContable.objects.filter(
        cuenta=cuenta_contable,
        poliza__estado='APLICADA',
        poliza__fecha__gte=fecha_inicio,
        poliza__fecha__lte=estado_cuenta.fecha_corte_real,
    ).exclude(id__in=emparejados_ids).select_related('poliza', 'cuenta')


def _en_transito_al_inicio(estado_cuenta, fecha_inicio):
    """Neto (debe − haber) de las líneas de bancos anteriores al periodo que
    liquidan movimientos de este estado de cuenta: ligadas directo o cubiertas
    por un depósito. Al primer día del periodo estaban en tránsito."""
    cobertura = cobertura_por_deposito(estado_cuenta.cuenta_bancaria)
    ids = set()
    for mov_id, linea_id in estado_cuenta.movimientos.filter(
        movimiento_contable__isnull=False,
    ).values_list('id', 'movimiento_contable_id'):
        ids.add(linea_id)
        ids |= cobertura.get(mov_id, set())
    totales = MovimientoContable.objects.filter(
        id__in=ids, poliza__fecha__lt=fecha_inicio,
    ).aggregate(debe=Sum('debe'), haber=Sum('haber'))
    return (totales['debe'] or Decimal('0.00')) - (totales['haber'] or Decimal('0.00'))


def analizar_conciliacion(estado_cuenta: EstadoCuentaBancario):
    """
    Calcula todas las cifras de la conciliación de un estado de cuenta, sin
    escribir nada. Es la fuente única del cálculo: la usan tanto
    `generar_conciliacion_preliminar()` como el comando `diagnosticar_conciliacion`.

    Usa fecha_corte_real (la que imprime el propio banco), NUNCA el fin del mes
    calendario de periodo_mes/periodo_anio — distintas cuentas BBVA cortan en
    días distintos del mes (ej. Libretón Básico corta el día 14, Maestra PYME
    corta fin de mes). Forzar fin de mes calendario para todas produciría una
    diferencia que no es un error real, solo una comparación de fechas distintas.

    La conciliación es DEL PERIODO: `saldo_a_fecha()` acumula desde el origen de
    la cuenta, así que compararlo a secas contra el saldo final de un solo estado
    de cuenta arrastra a la diferencia toda póliza anterior al primer estado de
    cuenta cargado. Por eso se ancla el inicio del periodo en
    `saldo_inicial_estado` —que el parser ya guardaba y nadie usaba— y lo que no
    cuadra ahí se aísla en `diferencia_arrastrada`.
    """
    if estado_cuenta.estado != 'PROCESADO':
        raise ValueError("El estado de cuenta debe estar en estado PROCESADO antes de generar la conciliación.")
    if not estado_cuenta.fecha_corte_real:
        raise ValueError("El estado de cuenta no tiene fecha_corte_real — vuelve a procesarlo.")

    cuenta_bancaria = estado_cuenta.cuenta_bancaria

    primer_movimiento = estado_cuenta.movimientos.order_by('fecha', 'id').first()
    fecha_inicio = (
        primer_movimiento.fecha if primer_movimiento
        else estado_cuenta.fecha_corte_real.replace(day=1)
    )

    saldo_libros_inicio = cuenta_bancaria.saldo_a_fecha(fecha_inicio - timedelta(days=1))
    en_transito_al_inicio = _en_transito_al_inicio(estado_cuenta, fecha_inicio)
    saldo_libros_corte = cuenta_bancaria.saldo_a_fecha(estado_cuenta.fecha_corte_real)

    saldo_inicial_banco = estado_cuenta.saldo_inicial_estado
    if saldo_inicial_banco is None:
        # Estado de cuenta procesado por una versión anterior del parser: sin
        # ancla no se puede separar el arrastre, y meterlo todo en `diferencia`
        # es justo el comportamiento que este cálculo existe para evitar.
        raise ValueError(
            "El estado de cuenta no tiene saldo inicial extraído del PDF — "
            "vuelve a procesarlo para poder separar la diferencia arrastrada."
        )
    # Lo asentado antes del periodo que el banco liquidó dentro de él era una
    # partida en tránsito al inicio, no descuadre heredado.
    diferencia_arrastrada = saldo_libros_inicio - en_transito_al_inicio - saldo_inicial_banco

    # Lado del banco: movimientos del estado de cuenta sin asiento contable.
    banco_sin_asiento = estado_cuenta.movimientos.filter(movimiento_contable__isnull=True)
    totales_banco = banco_sin_asiento.aggregate(abonos=Sum('abono'), cargos=Sum('cargo'))
    abonos_banco_no_registrados = totales_banco['abonos'] or Decimal('0.00')
    cargos_banco_no_registrados = totales_banco['cargos'] or Decimal('0.00')

    # Lado de libros: asientos sobre la cuenta de bancos sin movimiento bancario.
    libros_sin_banco = movimientos_contables_del_periodo(estado_cuenta, fecha_inicio)
    totales_libros = libros_sin_banco.aggregate(debe=Sum('debe'), haber=Sum('haber'))
    abonos_empresa_no_abonados = totales_libros['debe'] or Decimal('0.00')
    cargos_empresa_no_cobrados = totales_libros['haber'] or Decimal('0.00')

    return {
        'cuenta_bancaria': cuenta_bancaria,
        'fecha_inicio_periodo': fecha_inicio,
        'fecha_corte': estado_cuenta.fecha_corte_real,
        'saldo_inicial_banco': saldo_inicial_banco,
        'saldo_libros_inicio': saldo_libros_inicio,
        'saldo_segun_banco': estado_cuenta.saldo_final_estado or Decimal('0.00'),
        'saldo_segun_libros': saldo_libros_corte,
        'diferencia_arrastrada': diferencia_arrastrada,
        'cargos_banco_no_registrados': cargos_banco_no_registrados,
        'abonos_banco_no_registrados': abonos_banco_no_registrados,
        'cargos_empresa_no_cobrados': cargos_empresa_no_cobrados,
        'abonos_empresa_no_abonados': abonos_empresa_no_abonados,
        'detalle_banco_sin_asiento': banco_sin_asiento,
        'detalle_libros_sin_banco': libros_sin_banco,
    }


def generar_conciliacion_preliminar(estado_cuenta: EstadoCuentaBancario, usuario=None):
    """
    Crea o actualiza la ConciliacionBancaria del periodo con los datos ya
    procesados del estado de cuenta. No marca la conciliación como CONCILIADA
    automáticamente — eso lo hace el usuario después de revisar/confirmar los
    emparejamientos en el admin.
    """
    datos = analizar_conciliacion(estado_cuenta)

    conciliacion, _ = ConciliacionBancaria.objects.update_or_create(
        cuenta_bancaria=estado_cuenta.cuenta_bancaria,
        mes=estado_cuenta.periodo_mes,
        anio=estado_cuenta.periodo_anio,
        defaults=dict(
            saldo_segun_banco=datos['saldo_segun_banco'],
            saldo_segun_libros=datos['saldo_segun_libros'],
            cargos_banco_no_registrados=datos['cargos_banco_no_registrados'],
            abonos_banco_no_registrados=datos['abonos_banco_no_registrados'],
            cargos_empresa_no_cobrados=datos['cargos_empresa_no_cobrados'],
            abonos_empresa_no_abonados=datos['abonos_empresa_no_abonados'],
            diferencia_arrastrada=datos['diferencia_arrastrada'],
            fecha_inicio_periodo=datos['fecha_inicio_periodo'],
            fecha_corte=datos['fecha_corte'],
            estado='EN_PROCESO',
        )
    )
    estado_cuenta.conciliacion = conciliacion
    estado_cuenta.save(update_fields=['conciliacion'])
    return conciliacion
