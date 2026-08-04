"""
Servicios del módulo Airbnb
===========================
Lógica de negocio para sincronización, detección de conflictos e importación.
"""
import re
import csv
import io
from datetime import datetime, date, timedelta
from decimal import Decimal, InvalidOperation
from typing import List, Tuple, Optional, Dict, Any
from collections import defaultdict

import requests
from django.db import transaction
from django.utils import timezone
from django.db.models import Q

from .models import AnuncioAirbnb, ReservaAirbnb, PagoAirbnb, ConflictoCalendario


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

        for fila in filas:
            codigo = self._campo(fila, 'Código de confirmación',
                                 'Codigo de confirmacion', 'Confirmation code')
            if not codigo:
                # Las filas de payout no traen código, pero sí el id del
                # depósito, que sirve para conciliar contra el banco.
                continue

            tipo = self._campo(fila, 'Tipo', 'Type').lower()
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

            if 'reservaci' in tipo or 'reservation' in tipo:
                datos['monto_reservacion'] += monto
                datos['tarifa_servicio'] += abs(tarifa)
                datos['ingresos_brutos'] += brutos
            elif 'retenci' in tipo and 'renta' in tipo:
                datos['retencion_isr'] += abs(monto)
            elif 'retenci' in tipo and 'iva' in tipo:
                datos['retencion_iva'] += abs(monto)
            elif 'impuesto' in tipo and 'liquidado' in tipo:
                datos['impuesto_hospedaje'] += monto
            elif any(p in tipo for p in ('reembolso', 'refund', 'resolution',
                                         'resolución', 'resolucion', 'ajuste',
                                         'adjustment', 'cancel')):
                # Antes estas filas caían en ninguna rama y desaparecían, así
                # que un reembolso al huésped no se reflejaba en ningún lado.
                datos['ajustes'] += monto
                datos['tiene_ajuste'] = True

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

        # `Ingresos brutos` es el ingreso del anfitrión ya neto de la comisión
        # de Airbnb; `Monto` de la fila de reservación es lo mismo por otra
        # vía. Antes se tomaba el bruto de un campo y se le restaba la
        # comisión que YA estaba descontada, duplicando la resta.
        bruto = datos['ingresos_brutos'] or datos['monto_reservacion']
        if bruto <= 0 and not datos['tiene_ajuste']:
            raise ValueError("Sin monto de reservación")

        checkout = datos['fecha_checkout']
        if not checkout:
            noches = datos['noches'] or 1
            checkout = datos['fecha_checkin'] + timedelta(days=noches)

        neto = (bruto
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
            'comision_airbnb': datos['tarifa_servicio'],
            'retencion_isr': datos['retencion_isr'],
            'retencion_iva': datos['retencion_iva'],
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