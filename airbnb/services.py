"""
Servicios del módulo Airbnb
===========================
Lógica de negocio para sincronización, detección de conflictos e importación.
"""
import re
import csv
import io
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Tuple, Optional, Dict, Any

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
        
        Returns:
            Lista de diccionarios con: uid, titulo, fecha_inicio, fecha_fin
        """
        eventos = []
        evento_actual = {}
        
        for linea in contenido_ical.split('\n'):
            linea = linea.strip()
            
            if linea == 'BEGIN:VEVENT':
                evento_actual = {}
            elif linea == 'END:VEVENT':
                if evento_actual.get('uid') and evento_actual.get('fecha_inicio'):
                    eventos.append(evento_actual)
                evento_actual = {}
            elif linea.startswith('UID:'):
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
        
        return eventos
    
    def _parsear_fecha(self, linea: str) -> Optional[date]:
        """Extrae fecha de una línea DTSTART o DTEND."""
        # Formato: DTSTART;VALUE=DATE:20260315 o DTSTART:20260315T120000Z
        try:
            partes = linea.split(':')
            if len(partes) >= 2:
                fecha_str = partes[-1].strip()
                # Solo fecha (8 dígitos)
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
        
        # Descargar iCal
        try:
            response = requests.get(anuncio.url_ical, timeout=30)
            response.raise_for_status()
            contenido = response.text
        except requests.RequestException as e:
            raise ValueError(f"Error al descargar calendario: {str(e)}")
        
        # Parsear eventos
        eventos = self.parser.parsear(contenido)
        
        creadas = 0
        actualizadas = 0
        errores = 0
        
        for evento in eventos:
            try:
                reserva, fue_creada = self._procesar_evento(anuncio, evento)
                if fue_creada:
                    creadas += 1
                else:
                    actualizadas += 1
            except Exception as e:
                errores += 1
                print(f"Error procesando evento: {e}")
        
        # Actualizar timestamp de sincronización
        anuncio.ultima_sincronizacion = timezone.now()
        anuncio.save(update_fields=['ultima_sincronizacion'])
        
        return creadas, actualizadas, errores
    
    def _procesar_evento(self, anuncio: AnuncioAirbnb, evento: Dict) -> Tuple[ReservaAirbnb, bool]:
        """
        Procesa un evento del iCal y crea/actualiza la reserva.
        
        LÓGICA DE DETECCIÓN DE ESTADO:
        - "Reserved" → CONFIRMADA (reserva pagada)
        - Nombre de huésped (sin "Not available") → CONFIRMADA
        - "Not available" → BLOQUEADA (bloqueo manual o pendiente)
        - "(Not available)" → PENDIENTE (solicitud sin aceptar)
        """
        uid = evento['uid']
        titulo = evento.get('titulo', '')
        fecha_inicio = evento['fecha_inicio']
        fecha_fin = evento.get('fecha_fin', fecha_inicio + timedelta(days=1))
        
        # Determinar estado y origen basado en el título
        estado, origen = self._detectar_estado_y_origen(titulo)
        
        # Buscar o crear reserva
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
        
        Patrones de Airbnb:
        - "Reserved" → Reserva confirmada y pagada
        - "Juan Pérez" (nombre sin keywords) → Reserva confirmada con nombre del huésped
        - "Airbnb (Not available)" → Solicitud pendiente de aceptar
        - "Not available" → Bloqueo manual del anfitrión
        - "Blocked" → Bloqueo manual
        
        Returns:
            Tuple (estado, origen)
        """
        titulo_lower = titulo.lower().strip()
        
        # Caso 1: Reserva confirmada explícita
        if titulo_lower == 'reserved':
            return 'CONFIRMADA', 'AIRBNB'
        
        # Caso 2: Bloqueo explícito
        if titulo_lower in ('blocked', 'block', 'bloqueado'):
            return 'BLOQUEADA', 'MANUAL'
        
        # Caso 3: Not available con paréntesis → Pendiente de aceptar
        if '(not available)' in titulo_lower:
            return 'PENDIENTE', 'AIRBNB'
        
        # Caso 4: Not available sin paréntesis → Bloqueo manual
        if 'not available' in titulo_lower:
            return 'BLOQUEADA', 'MANUAL'
        
        # Caso 5: Airbnb con algo más → Pendiente
        if titulo_lower.startswith('airbnb'):
            return 'PENDIENTE', 'AIRBNB'
        
        # Caso 6: Tiene un nombre (probablemente huésped) → Confirmada
        # Si no contiene keywords de bloqueo y tiene texto, es nombre de huésped
        if titulo and not any(word in titulo_lower for word in ['available', 'block', 'airbnb']):
            return 'CONFIRMADA', 'AIRBNB'
        
        # Default: Pendiente
        return 'PENDIENTE', 'AIRBNB'


