"""
Servicios del módulo Airbnb
===========================
Lógica de negocio para sincronización, detección de conflictos e importación.
"""
import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import AnuncioAirbnb, ConflictoCalendario, PagoAirbnb, ReservaAirbnb


class _Simulacion(Exception):
    """
    Aborta la transacción de una importación simulada. Es la forma de obtener
    el resumen exacto de lo que pasaría —incluidos los ids y los cambios por
    campo— sin dejar nada escrito.
    """


# ==========================================
# PARSER DE ICAL
# ==========================================
class ICalParserService:
    """Parsea archivos iCal de Airbnb."""

    def parsear(self, contenido_ical: str) -> List[Dict[str, Any]]:
        """
        Parsea contenido iCal y retorna lista de eventos.
        Maneja líneas multi-línea (folded lines) del estándar iCal.
        """
        # Paso 1: Desplegar líneas folded (las que empiezan con espacio/tab son continuación)
        lineas_raw = contenido_ical.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        lineas = []
        for linea in lineas_raw:
            if linea.startswith(' ') or linea.startswith('\t'):
                if lineas:
                    lineas[-1] += linea[1:]  # Concatenar sin el espacio/tab inicial
            else:
                lineas.append(linea)

        eventos = []
        evento_actual = None

        for linea in lineas:
            linea = linea.strip()

            if linea == 'BEGIN:VEVENT':
                evento_actual = {}
            elif linea == 'END:VEVENT':
                if evento_actual and evento_actual.get('uid') and evento_actual.get('fecha_inicio'):
                    # Asegurar fecha_fin
                    if 'fecha_fin' not in evento_actual:
                        evento_actual['fecha_fin'] = evento_actual['fecha_inicio'] + timedelta(days=1)
                    eventos.append(evento_actual)
                evento_actual = None
            elif evento_actual is not None:
                if linea.startswith('UID:'):
                    evento_actual['uid'] = linea[4:].strip()
                elif linea.startswith('SUMMARY:'):
                    evento_actual['titulo'] = linea[8:].strip()
                elif linea.startswith('DTSTART'):
                    fecha = self._parsear_fecha(linea)
                    if fecha:
                        evento_actual['fecha_inicio'] = fecha
                elif linea.startswith('DTEND'):
                    fecha = self._parsear_fecha(linea)
                    if fecha:
                        evento_actual['fecha_fin'] = fecha
                elif linea.startswith('DESCRIPTION:'):
                    evento_actual['descripcion'] = linea[12:].strip()

        return eventos

    def _parsear_fecha(self, linea: str) -> Optional[date]:
        """Extrae fecha de una línea DTSTART o DTEND. Maneja múltiples formatos."""
        try:
            # Obtener la parte del valor (después del último ':')
            partes = linea.split(':')
            if len(partes) < 2:
                return None
            fecha_str = partes[-1].strip()

            # Formato date-only: 20260315
            if len(fecha_str) == 8 and fecha_str.isdigit():
                return datetime.strptime(fecha_str, '%Y%m%d').date()

            # Formato datetime: 20260315T120000 o 20260315T120000Z
            if len(fecha_str) >= 15 and 'T' in fecha_str:
                return datetime.strptime(fecha_str[:8], '%Y%m%d').date()

            # Intentar parsear los primeros 8 caracteres
            if len(fecha_str) >= 8:
                return datetime.strptime(fecha_str[:8], '%Y%m%d').date()

        except (ValueError, IndexError):
            pass
        return None


