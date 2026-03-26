"""
Servicio de integración con Jibble API
=======================================
Endpoints verificados:
- Token:      POST https://identity.prod.jibble.io/connect/token
- People:     GET  https://workspace.prod.jibble.io/v1/People
- Timesheets: GET  https://time-attendance.prod.jibble.io/v1/Timesheets?date=YYYY-MM-DD

Variables de entorno: JIBBLE_CLIENT_ID, JIBBLE_CLIENT_SECRET
"""

import re
import time
import logging
import requests
from datetime import datetime, timedelta
from django.conf import settings

logger = logging.getLogger(__name__)

JIBBLE_TOKEN_URL = 'https://identity.prod.jibble.io/connect/token'
JIBBLE_PEOPLE_URL = 'https://workspace.prod.jibble.io/v1/People'
JIBBLE_TIMESHEETS_URL = 'https://time-attendance.prod.jibble.io/v1/Timesheets'


class JibbleAPIError(Exception):
    pass


class JibbleService:

    def __init__(self):
        self.client_id = getattr(settings, 'JIBBLE_CLIENT_ID', '') or ''
        self.client_secret = getattr(settings, 'JIBBLE_CLIENT_SECRET', '') or ''
        self.access_token = None
        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json; charset=UTF-8',
        })

    def esta_configurado(self):
        return bool(self.client_id and self.client_secret)

    def autenticar(self):
        if not self.esta_configurado():
            raise JibbleAPIError(
                "Credenciales Jibble no configuradas. "
                "Agrega JIBBLE_CLIENT_ID y JIBBLE_CLIENT_SECRET en Railway."
            )
        try:
            response = requests.post(
                JIBBLE_TOKEN_URL,
                data={
                    'grant_type': 'client_credentials',
                    'client_id': self.client_id,
                    'client_secret': self.client_secret,
                },
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
                timeout=15,
            )
            if response.status_code != 200:
                raise JibbleAPIError(
                    f"Auth error (HTTP {response.status_code}): {response.text[:200]}"
                )
            data = response.json()
            self.access_token = data.get('access_token')
            if not self.access_token:
                raise JibbleAPIError("Jibble respondió OK pero sin access_token.")
            self._session.headers.update({'Authorization': f'Bearer {self.access_token}'})
            logger.info("Jibble autenticacion exitosa.")
            return True
        except requests.exceptions.RequestException as e:
            raise JibbleAPIError(f"Error de conexion: {e}")

    def _verificar_token(self):
        if not self.access_token:
            self.autenticar()

    # ==========================================
    # PEOPLE
    # ==========================================
    def obtener_personas(self):
        """GET /v1/People -> {person_id: nombre_upper}. Excluye owners/admins."""
        self._verificar_token()
        try:
            response = self._session.get(JIBBLE_PEOPLE_URL, timeout=15)
            response.raise_for_status()
            data = response.json()
            items = data.get('value', []) if isinstance(data, dict) else data

            mapa = {}
            for p in items:
                pid = p.get('id', '')
                nombre = p.get('fullName') or p.get('preferredName', 'Desconocido')
                role = p.get('role', '').lower()
                if pid and role not in ('owner', 'admin'):
                    mapa[pid] = nombre.upper().strip()

            return mapa
        except requests.exceptions.RequestException as e:
            raise JibbleAPIError(f"Error al obtener personas: {e}")

    # ==========================================
    # TIMESHEETS — con entrada/salida real
    # ==========================================
    def obtener_timesheets_semana(self, fecha_inicio, fecha_fin, person_ids=None):
        """
        Obtiene horas por dia por empleado usando el endpoint Timesheets.
        Incluye pausa entre requests para evitar rate limit (429).
        
        Returns:
            dict {
                'personas': {
                    'NOMBRE': {
                        'person_id': 'xxx',
                        'dias': [{fecha, duracion_segundos, entrada, salida, salida_raw}, ...],
                        'ultima_salida': '2026-03-21 14:39'  # Última salida de la semana
                    }
                },
                'fuente': 'Timesheets'
            }
        """
        self._verificar_token()

        # 1. Obtener personas
        mapa_personas = self.obtener_personas()
        if not mapa_personas:
            raise JibbleAPIError("No se encontraron personas activas en Jibble.")

        if person_ids:
            mapa_personas = {k: v for k, v in mapa_personas.items() if k in person_ids}

        logger.info(f"Jibble: {len(mapa_personas)} empleados encontrados.")

        # 2. Iterar dia por dia con pausa para evitar rate limit
        personas = {}
        dt_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
        dt_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        current = dt_inicio
        es_primer_dia = True

        while current <= dt_fin:
            fecha_str = current.strftime('%Y-%m-%d')

            try:
                # Pausa entre requests para evitar rate limit (429)
                if not es_primer_dia:
                    time.sleep(2)
                es_primer_dia = False

                response = self._session.get(
                    JIBBLE_TIMESHEETS_URL,
                    params={'date': fecha_str},
                    timeout=20,
                )

                # Retry si hay rate limit
                if response.status_code == 429:
                    logger.warning(f"Timesheets {fecha_str}: rate limit, esperando 6s...")
                    time.sleep(6)
                    response = self._session.get(
                        JIBBLE_TIMESHEETS_URL,
                        params={'date': fecha_str},
                        timeout=20,
                    )

                # Segundo retry si sigue 429
                if response.status_code == 429:
                    logger.warning(f"Timesheets {fecha_str}: segundo rate limit, esperando 10s...")
                    time.sleep(10)
                    response = self._session.get(
                        JIBBLE_TIMESHEETS_URL,
                        params={'date': fecha_str},
                        timeout=20,
                    )

                if response.status_code != 200:
                    logger.warning(f"Timesheets {fecha_str}: HTTP {response.status_code}")
                    current += timedelta(days=1)
                    continue

                data = response.json()
                items = data.get('value', [])

                for item in items:
                    pid = item.get('personId', '')
                    if pid not in mapa_personas:
                        continue

                    nombre = mapa_personas[pid]
                    if nombre not in personas:
                        personas[nombre] = {'person_id': pid, 'dias': [], 'ultima_salida': ''}

                    daily_list = item.get('daily', [])
                    for day in daily_list:
                        day_date = day.get('date', '')
                        if isinstance(day_date, str) and len(day_date) >= 10:
                            day_date = day_date[:10]

                        # Duracion: payroll -> tracked -> worked
                        payroll_hours = day.get('payrollHours', {})
                        tracked_hours = day.get('trackedHours', {})

                        duracion_seg = self._parsear_iso_duration(payroll_hours.get('total', ''))
                        if duracion_seg == 0:
                            duracion_seg = self._parsear_iso_duration(tracked_hours.get('total', ''))
                        if duracion_seg == 0:
                            duracion_seg = self._parsear_iso_duration(tracked_hours.get('worked', ''))
                        if duracion_seg == 0:
                            duracion_seg = self._parsear_iso_duration(item.get('totalPayroll', ''))
                        if duracion_seg == 0:
                            duracion_seg = self._parsear_iso_duration(item.get('totalTracked', ''))

                        # Entrada y salida reales
                        first_in = day.get('firstIn') or day.get('firstInTimestamp') or ''
                        last_out = day.get('lastOut') or day.get('lastOutTimestamp') or ''

                        entrada = self._formatear_hora(first_in)
                        salida = self._formatear_hora(last_out)

                        # Solo agregar si hay tiempo real trabajado (> 1 minuto)
                        if duracion_seg > 60:
                            personas[nombre]['dias'].append({
                                'fecha': day_date or fecha_str,
                                'duracion_segundos': duracion_seg,
                                'entrada': entrada,
                                'salida': salida,
                            })

                            # Rastrear la última salida de la semana para fecha de emisión
                            salida_raw = str(last_out).strip() if last_out else ''
                            ultima_actual = personas[nombre].get('ultima_salida', '')

                            # Construir datetime de salida: fecha + hora
                            if salida != '-':
                                salida_dt_str = f"{day_date or fecha_str} {salida}"
                                if not ultima_actual or salida_dt_str > ultima_actual:
                                    personas[nombre]['ultima_salida'] = salida_dt_str

            except Exception as e:
                logger.warning(f"Error procesando Timesheets {fecha_str}: {e}")

            current += timedelta(days=1)

        # Ordenar dias de cada persona
        for nombre in personas:
            personas[nombre]['dias'].sort(key=lambda d: d['fecha'])

        return {'personas': personas, 'fuente': 'Timesheets'}

    # ==========================================
    # PARSERS
    # ==========================================
    @staticmethod
    def _parsear_iso_duration(valor):
        """Parsea ISO 8601 duration a segundos: PT6H6M52.753408S -> 22012"""
        if not valor or not isinstance(valor, str) or valor == 'PT0S':
            return 0
        try:
            pattern = r'P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?'
            match = re.match(pattern, valor)
            if not match:
                return 0
            days = int(match.group(1) or 0)
            hours = int(match.group(2) or 0)
            minutes = int(match.group(3) or 0)
            seconds = float(match.group(4) or 0)
            return (days * 86400) + (hours * 3600) + (minutes * 60) + int(seconds)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _formatear_hora(valor):
        """Formatea hora de Jibble a HH:MM."""
        if not valor:
            return '-'
        s = str(valor).strip()
        if 'T' in s:
            try:
                parte_hora = s.split('T')[1][:8]
                parts = parte_hora.split(':')
                return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            except (ValueError, IndexError):
                pass
        if ':' in s:
            parts = s.split(':')
            try:
                return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            except (ValueError, IndexError):
                pass
        return '-'

    # ==========================================
    # DIAGNOSTICO
    # ==========================================
    def diagnostico(self):
        resultado = {
            'configurado': self.esta_configurado(),
            'autenticado': False,
            'personas': {},
            'errores': [],
        }
        if not resultado['configurado']:
            resultado['errores'].append('JIBBLE_CLIENT_ID y/o JIBBLE_CLIENT_SECRET no configurados.')
            return resultado
        try:
            self.autenticar()
            resultado['autenticado'] = True
        except JibbleAPIError as e:
            resultado['errores'].append(f'Auth: {e}')
            return resultado
        try:
            personas = self.obtener_personas()
            resultado['personas'] = personas
        except JibbleAPIError as e:
            resultado['errores'].append(f'People: {e}')
        return resultado