# ==========================================
# DETECTOR DE CONFLICTOS
# ==========================================
class DetectorConflictosService:
    """Detecta conflictos entre reservas de Airbnb y eventos de la quinta."""
    
    def detectar_conflictos(self) -> List[ConflictoCalendario]:
        """
        Detecta nuevos conflictos entre reservas Airbnb y cotizaciones.
        Solo considera:
        - Reservas de anuncios que afectan eventos de la quinta
        - Reservas CONFIRMADAS (no pendientes ni bloqueadas)
        - Cotizaciones CONFIRMADAS
        
        Returns:
            Lista de conflictos creados
        """
        from comercial.models import Cotizacion
        
        # Reservas que afectan la quinta
        reservas = ReservaAirbnb.objects.filter(
            anuncio__afecta_eventos_quinta=True,
            anuncio__activo=True,
            estado='CONFIRMADA',  # Solo confirmadas generan conflictos reales
        ).select_related('anuncio')
        
        # Cotizaciones confirmadas
        cotizaciones = Cotizacion.objects.filter(
            estado='CONFIRMADA'
        ).select_related('cliente')
        
        conflictos_creados = []
        
        for reserva in reservas:
            for cotizacion in cotizaciones:
                # Verificar si hay overlap de fechas
                if self._hay_conflicto_fechas(reserva, cotizacion):
                    # Crear conflicto si no existe
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
        """Verifica si la fecha del evento cae dentro de la reserva."""
        # La fecha del evento debe estar entre check-in y check-out (exclusivo)
        return reserva.fecha_inicio <= cotizacion.fecha_evento < reserva.fecha_fin
    
    def _generar_descripcion(self, reserva: ReservaAirbnb, cotizacion) -> str:
        """Genera descripción del conflicto."""
        return (
            f"El evento '{cotizacion.nombre_evento}' del {cotizacion.fecha_evento.strftime('%d/%m/%Y')} "
            f"conflicta con la reserva de Airbnb en '{reserva.anuncio.nombre}' "
            f"({reserva.fecha_inicio.strftime('%d/%m')} - {reserva.fecha_fin.strftime('%d/%m')})"
        )