# ==========================================
# SINCRONIZADOR DE AIRBNB
# ==========================================
class SincronizadorAirbnbService:
    """
    Sincroniza reservas desde calendarios iCal de Airbnb.

    FIX de duplicados:
    - El uid_ical ahora se usa como clave única COMPUESTA con el anuncio
    - Se usa update_or_create con uid_ical como lookup
    - Se limpian reservas que ya no existen en el iCal (canceladas por Airbnb)
    """

    def __init__(self):
        self.parser = ICalParserService()

    def sincronizar_todos(self) -> Dict[str, Any]:
        """Sincroniza todos los anuncios activos."""
        anuncios = AnuncioAirbnb.objects.filter(activo=True)
        resultados = {}

        for anuncio in anuncios:
            try:
                creadas, actualizadas, errores = self.sincronizar_anuncio(anuncio)
                resultados[anuncio.nombre] = {
                    'status': 'ok',
                    'creadas': creadas,
                    'actualizadas': actualizadas,
                    'errores': errores
                }
            except Exception as e:
                resultados[anuncio.nombre] = {
                    'status': 'error',
                    'mensaje': str(e)
                }

        return resultados

    def sincronizar_anuncio(self, anuncio: AnuncioAirbnb) -> Tuple[int, int, int]:
        """Sincroniza un anuncio específico."""
        if not anuncio.url_ical:
            raise ValueError(f"El anuncio '{anuncio.nombre}' no tiene URL iCal configurada")

        try:
            response = requests.get(anuncio.url_ical, timeout=30)
            response.raise_for_status()
            contenido = response.text
        except requests.RequestException as e:
            raise ValueError(f"Error al descargar calendario: {str(e)}")

        eventos = self.parser.parsear(contenido)

        creadas = 0
        actualizadas = 0
        errores = 0
        uids_en_ical = set()

        for evento in eventos:
            try:
                uid = evento.get('uid', '').strip()
                if not uid:
                    errores += 1
                    continue

                uids_en_ical.add(uid)
                reserva, fue_creada = self._procesar_evento(anuncio, evento)
                if fue_creada:
                    creadas += 1
                else:
                    actualizadas += 1
            except Exception as e:
                errores += 1
                print(f"Error procesando evento {evento.get('uid', '?')}: {e}")

        # Marcar como canceladas las reservas de este anuncio que ya no están en el iCal
        # (solo las que fueron importadas de Airbnb, no las manuales ni las de eventos)
        reservas_obsoletas = ReservaAirbnb.objects.filter(
            anuncio=anuncio,
            origen='AIRBNB',
        ).exclude(
            uid_ical__in=uids_en_ical
        ).exclude(
            estado='CANCELADA'
        )

        canceladas = reservas_obsoletas.update(estado='CANCELADA')
        if canceladas > 0:
            print(f"  {canceladas} reservas obsoletas marcadas como canceladas en {anuncio.nombre}")

        anuncio.ultima_sincronizacion = timezone.now()
        anuncio.save(update_fields=['ultima_sincronizacion'])

        return creadas, actualizadas, errores

    def _procesar_evento(self, anuncio: AnuncioAirbnb, evento: Dict) -> Tuple[ReservaAirbnb, bool]:
        """
        Procesa un evento del iCal y crea/actualiza la reserva.
        Usa uid_ical como clave única para evitar duplicados.
        """
        uid = evento['uid'].strip()
        titulo = evento.get('titulo', '').strip()
        fecha_inicio = evento['fecha_inicio']
        fecha_fin = evento.get('fecha_fin', fecha_inicio + timedelta(days=1))

        estado, origen = self._detectar_estado_y_origen(titulo)

        # update_or_create usando uid_ical como lookup
        # Si el UID ya existe, actualiza los datos; si no, crea nuevo
        reserva, creada = ReservaAirbnb.objects.update_or_create(
            uid_ical=uid,
            defaults={
                'anuncio': anuncio,
                'titulo': titulo,
                'fecha_inicio': fecha_inicio,
                'fecha_fin': fecha_fin,
                'estado': estado,
                'origen': origen,
            }
        )

        return reserva, creada

    def _detectar_estado_y_origen(self, titulo: str) -> Tuple[str, str]:
        """
        Detecta el estado y origen de una reserva basado en el título del iCal.

        Títulos conocidos de Airbnb:
        - "Reserved"                    → Confirmada (huésped ya pagó)
        - "Airbnb (Not available)"      → Pendiente (solicitud sin aceptar)
        - "Not available"               → Bloqueada por host
        - "Blocked"                     → Bloqueada por host
        - Nombre de persona             → Confirmada (huésped con nombre)
        - ""  (vacío)                   → Pendiente
        """
        titulo_lower = titulo.lower().strip()

        # Reserva confirmada por Airbnb
        if titulo_lower == 'reserved':
            return 'CONFIRMADA', 'AIRBNB'

        # Bloqueo manual del host
        if titulo_lower in ('blocked', 'block', 'bloqueado', 'not available'):
            return 'BLOQUEADA', 'MANUAL'

        # Solicitud pendiente de Airbnb
        if 'not available' in titulo_lower and 'airbnb' in titulo_lower:
            return 'PENDIENTE', 'AIRBNB'

        # Título que empieza con "airbnb" sin más contexto
        if titulo_lower.startswith('airbnb'):
            return 'PENDIENTE', 'AIRBNB'

        # Título vacío
        if not titulo_lower:
            return 'PENDIENTE', 'AIRBNB'

        # Si tiene un nombre de persona (no contiene palabras clave) → Confirmada
        if titulo and not any(word in titulo_lower for word in ['available', 'block', 'airbnb', 'evento', 'qkt']):
            return 'CONFIRMADA', 'AIRBNB'

        return 'PENDIENTE', 'AIRBNB'

