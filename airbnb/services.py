"""
Servicios del módulo Airbnb
===========================
Lógica de negocio para sincronización de calendarios y detección de conflictos.
"""
import re
import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Tuple, Optional
from urllib.request import urlopen
from urllib.error import URLError

from django.utils import timezone
from django.db import transaction

from .models import AnuncioAirbnb, ReservaAirbnb, PagoAirbnb, ConflictoCalendario


class ICalParserService:
    """
    Parser de feeds iCal de Airbnb.
    Extrae eventos de bloqueo/reserva del calendario.
    """
    
    def __init__(self, anuncio: AnuncioAirbnb):
        self.anuncio = anuncio
    
    def fetch_and_parse(self) -> List[dict]:
        """
        Descarga el feed iCal y extrae los eventos.
        Retorna lista de diccionarios con datos de cada evento.
        """
        try:
            response = urlopen(self.anuncio.url_ical, timeout=30)
            content = response.read().decode('utf-8')
            return self._parse_ical(content)
        except URLError as e:
            raise Exception(f"Error al conectar con Airbnb: {str(e)}")
        except Exception as e:
            raise Exception(f"Error procesando iCal: {str(e)}")
    
    def _parse_ical(self, content: str) -> List[dict]:
        """
        Parsea el contenido iCal y extrae eventos VEVENT.
        """
        eventos = []
        lines = content.replace('\r\n ', '').replace('\r\n\t', '').split('\r\n')
        
        evento_actual = None
        
        for line in lines:
            if line == 'BEGIN:VEVENT':
                evento_actual = {}
            elif line == 'END:VEVENT' and evento_actual:
                if 'uid' in evento_actual and 'dtstart' in evento_actual:
                    eventos.append(evento_actual)
                evento_actual = None
            elif evento_actual is not None:
                if ':' in line:
                    key, value = line.split(':', 1)
                    # Limpiar parámetros del key (ej: DTSTART;VALUE=DATE)
                    key = key.split(';')[0].lower()
                    
                    if key == 'uid':
                        evento_actual['uid'] = value
                    elif key == 'dtstart':
                        evento_actual['dtstart'] = self._parse_date(value)
                    elif key == 'dtend':
                        evento_actual['dtend'] = self._parse_date(value)
                    elif key == 'summary':
                        evento_actual['summary'] = value
                    elif key == 'description':
                        evento_actual['description'] = value
        
        return eventos
    
    def _parse_date(self, value: str) -> Optional[datetime]:
        """Parsea fecha de iCal (formato YYYYMMDD o YYYYMMDDTHHMMSS)"""
        try:
            value = value.replace('Z', '')
            if 'T' in value:
                return datetime.strptime(value[:15], '%Y%m%dT%H%M%S').date()
            else:
                return datetime.strptime(value[:8], '%Y%m%d').date()
        except:
            return None


class SincronizadorAirbnbService:
    """
    Servicio principal de sincronización de calendarios Airbnb.
    """
    
    def sincronizar_anuncio(self, anuncio: AnuncioAirbnb) -> Tuple[int, int, int]:
        """
        Sincroniza un anuncio específico con su feed iCal.
        
        Returns:
            Tuple (creadas, actualizadas, errores)
        """
        parser = ICalParserService(anuncio)
        eventos = parser.fetch_and_parse()
        
        creadas = 0
        actualizadas = 0
        errores = 0
        
        uids_procesados = set()
        
        with transaction.atomic():
            for evento in eventos:
                try:
                    uid = evento.get('uid', '')
                    if not uid:
                        continue
                    
                    uids_procesados.add(uid)
                    
                    fecha_inicio = evento.get('dtstart')
                    fecha_fin = evento.get('dtend')
                    
                    if not fecha_inicio:
                        continue
                    
                    # Si no hay fecha fin, asumimos una noche
                    if not fecha_fin:
                        fecha_fin = fecha_inicio + timedelta(days=1)
                    
                    # Determinar si es reserva o bloqueo
                    summary = evento.get('summary', '')
                    descripcion = evento.get('description', '')
                    
                    # Airbnb usa "Reserved" o "Not available" típicamente
                    if 'reserved' in summary.lower() or 'airbnb' in summary.lower():
                        estado = 'CONFIRMADA'
                        origen = 'AIRBNB'
                    elif 'not available' in summary.lower() or 'blocked' in summary.lower():
                        estado = 'BLOQUEADA'
                        origen = 'MANUAL'
                    else:
                        estado = 'CONFIRMADA'
                        origen = 'AIRBNB'
                    
                    # Crear o actualizar reserva
                    reserva, created = ReservaAirbnb.objects.update_or_create(
                        uid_ical=uid,
                        defaults={
                            'anuncio': anuncio,
                            'titulo': summary[:200] if summary else 'Reserva Airbnb',
                            'fecha_inicio': fecha_inicio,
                            'fecha_fin': fecha_fin,
                            'estado': estado,
                            'origen': origen,
                            'notas': descripcion[:500] if descripcion else '',
                        }
                    )
                    
                    if created:
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
    
    def sincronizar_todos(self) -> dict:
        """
        Sincroniza todos los anuncios activos.
        
        Returns:
            Dict con resultados por anuncio
        """
        resultados = {}
        anuncios = AnuncioAirbnb.objects.filter(activo=True)
        
        for anuncio in anuncios:
            try:
                creadas, actualizadas, errores = self.sincronizar_anuncio(anuncio)
                resultados[anuncio.nombre] = {
                    'status': 'ok',
                    'creadas': creadas,
                    'actualizadas': actualizadas,
                    'errores': errores,
                }
            except Exception as e:
                resultados[anuncio.nombre] = {
                    'status': 'error',
                    'mensaje': str(e),
                }
        
        return resultados


