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
from django.utils import timezone
from django.db.models import Q

from .models import AnuncioAirbnb, ReservaAirbnb, PagoAirbnb, ConflictoCalendario


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
            partes = linea.split(':')
            if len(partes) < 2:
                return None
            fecha_str = partes[-1].strip()

            if len(fecha_str) == 8 and fecha_str.isdigit():
                return datetime.strptime(fecha_str, '%Y%m%d').date()

            if len(fecha_str) >= 15 and 'T' in fecha_str:
                return datetime.strptime(fecha_str[:8], '%Y%m%d').date()

            if len(fecha_str) >= 8:
                return datetime.strptime(fecha_str[:8], '%Y%m%d').date()

        except (ValueError, IndexError):
            pass
        return None


# ==========================================
# SINCRONIZADOR DE AIRBNB
# ==========================================
class SincronizadorAirbnbService:
    """Sincroniza reservas desde calendarios iCal de Airbnb."""

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
        """
        Sincroniza un anuncio específico.

        Returns:
            Tuple (creadas, actualizadas, errores)
        """
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

        Usa doble verificación para evitar duplicados:
        1. Primero busca por uid_ical (identificador único del iCal)
        2. Si no existe, busca por anuncio + fechas (evita duplicados por UIDs cambiantes)
        """
        uid = evento['uid'].strip()
        titulo = evento.get('titulo', '').strip()
        fecha_inicio = evento['fecha_inicio']
        fecha_fin = evento.get('fecha_fin', fecha_inicio + timedelta(days=1))

        estado, origen = self._detectar_estado_y_origen(titulo)

        # Primero intentar buscar por UID
        reserva_existente = ReservaAirbnb.objects.filter(uid_ical=uid).first()

        if reserva_existente:
            reserva_existente.anuncio = anuncio
            reserva_existente.titulo = titulo
            reserva_existente.fecha_inicio = fecha_inicio
            reserva_existente.fecha_fin = fecha_fin
            reserva_existente.estado = estado
            reserva_existente.origen = origen
            reserva_existente.save()
            return reserva_existente, False

        # Si no existe por UID, buscar por anuncio + fechas exactas (evita duplicados)
        reserva_por_fechas = ReservaAirbnb.objects.filter(
            anuncio=anuncio,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin
        ).first()

        if reserva_por_fechas:
            reserva_por_fechas.uid_ical = uid
            reserva_por_fechas.titulo = titulo
            reserva_por_fechas.estado = estado
            reserva_por_fechas.origen = origen
            reserva_por_fechas.save()
            return reserva_por_fechas, False

        # Crear nueva reserva
        reserva = ReservaAirbnb.objects.create(
            uid_ical=uid,
            anuncio=anuncio,
            titulo=titulo,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            estado=estado,
            origen=origen,
        )

        return reserva, True

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

        if titulo_lower == 'reserved':
            return 'CONFIRMADA', 'AIRBNB'

        if titulo_lower in ('blocked', 'block', 'bloqueado', 'not available'):
            return 'BLOQUEADA', 'MANUAL'

        if 'not available' in titulo_lower and 'airbnb' in titulo_lower:
            return 'PENDIENTE', 'AIRBNB'

        if titulo_lower.startswith('airbnb'):
            return 'PENDIENTE', 'AIRBNB'

        if not titulo_lower:
            return 'PENDIENTE', 'AIRBNB'

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
        evento_inicio = cotizacion.fecha_evento
        if (cotizacion.hora_inicio and cotizacion.hora_fin
                and cotizacion.hora_fin < cotizacion.hora_inicio):
            evento_fin = cotizacion.fecha_evento + timedelta(days=1)
        else:
            evento_fin = cotizacion.fecha_evento
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

    IMPORTANTE: Una misma reserva puede tener múltiples pagos en diferentes fechas
    (por extensiones, pagos parciales, etc.). Este servicio agrupa por
    código de confirmación + fecha de pago para permitir múltiples registros
    del mismo código pero con diferentes fechas de pago.
    """

    def __init__(self, archivo_nombre: str = None):
        self.archivo_nombre = archivo_nombre

    def importar(self, contenido_csv: str, usuario=None) -> Tuple[int, int, List[str]]:
        """
        Importa pagos desde contenido CSV.

        Returns:
            Tuple (importados, duplicados, errores)
        """
        importados = 0
        duplicados = 0
        errores = []

        # Limpiar BOM si existe
        if contenido_csv.startswith('﻿'):
            contenido_csv = contenido_csv[1:]

        try:
            reader = csv.DictReader(io.StringIO(contenido_csv))
            filas = list(reader)
        except Exception as e:
            errores.append(f"Error al leer CSV: {str(e)}")
            return importados, duplicados, errores

        # Agrupar filas por código de confirmación + fecha de pago
        reservas_agrupadas = self._agrupar_por_codigo_y_fecha(filas)

        for clave, datos in reservas_agrupadas.items():
            try:
                resultado = self._procesar_reserva_agrupada(clave, datos, usuario)
                if resultado == 'creado':
                    importados += 1
                elif resultado == 'duplicado':
                    duplicados += 1
            except Exception as e:
                errores.append(f"{clave}: {str(e)}")

        return importados, duplicados, errores

    def _agrupar_por_codigo_y_fecha(self, filas: List[Dict]) -> Dict[str, Dict]:
        """
        Agrupa las filas del CSV por código de confirmación + fecha de pago.

        Esto permite múltiples pagos del mismo código cuando hay extensiones
        o pagos parciales en diferentes fechas.

        La clave es: "CODIGO|FECHA_PAGO" (ej: "HMFRS8DQ8Z|2026-04-12")
        """
        agrupado = defaultdict(lambda: {
            'codigo': '',
            'fecha_pago': None,
            'huesped': '',
            'espacio': '',
            'fecha_checkin': None,
            'fecha_checkout': None,
            'noches': 0,
            'monto_reservacion': Decimal('0.00'),
            'retencion_isr': Decimal('0.00'),
            'retencion_iva': Decimal('0.00'),
            'impuesto_hospedaje': Decimal('0.00'),
            'tarifa_servicio': Decimal('0.00'),
            'ingresos_brutos': Decimal('0.00'),
        })

        for fila in filas:
            # Obtener código de confirmación
            codigo = (
                fila.get('Código de confirmación', '') or
                fila.get('Codigo de confirmacion', '') or
                fila.get('Confirmation code', '') or
                ''
            ).strip()

            # Ignorar filas sin código (como Payout, Resolution, etc.)
            if not codigo:
                continue

            # Obtener fecha de pago (columna "Fecha")
            fecha_pago_str = (
                fila.get('Fecha', '') or
                fila.get('Date', '') or
                ''
            ).strip()

            fecha_pago = self._parsear_fecha(fecha_pago_str)

            # Crear clave única: código + fecha de pago
            if fecha_pago:
                clave = f"{codigo}|{fecha_pago.isoformat()}"
            else:
                clave = codigo

            tipo = (
                fila.get('Tipo', '') or
                fila.get('Type', '') or
                ''
            ).strip().lower()

            datos = agrupado[clave]
            datos['codigo'] = codigo
            if fecha_pago:
                datos['fecha_pago'] = fecha_pago

            # Extraer datos comunes (de cualquier fila con este código)
            if not datos['huesped']:
                datos['huesped'] = (
                    fila.get('Huésped', '') or
                    fila.get('Huesped', '') or
                    fila.get('Guest', '') or
                    ''
                ).strip()

            if not datos['espacio']:
                datos['espacio'] = (
                    fila.get('Espacio', '') or
                    fila.get('Listing', '') or
                    ''
                ).strip()

            if not datos['fecha_checkin']:
                fecha_inicio = (
                    fila.get('Fecha de inicio', '') or
                    fila.get('Start date', '') or
                    ''
                ).strip()
                if fecha_inicio:
                    datos['fecha_checkin'] = self._parsear_fecha(fecha_inicio)

            if not datos['fecha_checkout']:
                fecha_fin = (
                    fila.get('Fecha de finalización', '') or
                    fila.get('Fecha de finalizacion', '') or
                    fila.get('End date', '') or
                    ''
                ).strip()
                if fecha_fin:
                    datos['fecha_checkout'] = self._parsear_fecha(fecha_fin)

            if not datos['noches']:
                noches_str = (
                    fila.get('Noches', '') or
                    fila.get('Nights', '') or
                    ''
                ).strip()
                if noches_str:
                    try:
                        datos['noches'] = int(noches_str)
                    except Exception:
                        pass

            # Parsear monto
            monto = self._parsear_monto(
                fila.get('Monto', '') or
                fila.get('Amount', '') or
                '0'
            )

            # Parsear tarifa de servicio
            tarifa = self._parsear_monto(
                fila.get('Tarifa de servicio', '') or
                fila.get('Service fee', '') or
                '0'
            )

            # Parsear ingresos brutos
            ingresos_brutos = self._parsear_monto(
                fila.get('Ingresos brutos', '') or
                fila.get('Gross earnings', '') or
                '0'
            )

            # Clasificar según tipo de fila y SUMAR (no reemplazar)
            if 'reservaci' in tipo or 'reservation' in tipo:
                datos['monto_reservacion'] += monto
                datos['tarifa_servicio'] += abs(tarifa)
                if ingresos_brutos > 0:
                    datos['ingresos_brutos'] += ingresos_brutos

            elif 'retenci' in tipo and 'renta' in tipo:
                # Retención ISR (viene como negativo)
                datos['retencion_isr'] += abs(monto)

            elif 'retenci' in tipo and 'iva' in tipo:
                # Retención IVA (viene como negativo)
                datos['retencion_iva'] += abs(monto)

            elif 'impuesto' in tipo and 'liquidado' in tipo:
                # Impuesto de hospedaje
                datos['impuesto_hospedaje'] += monto

        return dict(agrupado)

    def _procesar_reserva_agrupada(self, clave: str, datos: Dict, usuario) -> str:
        """
        Procesa una reserva agrupada y crea el pago.

        Returns:
            'creado', 'duplicado', o raise Exception
        """
        codigo = datos['codigo']
        fecha_pago = datos.get('fecha_pago')

        # Verificar duplicado por código + fecha de pago
        filtro = {'codigo_confirmacion': codigo}
        if fecha_pago:
            filtro['fecha_pago'] = fecha_pago

        if PagoAirbnb.objects.filter(**filtro).exists():
            return 'duplicado'

        # Validar datos mínimos
        if not datos['huesped']:
            raise ValueError("Sin nombre de huésped")

        if not datos['fecha_checkin']:
            raise ValueError("Sin fecha de check-in")

        # Calcular monto bruto (usar ingresos_brutos si está disponible, sino monto_reservacion)
        if datos['ingresos_brutos'] > 0:
            monto_bruto = datos['ingresos_brutos']
        else:
            monto_bruto = datos['monto_reservacion']

        if monto_bruto <= 0:
            raise ValueError("Sin monto de reservación")

        # Calcular checkout si no existe
        fecha_checkout = datos['fecha_checkout']
        if not fecha_checkout and datos['noches'] > 0:
            fecha_checkout = datos['fecha_checkin'] + timedelta(days=datos['noches'])
        elif not fecha_checkout:
            fecha_checkout = datos['fecha_checkin'] + timedelta(days=1)

        # Calcular monto neto
        monto_neto = (
            datos['monto_reservacion']
            - datos['tarifa_servicio']
        )

        if monto_neto <= 0:
            monto_neto = monto_bruto - datos['tarifa_servicio'] - datos['retencion_isr'] - datos['retencion_iva']

        # Buscar anuncio por nombre
        anuncio = self._buscar_anuncio(datos['espacio'])

        # Generar notas
        notas_list = []
        if datos['impuesto_hospedaje'] > 0:
            notas_list.append(f"Impuesto hospedaje: ${datos['impuesto_hospedaje']}")

        # Verificar si hay otros pagos del mismo código (extensión)
        pagos_previos = PagoAirbnb.objects.filter(codigo_confirmacion=codigo).count()
        if pagos_previos > 0:
            notas_list.append(f"Pago #{pagos_previos + 1} de esta reserva (extensión o pago parcial)")

        # Crear pago
        pago = PagoAirbnb.objects.create(
            anuncio=anuncio,
            codigo_confirmacion=codigo,
            huesped=datos['huesped'],
            fecha_checkin=datos['fecha_checkin'],
            fecha_checkout=fecha_checkout,
            fecha_pago=fecha_pago,
            monto_bruto=monto_bruto,
            comision_airbnb=datos['tarifa_servicio'],
            retencion_isr=datos['retencion_isr'],
            retencion_iva=datos['retencion_iva'],
            monto_neto=monto_neto if monto_neto > 0 else monto_bruto,
            estado='PAGADO',
            archivo_csv_origen=self.archivo_nombre or '',
            created_by=usuario,
            notas='\n'.join(notas_list),
        )

        return 'creado'

    def _parsear_fecha(self, fecha_str: str) -> Optional[date]:
        """Parsea fecha desde string."""
        if not fecha_str:
            return None

        fecha_str = fecha_str.strip()

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

        monto_str = str(monto_str).strip()

        # Detectar si es negativo
        es_negativo = '-' in monto_str or '(' in monto_str

        limpio = re.sub(r'[^\d.,]', '', monto_str)

        if not limpio:
            return Decimal('0.00')

        if ',' in limpio and '.' in limpio:
            ultima_coma = limpio.rfind(',')
            ultimo_punto = limpio.rfind('.')

            if ultima_coma > ultimo_punto:
                # Coma es decimal: 1.234,56
                limpio = limpio.replace('.', '').replace(',', '.')
            else:
                # Punto es decimal: 1,234.56
                limpio = limpio.replace(',', '')
        elif ',' in limpio:
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

        anuncio = AnuncioAirbnb.objects.filter(
            Q(nombre__icontains=texto) |
            Q(nombre__icontains=texto.split()[0] if texto.split() else texto)
        ).first()

        return anuncio