# ==========================================
# DETECTOR DE CONFLICTOS
# ==========================================
class DetectorConflictosService:
    """Detecta conflictos entre reservas de Airbnb y eventos de la quinta."""

    def detectar_conflictos(self) -> List[ConflictoCalendario]:
        """Detecta nuevos conflictos entre reservas Airbnb y cotizaciones."""
        from comercial.models import Cotizacion

        reservas = ReservaAirbnb.objects.filter(
            anuncio__afecta_eventos_quinta=True,
            anuncio__activo=True,
            estado='CONFIRMADA',
        ).select_related('anuncio')

        cotizaciones = Cotizacion.objects.filter(
            estado='CONFIRMADA'
        ).select_related('cliente')

        conflictos_creados = []

        for reserva in reservas:
            for cotizacion in cotizaciones:
                if self._hay_conflicto_fechas(reserva, cotizacion):
                    conflicto, creado = ConflictoCalendario.objects.get_or_create(
                        reserva_airbnb=reserva,
                        cotizacion=cotizacion,
                        fecha_conflicto=cotizacion.fecha_evento,
                        defaults={
                            'estado': 'PENDIENTE',
                            'descripcion': self._generar_descripcion(reserva, cotizacion)
                        }
                    )
                    if creado:
                        conflictos_creados.append(conflicto)

        return conflictos_creados

    def _hay_conflicto_fechas(self, reserva: ReservaAirbnb, cotizacion) -> bool:
        from datetime import timedelta
        evento_inicio = cotizacion.fecha_evento
        # Si hora_fin < hora_inicio, el evento cruza medianoche y ocupa 2 días
        if (cotizacion.hora_inicio and cotizacion.hora_fin
                and cotizacion.hora_fin < cotizacion.hora_inicio):
            evento_fin = cotizacion.fecha_evento + timedelta(days=1)
        else:
            evento_fin = cotizacion.fecha_evento
        # Hay conflicto si los rangos se solapan
        return reserva.fecha_inicio <= evento_fin and evento_inicio < reserva.fecha_fin

    def _generar_descripcion(self, reserva: ReservaAirbnb, cotizacion) -> str:
        return (
            f"El evento '{cotizacion.nombre_evento}' del {cotizacion.fecha_evento.strftime('%d/%m/%Y')} "
            f"conflicta con la reserva de Airbnb en '{reserva.anuncio.nombre}' "
            f"({reserva.fecha_inicio.strftime('%d/%m')} - {reserva.fecha_fin.strftime('%d/%m')})"
        )