class DetectorConflictosService:
    """
    Detecta conflictos entre reservas de Airbnb y eventos de la quinta.
    """
    
    def detectar_conflictos(self, fecha_inicio=None, fecha_fin=None) -> List[ConflictoCalendario]:
        """
        Busca conflictos en un rango de fechas.
        Si no se especifica rango, busca los próximos 90 días.
        """
        from comercial.models import Cotizacion
        
        if not fecha_inicio:
            fecha_inicio = timezone.now().date()
        if not fecha_fin:
            fecha_fin = fecha_inicio + timedelta(days=90)
        
        conflictos_nuevos = []
        
        # Obtener reservas de Airbnb que afectan la quinta
        reservas = ReservaAirbnb.objects.filter(
            anuncio__afecta_eventos_quinta=True,
            anuncio__activo=True,
            estado='CONFIRMADA',
            fecha_inicio__lte=fecha_fin,
            fecha_fin__gte=fecha_inicio,
        ).select_related('anuncio')
        
        # Obtener eventos confirmados de la quinta
        eventos = Cotizacion.objects.filter(
            estado='CONFIRMADA',
            fecha_evento__gte=fecha_inicio,
            fecha_evento__lte=fecha_fin,
        )
        
        for reserva in reservas:
            for evento in eventos:
                # Verificar si hay solapamiento de fechas
                fecha_evento = evento.fecha_evento
                
                # El evento cae dentro del rango de la reserva
                if reserva.fecha_inicio <= fecha_evento < reserva.fecha_fin:
                    # Verificar si ya existe este conflicto
                    existe = ConflictoCalendario.objects.filter(
                        reserva_airbnb=reserva,
                        cotizacion=evento,
                        fecha_conflicto=fecha_evento,
                    ).exists()
                    
                    if not existe:
                        conflicto = ConflictoCalendario.objects.create(
                            reserva_airbnb=reserva,
                            cotizacion=evento,
                            fecha_conflicto=fecha_evento,
                            descripcion=f"Reserva en {reserva.anuncio.nombre} ({reserva.fecha_inicio} - {reserva.fecha_fin}) "
                                       f"choca con evento '{evento.nombre_evento}' del {fecha_evento}",
                            estado='PENDIENTE',
                        )
                        conflictos_nuevos.append(conflicto)
        
        return conflictos_nuevos
    
    def obtener_conflictos_pendientes(self) -> List[ConflictoCalendario]:
        """Retorna todos los conflictos sin resolver."""
        return ConflictoCalendario.objects.filter(
            estado='PENDIENTE'
        ).select_related('reserva_airbnb', 'cotizacion', 'reserva_airbnb__anuncio')