# ==========================================
# IMPORTADOR DE CSV DE PAGOS
# ==========================================
class ImportadorCSVPagosService:
    """Importa pagos desde CSV de Airbnb."""
    
    # Retenciones según régimen de plataformas tecnológicas
    TASA_ISR = Decimal('0.04')  # 4%
    TASA_IVA = Decimal('0.08')  # 8%
    
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
        
        try:
            reader = csv.DictReader(io.StringIO(contenido_csv))
        except Exception as e:
            errores.append(f"Error al leer CSV: {str(e)}")
            return importados, duplicados, errores
        
        for i, row in enumerate(reader, start=2):  # Empezar en 2 por el header
            try:
                resultado = self._procesar_fila(row, usuario)
                if resultado == 'creado':
                    importados += 1
                elif resultado == 'duplicado':
                    duplicados += 1
            except Exception as e:
                errores.append(f"Fila {i}: {str(e)}")
        
        return importados, duplicados, errores
    
    def _procesar_fila(self, row: Dict, usuario) -> str:
        """
        Procesa una fila del CSV.
        
        Returns:
            'creado', 'duplicado', o raise Exception
        """
        # Mapeo de columnas (ajustar según formato real de Airbnb)
        codigo = row.get('Confirmation code', row.get('Código de confirmación', '')).strip()
        if not codigo:
            raise ValueError("Sin código de confirmación")
        
        # Verificar duplicado
        if PagoAirbnb.objects.filter(codigo_confirmacion=codigo).exists():
            return 'duplicado'
        
        # Parsear datos
        huesped = row.get('Guest name', row.get('Nombre del huésped', 'Huésped'))
        
        # Fechas
        fecha_checkin = self._parsear_fecha(
            row.get('Start date', row.get('Fecha de inicio', ''))
        )
        fecha_checkout = self._parsear_fecha(
            row.get('End date', row.get('Fecha de finalización', ''))
        )
        
        # Montos
        monto_bruto = self._parsear_monto(
            row.get('Amount', row.get('Importe', row.get('Monto', '0')))
        )
        
        # Buscar anuncio por listing ID o nombre
        listing = row.get('Listing', row.get('Anuncio', ''))
        anuncio = self._buscar_anuncio(listing)
        
        # Calcular retenciones
        retencion_isr = monto_bruto * self.TASA_ISR
        retencion_iva = monto_bruto * self.TASA_IVA
        
        # Comisión de Airbnb (si viene en el CSV, sino 0)
        comision = self._parsear_monto(
            row.get('Host Fee', row.get('Tarifa del anfitrión', '0'))
        )
        
        monto_neto = monto_bruto - comision - retencion_isr - retencion_iva
        
        # Crear pago
        pago = PagoAirbnb.objects.create(
            anuncio=anuncio,
            codigo_confirmacion=codigo,
            huesped=huesped,
            fecha_checkin=fecha_checkin,
            fecha_checkout=fecha_checkout or fecha_checkin + timedelta(days=1),
            monto_bruto=monto_bruto,
            comision_airbnb=comision,
            retencion_isr=retencion_isr,
            retencion_iva=retencion_iva,
            monto_neto=monto_neto,
            estado='PAGADO',
            archivo_csv_origen=self.archivo_nombre,
            created_by=usuario,
        )
        
        return 'creado'
    
    def _parsear_fecha(self, fecha_str: str) -> Optional[date]:
        """Parsea fecha desde string."""
        if not fecha_str:
            return None
        
        formatos = ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y']
        for fmt in formatos:
            try:
                return datetime.strptime(fecha_str.strip(), fmt).date()
            except ValueError:
                continue
        
        return None
    
    def _parsear_monto(self, monto_str: str) -> Decimal:
        """Parsea monto desde string."""
        if not monto_str:
            return Decimal('0.00')
        
        # Limpiar caracteres no numéricos excepto punto y coma
        limpio = re.sub(r'[^\d.,\-]', '', str(monto_str))
        limpio = limpio.replace(',', '.')
        
        # Si hay múltiples puntos, quitar todos menos el último (miles vs decimales)
        partes = limpio.split('.')
        if len(partes) > 2:
            limpio = ''.join(partes[:-1]) + '.' + partes[-1]
        
        try:
            return Decimal(limpio).quantize(Decimal('0.01'))
        except:
            return Decimal('0.00')
    
    def _buscar_anuncio(self, texto: str) -> Optional[AnuncioAirbnb]:
        """Busca anuncio por nombre o listing ID."""
        if not texto:
            return None
        
        # Buscar por nombre parcial
        anuncio = AnuncioAirbnb.objects.filter(
            Q(nombre__icontains=texto) | Q(airbnb_listing_id__icontains=texto)
        ).first()
        
        return anuncio