# ==========================================
# IMPORTADOR DE CSV DE PAGOS (AIRBNB MÉXICO)
# ==========================================
class ImportadorCSVPagosService:
    """
    Importa pagos desde CSV de Airbnb (formato México).

    El CSV de Airbnb tiene múltiples filas por reserva:
    - Reservación: Monto principal
    - Retención del impuesto sobre la renta para México: ISR (negativo)
    - Retención del IVA en México: IVA (negativo)
    - Impuestos liquidados como anfitrión: Impuesto de hospedaje
    - Payout: Transferencia (sin código, se ignora)

    Este servicio agrupa todo por código de confirmación.
    """

    def __init__(self, archivo_nombre: str = None):
        self.archivo_nombre = archivo_nombre

    def importar(self, contenido_csv: str, usuario=None, *,
                 simular: bool = False) -> Dict[str, Any]:
        """
        Importa (o simula importar) los pagos de un CSV de Airbnb.

        Con `simular=True` no escribe nada: devuelve el mismo resumen para que
        el admin muestre una vista previa. Antes la importación era a ciegas y
        sin transacción, así que un error a media pasada dejaba pagos a medias.

        Reimportar el mismo archivo ACTUALIZA los pagos existentes en vez de
        omitirlos: Airbnb altera reservas, aplica reembolsos y ajusta montos
        después del hecho, y el ERP se quedaba con el dato viejo para siempre.
        """
        resumen = {
            'creados': [], 'actualizados': [], 'sin_cambios': [],
            'descuadrados': [], 'errores': [], 'simulado': simular,
        }

        if contenido_csv.startswith('\ufeff'):
            contenido_csv = contenido_csv[1:]

        try:
            filas = list(csv.DictReader(io.StringIO(contenido_csv)))
        except Exception as e:
            resumen['errores'].append(f"Error al leer CSV: {e}")
            return resumen

        try:
            agrupadas = self._agrupar_por_codigo(filas)
        except Exception as e:
            resumen['errores'].append(f"Error al agrupar las filas: {e}")
            return resumen

        # Todo o nada: si una reserva revienta a la mitad, no queremos medio
        # CSV aplicado. En simulación se revierte siempre.
        try:
            with transaction.atomic():
                for codigo, datos in agrupadas.items():
                    try:
                        self._procesar_reserva_agrupada(codigo, datos, usuario, resumen)
                    except Exception as e:
                        resumen['errores'].append(f"Código {codigo}: {e}")
                if simular:
                    raise _Simulacion()
        except _Simulacion:
            pass

        return resumen

    def _agrupar_por_codigo(self, filas: List[Dict]) -> Dict[str, Dict]:
        """
        Agrupa las filas del CSV por código de confirmación.

        El CSV trae varias filas por reserva —el cargo, cada retención, el
        impuesto de hospedaje, los reembolsos— y hay que sumarlas para saber
        qué pasó realmente con esa reserva.
        """
        agrupado = defaultdict(self._grupo_vacio)
        payouts: Dict[date, str] = {}

        for fila in filas:
            codigo = self._campo(fila, 'Código de confirmación',
                                 'Codigo de confirmacion', 'Confirmation code')
            tipo = self._campo(fila, 'Tipo', 'Type').lower()
            fecha = self._parsear_fecha(self._campo(fila, 'Fecha', 'Date'))

            if not codigo:
                # Las filas de Payout no traen código de reserva, pero sí el id
                # del depósito. Se indexan por fecha para pegárselo después a
                # las reservas que Airbnb liquidó ese día: es lo que permite
                # cuadrar contra el movimiento bancario.
                if 'payout' in tipo or 'transferencia' in tipo:
                    referencia = self._campo(fila, 'Código de referencia',
                                             'Payout ID', 'Reference code')
                    if fecha and referencia:
                        payouts[fecha] = referencia
                continue
            datos = agrupado[codigo]

            for clave, etiquetas in (
                ('huesped', ('Huésped', 'Huesped', 'Guest')),
                ('espacio', ('Espacio', 'Listing', 'Anuncio')),
                ('divisa', ('Divisa', 'Currency')),
                ('payout_id', ('ID de pago', 'Payout ID', 'Referencia')),
            ):
                if not datos[clave]:
                    datos[clave] = self._campo(fila, *etiquetas)

            if not datos['fecha_checkin']:
                datos['fecha_checkin'] = self._parsear_fecha(
                    self._campo(fila, 'Fecha de inicio', 'Start date'))
            if not datos['fecha_checkout']:
                datos['fecha_checkout'] = self._parsear_fecha(
                    self._campo(fila, 'Fecha de finalización',
                                'Fecha de finalizacion', 'End date'))
            if not datos['fecha_pago']:
                # La fecha de la transacción es la que determina el período
                # fiscal: es cuando Airbnb pagó y retuvo.
                datos['fecha_pago'] = self._parsear_fecha(
                    self._campo(fila, 'Fecha', 'Date'))

            if not datos['noches']:
                try:
                    datos['noches'] = int(self._campo(fila, 'Noches', 'Nights') or 0)
                except ValueError:
                    datos['noches'] = 0

            monto = self._parsear_monto(self._campo(fila, 'Monto', 'Amount'))
            tarifa = self._parsear_monto(
                self._campo(fila, 'Tarifa de servicio', 'Service fee'))
            brutos = self._parsear_monto(
                self._campo(fila, 'Ingresos brutos', 'Gross earnings'))
            # Columna aparte: el impuesto al hospedaje que Airbnb retiene y
            # entera por su cuenta. No llega al depósito.
            ish = self._parsear_monto(
                self._campo(fila, 'Impuesto liquidado por Airbnb',
                            'Taxes withheld by Airbnb'))

            if 'reservaci' in tipo or 'reservation' in tipo:
                datos['monto_reservacion'] += monto
                datos['tarifa_servicio'] += abs(tarifa)
                datos['ingresos_brutos'] += brutos
                datos['impuesto_hospedaje'] += ish
            elif 'retenci' in tipo and 'renta' in tipo:
                datos['retencion_isr'] += abs(monto)
            elif 'retenci' in tipo and 'iva' in tipo:
                datos['retencion_iva'] += abs(monto)
            elif 'impuesto' in tipo and 'liquidado' in tipo:
                # "Impuestos liquidados como anfitrión" es el IVA trasladado
                # que Airbnb cobra al huésped y transfiere al anfitrión: suma
                # al depósito y lo entera él. No confundir con el ISH.
                datos['iva_trasladado'] += monto
            elif any(p in tipo for p in ('reembolso', 'refund', 'resolution',
                                         'resolución', 'resolucion', 'ajuste',
                                         'adjustment', 'cancel')):
                # Antes estas filas caían en ninguna rama y desaparecían, así
                # que un reembolso al huésped no se reflejaba en ningún lado.
                datos['ajustes'] += monto
                datos['tiene_ajuste'] = True

        for datos in agrupado.values():
            if not datos['payout_id'] and datos['fecha_pago'] in payouts:
                datos['payout_id'] = payouts[datos['fecha_pago']]

        return dict(agrupado)

    @staticmethod
    def _grupo_vacio() -> Dict[str, Any]:
        return {
            'huesped': '', 'espacio': '', 'divisa': '', 'payout_id': '',
            'fecha_checkin': None, 'fecha_checkout': None, 'fecha_pago': None,
            'noches': 0,
            'monto_reservacion': Decimal('0.00'),
            'retencion_isr': Decimal('0.00'),
            'retencion_iva': Decimal('0.00'),
            'iva_trasladado': Decimal('0.00'),
            'impuesto_hospedaje': Decimal('0.00'),
            'tarifa_servicio': Decimal('0.00'),
            'ingresos_brutos': Decimal('0.00'),
            'ajustes': Decimal('0.00'),
            'tiene_ajuste': False,
        }

    @staticmethod
    def _campo(fila: Dict, *etiquetas: str) -> str:
        """Primer valor no vacío entre varios encabezados posibles."""
        for etiqueta in etiquetas:
            valor = (fila.get(etiqueta) or '').strip()
            if valor:
                return valor
        return ''

    def _procesar_reserva_agrupada(self, codigo: str, datos: Dict, usuario,
                                   resumen: Dict) -> None:
        if not datos['huesped']:
            raise ValueError("Sin nombre de huésped")
        if not datos['fecha_checkin']:
            raise ValueError("Sin fecha de check-in")

        # `Ingresos brutos` es la BASE del ingreso, sin IVA —pese al nombre— y
        # antes de descontar la comisión. `Monto` de la fila de reservación es
        # lo que queda tras la comisión. La diferencia entre ambos ES la
        # comisión efectiva (incluye su propio IVA), así que no hace falta
        # confiar en la columna "Tarifa de servicio", que la reporta sin IVA.
        bruto = datos['ingresos_brutos'] or datos['monto_reservacion']
        if bruto <= 0 and not datos['tiene_ajuste']:
            raise ValueError("Sin monto de reservación")

        comision = datos['tarifa_servicio']
        if datos['ingresos_brutos'] and datos['monto_reservacion']:
            comision = datos['ingresos_brutos'] - datos['monto_reservacion']

        checkout = datos['fecha_checkout']
        if not checkout:
            noches = datos['noches'] or 1
            checkout = datos['fecha_checkin'] + timedelta(days=noches)

        # Reconstrucción del depósito real de Airbnb, verificada al centavo
        # contra el reporte de marzo de 2026. El IVA trasladado SUMA: Airbnb
        # se lo cobra al huésped y lo transfiere para que lo entere el
        # anfitrión. El impuesto al hospedaje NO entra: ese lo retiene y
        # entera la propia plataforma.
        neto = (bruto
                - comision
                + datos['iva_trasladado']
                - datos['retencion_isr']
                - datos['retencion_iva']
                + datos['ajustes'])

        estado = 'PAGADO'
        if datos['tiene_ajuste'] and neto <= 0:
            estado = 'REEMBOLSADO'

        campos = {
            'huesped': datos['huesped'],
            'fecha_checkin': datos['fecha_checkin'],
            'fecha_checkout': checkout,
            'fecha_pago': datos['fecha_pago'],
            'monto_bruto': bruto,
            'comision_airbnb': comision,
            'retencion_isr': datos['retencion_isr'],
            'retencion_iva': datos['retencion_iva'],
            'iva_trasladado': datos['iva_trasladado'],
            'impuesto_hospedaje': datos['impuesto_hospedaje'],
            'monto_neto': neto,
            'estado': estado,
            'payout_id': datos['payout_id'],
            'archivo_csv_origen': self.archivo_nombre or '',
            'origen': 'CSV',
        }

        anuncio = self._buscar_anuncio(datos['espacio'])
        if anuncio:
            campos['anuncio'] = anuncio

        existente = PagoAirbnb.objects.filter(codigo_confirmacion=codigo).first()

        if existente is None:
            pago = PagoAirbnb(codigo_confirmacion=codigo, created_by=usuario, **campos)
            pago.reserva = self._buscar_reserva(pago)
            pago.save()
            resumen['creados'].append(codigo)
        elif existente.origen == 'MANUAL':
            # Un pago capturado a mano gana: alguien lo corrigió sabiendo algo
            # que el CSV no dice.
            resumen['sin_cambios'].append(codigo)
            return
        else:
            cambios = [c for c, v in campos.items() if getattr(existente, c) != v]
            if not cambios:
                resumen['sin_cambios'].append(codigo)
                return
            for campo, valor in campos.items():
                setattr(existente, campo, valor)
            if existente.reserva_id is None:
                existente.reserva = self._buscar_reserva(existente)
            existente.save()
            pago = existente
            resumen['actualizados'].append((codigo, cambios))

        # El neto que no cuadra con sus componentes se marca en vez de
        # corregirse en silencio: casi siempre significa que el CSV trae un
        # concepto que no estamos modelando.
        if not pago.cuadra:
            resumen['descuadrados'].append((codigo, pago.diferencia_neto))

    def _buscar_reserva(self, pago) -> Optional[ReservaAirbnb]:
        """
        Vincula el pago con su reserva del calendario. El FK existía desde
        siempre pero nadie lo llenaba, así que no se podía conciliar el
        calendario contra lo cobrado.
        """
        candidatas = ReservaAirbnb.objects.filter(fecha_inicio=pago.fecha_checkin)
        if pago.anuncio_id:
            candidatas = candidatas.filter(anuncio_id=pago.anuncio_id)
        return candidatas.order_by('-id').first()


    def _parsear_fecha(self, fecha_str: str) -> Optional[date]:
        """Parsea fecha desde string."""
        if not fecha_str:
            return None

        fecha_str = fecha_str.strip()

        # Formatos comunes de Airbnb
        formatos = [
            '%m/%d/%Y',   # 01/25/2026 (formato USA que usa Airbnb)
            '%d/%m/%Y',   # 25/01/2026
            '%Y-%m-%d',   # 2026-01-25
            '%d-%m-%Y',   # 25-01-2026
        ]

        for fmt in formatos:
            try:
                return datetime.strptime(fecha_str, fmt).date()
            except ValueError:
                continue

        return None

    def _parsear_monto(self, monto_str: str) -> Decimal:
        """Parsea monto desde string."""
        if not monto_str:
            return Decimal('0.00')

        # Limpiar caracteres no numéricos excepto punto, coma y signo negativo
        monto_str = str(monto_str).strip()

        # Detectar si es negativo
        es_negativo = '-' in monto_str or '(' in monto_str

        # Limpiar
        limpio = re.sub(r'[^\d.,]', '', monto_str)

        if not limpio:
            return Decimal('0.00')

        # Manejar separadores de miles vs decimales
        # Si tiene coma y punto, el último es el decimal
        if ',' in limpio and '.' in limpio:
            # Determinar cuál es el separador decimal (el último)
            ultima_coma = limpio.rfind(',')
            ultimo_punto = limpio.rfind('.')

            if ultima_coma > ultimo_punto:
                # Coma es decimal: 1.234,56
                limpio = limpio.replace('.', '').replace(',', '.')
            else:
                # Punto es decimal: 1,234.56
                limpio = limpio.replace(',', '')
        elif ',' in limpio:
            # Solo coma - puede ser decimal o miles
            # Si hay exactamente 2 dígitos después de la coma, es decimal
            partes = limpio.split(',')
            if len(partes) == 2 and len(partes[1]) <= 2:
                limpio = limpio.replace(',', '.')
            else:
                limpio = limpio.replace(',', '')

        try:
            valor = Decimal(limpio).quantize(Decimal('0.01'))
            return -valor if es_negativo else valor
        except (InvalidOperation, ValueError):
            return Decimal('0.00')

    def _buscar_anuncio(self, texto: str) -> Optional[AnuncioAirbnb]:
        """Busca anuncio por nombre parcial."""
        if not texto:
            return None

        # Buscar coincidencia parcial
        anuncio = AnuncioAirbnb.objects.filter(
            Q(nombre__icontains=texto) |
            Q(nombre__icontains=texto.split()[0] if texto.split() else texto)
        ).first()

        return anuncio