class ImportadorCSVPagosService:
    """
    Importa pagos desde CSV exportado de Airbnb.
    
    El CSV de Airbnb típicamente tiene columnas como:
    - Confirmation code
    - Guest
    - Start date
    - End date
    - Gross earnings
    - Host service fee
    - Payout
    - Payout date
    """
    
    # Mapeo de columnas (Airbnb puede variar los nombres)
    COLUMN_MAPPINGS = {
        'codigo_confirmacion': ['confirmation code', 'código de confirmación', 'confirmation', 'codigo'],
        'huesped': ['guest', 'huésped', 'guest name', 'nombre del huésped'],
        'fecha_checkin': ['start date', 'fecha de inicio', 'check-in', 'checkin', 'inicio'],
        'fecha_checkout': ['end date', 'fecha de fin', 'check-out', 'checkout', 'fin'],
        'monto_bruto': ['gross earnings', 'ganancias brutas', 'earnings', 'importe bruto', 'amount'],
        'comision_airbnb': ['host service fee', 'comisión de servicio', 'service fee', 'host fee'],
        'monto_neto': ['payout', 'pago', 'amount paid out', 'cobro neto'],
        'fecha_pago': ['payout date', 'fecha de pago', 'paid date'],
        'listing': ['listing', 'anuncio', 'property'],
    }
    
    def __init__(self, archivo_nombre: str = ''):
        self.archivo_nombre = archivo_nombre
        self.errores = []
    
    def importar(self, contenido_csv: str, usuario=None) -> Tuple[int, int, List[str]]:
        """
        Importa pagos desde contenido CSV.
        
        Returns:
            Tuple (importados, duplicados, errores)
        """
        importados = 0
        duplicados = 0
        self.errores = []
        
        try:
            # Detectar delimitador
            dialect = csv.Sniffer().sniff(contenido_csv[:2048])
            reader = csv.DictReader(io.StringIO(contenido_csv), dialect=dialect)
        except:
            # Fallback a coma
            reader = csv.DictReader(io.StringIO(contenido_csv))
        
        # Normalizar headers
        if reader.fieldnames:
            header_map = self._mapear_columnas(reader.fieldnames)
        else:
            self.errores.append("No se encontraron encabezados en el CSV")
            return 0, 0, self.errores
        
        for i, row in enumerate(reader, start=2):  # start=2 porque fila 1 es header
            try:
                datos = self._extraer_datos_fila(row, header_map)
                
                if not datos.get('codigo_confirmacion') and not datos.get('huesped'):
                    continue  # Fila vacía
                
                # Verificar duplicado
                if datos.get('codigo_confirmacion'):
                    existe = PagoAirbnb.objects.filter(
                        codigo_confirmacion=datos['codigo_confirmacion']
                    ).exists()
                    if existe:
                        duplicados += 1
                        continue
                
                # Crear pago
                pago = PagoAirbnb(
                    codigo_confirmacion=datos.get('codigo_confirmacion', ''),
                    huesped=datos.get('huesped', 'Sin nombre'),
                    fecha_checkin=datos.get('fecha_checkin'),
                    fecha_checkout=datos.get('fecha_checkout'),
                    monto_bruto=datos.get('monto_bruto', Decimal('0')),
                    comision_airbnb=datos.get('comision_airbnb', Decimal('0')),
                    monto_neto=datos.get('monto_neto', Decimal('0')),
                    fecha_pago=datos.get('fecha_pago'),
                    estado='PAGADO' if datos.get('fecha_pago') else 'PENDIENTE',
                    archivo_csv_origen=self.archivo_nombre,
                    created_by=usuario,
                )
                
                # Calcular retenciones si no vienen en el CSV
                if pago.monto_bruto > 0:
                    pago.calcular_retenciones()
                
                # Intentar vincular con anuncio
                listing_name = datos.get('listing', '')
                if listing_name:
                    anuncio = AnuncioAirbnb.objects.filter(
                        nombre__icontains=listing_name
                    ).first()
                    if anuncio:
                        pago.anuncio = anuncio
                
                pago.save()
                importados += 1
                
            except Exception as e:
                self.errores.append(f"Fila {i}: {str(e)}")
        
        return importados, duplicados, self.errores
    
    def _mapear_columnas(self, headers: List[str]) -> dict:
        """Mapea los headers del CSV a nuestros campos."""
        header_map = {}
        headers_lower = [h.lower().strip() for h in headers]
        
        for campo, variantes in self.COLUMN_MAPPINGS.items():
            for variante in variantes:
                if variante in headers_lower:
                    idx = headers_lower.index(variante)
                    header_map[campo] = headers[idx]
                    break
        
        return header_map
    
    def _extraer_datos_fila(self, row: dict, header_map: dict) -> dict:
        """Extrae y parsea datos de una fila del CSV."""
        datos = {}
        
        for campo, header in header_map.items():
            valor = row.get(header, '').strip()
            
            if campo in ['fecha_checkin', 'fecha_checkout', 'fecha_pago']:
                datos[campo] = self._parsear_fecha(valor)
            elif campo in ['monto_bruto', 'comision_airbnb', 'monto_neto']:
                datos[campo] = self._parsear_monto(valor)
            else:
                datos[campo] = valor
        
        return datos
    
    def _parsear_fecha(self, valor: str):
        """Parsea fecha de varios formatos."""
        if not valor:
            return None
        
        formatos = ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y']
        for fmt in formatos:
            try:
                return datetime.strptime(valor, fmt).date()
            except:
                continue
        return None
    
    def _parsear_monto(self, valor: str) -> Decimal:
        """Parsea monto limpiando símbolos de moneda."""
        if not valor:
            return Decimal('0')
        
        # Limpiar símbolos
        valor = re.sub(r'[^\d.,\-]', '', valor)
        valor = valor.replace(',', '')
        
        try:
            return Decimal(valor).quantize(Decimal('0.01'))
        except:
            return Decimal('0')