# ==========================================
# CONCILIACIÓN DE DEPÓSITOS
# ==========================================
class ConciliacionDepositosService:
    """
    Cuadra lo que Airbnb dice haber depositado contra lo que el banco registró.

    Airbnb no deposita reserva por reserva: junta las que liquida el mismo día
    en un solo payout, y ese payout es el que aparece en el estado de cuenta.
    Por eso la conciliación no puede ser pago contra movimiento —hay que sumar
    primero por `payout_id` y comparar el total—, que es justo lo que se venía
    haciendo a mano contra el PDF del banco.

    El emparejamiento automático **solo asigna cuando no hay duda**. Si un
    depósito admite dos abonos igual de plausibles, se marca como ambiguo y lo
    resuelve quien concilia: adivinar y presentar el resultado como conciliado
    es peor que dejar el pendiente a la vista. Lo que se confirma a mano queda
    guardado en `DepositoConciliado` y manda sobre cualquier automatismo.

    Fuera de esas confirmaciones no escribe nada: el resto se recalcula cada
    vez, así que cargar el estado de cuenta que faltaba basta para que cuadre.
    """

    # Airbnb libera el pago y el banco lo abona días después (en el CSV real de
    # marzo, la "fecha de llegada estimada" va cinco días después del payout).
    # Se busca en esa ventana en vez de exigir la misma fecha.
    DIAS_ANTES = 1
    DIAS_DESPUES = 10

    def __init__(self, mes: int, anio: int):
        self.mes = mes
        self.anio = anio

    def conciliar(self) -> List[Dict[str, Any]]:
        """
        Un renglón por depósito del mes, ordenado por fecha.

        Los pagos que Airbnb no liquidó en ningún payout (o cuyo CSV no traía
        la fila de transferencia) se agrupan aparte al final: son los que
        todavía no se pueden cuadrar contra el banco.
        """
        depositos = self._agrupar()
        identificados = [d for d in depositos if d['payout_id']]

        usados: set = set()
        self._asignar_confirmados(identificados, usados)
        self._asignar_por_referencia(identificados, usados)
        self._asignar_por_importe(identificados, usados)

        for deposito in identificados:
            self._calificar(deposito)

        return depositos

    def totales(self, depositos: List[Dict[str, Any]]) -> Dict[str, Any]:
        cuadrados = [d for d in depositos if d['estado'] == 'CONCILIADO']
        return {
            'num_depositos': len(depositos),
            'num_conciliados': len(cuadrados),
            'num_ambiguos': len([d for d in depositos if d['estado'] == 'AMBIGUO']),
            'esperado': sum((d['total'] for d in depositos), Decimal('0.00')),
            'conciliado': sum((d['total'] for d in cuadrados), Decimal('0.00')),
            'diferencia': sum((d['diferencia'] for d in depositos), Decimal('0.00')),
        }

    @staticmethod
    def confirmar(payout_id: str, movimiento, usuario=None):
        """
        Fija a mano el abono que corresponde a un payout.

        Se reemplaza cualquier confirmación previa del mismo payout y
        cualquier otra que apuntara a ese movimiento: un abono del banco es un
        solo depósito, no dos.
        """
        from .models import DepositoConciliado

        DepositoConciliado.objects.filter(
            Q(payout_id=payout_id) | Q(movimiento=movimiento)
        ).delete()
        return DepositoConciliado.objects.create(
            payout_id=payout_id, movimiento=movimiento,
            confirmado_por=usuario if usuario and usuario.is_authenticated else None,
        )

    @staticmethod
    def deshacer(payout_id: str) -> int:
        """Suelta un emparejamiento confirmado a mano."""
        from .models import DepositoConciliado

        borrados, _ = DepositoConciliado.objects.filter(payout_id=payout_id).delete()
        return borrados

    # ---- armado y emparejamiento -------------------------------------

    def _agrupar(self) -> List[Dict[str, Any]]:
        pagos = (PagoAirbnb.objects
                 .filter(fecha_pago__month=self.mes, fecha_pago__year=self.anio)
                 .exclude(estado__in=('CANCELADO', 'REEMBOLSADO'))
                 .select_related('anuncio')
                 .order_by('fecha_pago', 'codigo_confirmacion'))

        grupos: Dict[str, List[PagoAirbnb]] = defaultdict(list)
        for pago in pagos:
            grupos[pago.payout_id or ''].append(pago)

        depositos = [self._renglon(payout_id, lista)
                     for payout_id, lista in grupos.items() if payout_id]
        depositos.sort(key=lambda d: (d['fecha'], d['payout_id']))

        sueltos = grupos.get('')
        if sueltos:
            renglon = self._renglon('', sueltos)
            renglon['estado'] = 'SIN_PAYOUT'
            depositos.append(renglon)
        return depositos

    @staticmethod
    def _renglon(payout_id, pagos_del_payout) -> Dict[str, Any]:
        return {
            'payout_id': payout_id,
            'fecha': min(p.fecha_pago for p in pagos_del_payout),
            'pagos': pagos_del_payout,
            'total': sum((p.monto_neto for p in pagos_del_payout), Decimal('0.00')),
            'movimiento': None,
            'candidatos': [],
            'confirmado': False,
            'diferencia': Decimal('0.00'),
            'estado': 'SIN_MOVIMIENTO',
        }

    def _asignar_confirmados(self, depositos, usados):
        """Lo que alguien ya resolvió a mano no se vuelve a adivinar."""
        from .models import DepositoConciliado

        confirmados = {
            c.payout_id: c.movimiento
            for c in (DepositoConciliado.objects
                      .filter(payout_id__in=[d['payout_id'] for d in depositos])
                      .select_related('movimiento__estado_cuenta__cuenta_bancaria'))
        }
        for deposito in depositos:
            movimiento = confirmados.get(deposito['payout_id'])
            if movimiento is not None:
                deposito['movimiento'] = movimiento
                deposito['confirmado'] = True
                usados.add(movimiento.pk)

    def _asignar_por_referencia(self, depositos, usados):
        """
        Si el banco conservó el id del payout no hay ambigüedad posible: ese
        es el depósito aunque el importe difiera, y la diferencia es
        justamente lo que hay que revisar.
        """
        for deposito in depositos:
            if deposito['movimiento'] is not None:
                continue
            candidatos = list(
                self._abonos(usados).filter(
                    Q(referencia__icontains=deposito['payout_id']) |
                    Q(descripcion__icontains=deposito['payout_id'])
                )[:2]
            )
            if len(candidatos) == 1:
                deposito['movimiento'] = candidatos[0]
                usados.add(candidatos[0].pk)

    def _asignar_por_importe(self, depositos, usados):
        """
        Por importe exacto dentro de la ventana de días, y **solo si el
        candidato es único**.

        Dos payouts del mismo importe en fechas cercanas son perfectamente
        posibles —dos habitaciones con la misma tarifa liquidadas la misma
        semana—, y ahí el importe no distingue nada. Elegir por proximidad de
        fecha daría un resultado que parece conciliado sin serlo. Se resuelve
        en varias pasadas: cada asignación inequívoca descarta ese abono y
        puede volver inequívoco a otro depósito que antes tenía dos.
        """
        pendientes = [d for d in depositos if d['movimiento'] is None]

        hubo_cambios = True
        while hubo_cambios:
            hubo_cambios = False
            for deposito in pendientes:
                if deposito['movimiento'] is not None:
                    continue
                candidatos = list(self._candidatos_por_importe(deposito, usados))
                deposito['candidatos'] = candidatos
                if len(candidatos) == 1:
                    deposito['movimiento'] = candidatos[0]
                    deposito['candidatos'] = []
                    usados.add(candidatos[0].pk)
                    hubo_cambios = True

    def _candidatos_por_importe(self, deposito, usados):
        return self._abonos(usados).filter(
            abono=deposito['total'],
            fecha__gte=deposito['fecha'] - timedelta(days=self.DIAS_ANTES),
            fecha__lte=deposito['fecha'] + timedelta(days=self.DIAS_DESPUES),
        ).order_by('fecha', 'pk')

    @staticmethod
    def _abonos(usados):
        from contabilidad.models import MovimientoEstadoCuenta

        return (MovimientoEstadoCuenta.objects
                .filter(abono__gt=0)
                .exclude(pk__in=usados)
                .select_related('estado_cuenta__cuenta_bancaria'))

    @staticmethod
    def _calificar(deposito):
        movimiento = deposito['movimiento']
        if movimiento is None:
            deposito['estado'] = ('AMBIGUO' if deposito['candidatos']
                                  else 'SIN_MOVIMIENTO')
            return
        deposito['diferencia'] = movimiento.abono - deposito['total']
        deposito['estado'] = ('CONCILIADO' if deposito['diferencia'] == 0
                              else 'DIFERENCIA')
