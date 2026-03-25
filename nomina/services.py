"""
Servicio de integración con Jibble API
=======================================
Maneja autenticación OAuth2, obtención de empleados y timesheets.

Endpoints:
- Token:      POST https://identity.prod.jibble.io/connect/token
- People:     GET  https://workspace.prod.jibble.io/v1/People
- Timesheets: GET  https://time-attendance.prod.jibble.io/v1/TrackedTimeReport
- Daily:      GET  https://time-attendance.prod.jibble.io/v1/DailyTimesheetsSummary

Variables de entorno requeridas:
- JIBBLE_CLIENT_ID
- JIBBLE_CLIENT_SECRET
"""

import logging
import requests
from datetime import date, datetime, timedelta
from django.conf import settings

logger = logging.getLogger(__name__)

JIBBLE_TOKEN_URL = 'https://identity.prod.jibble.io/connect/token'
JIBBLE_PEOPLE_URL = 'https://workspace.prod.jibble.io/v1/People'
JIBBLE_TRACKED_TIME_URL = 'https://time-attendance.prod.jibble.io/v1/TrackedTimeReport'
JIBBLE_DAILY_SUMMARY_URL = 'https://time-attendance.prod.jibble.io/v1/DailyTimesheetsSummary'


class JibbleAPIError(Exception):
    """Error al comunicarse con la API de Jibble."""
    pass


class JibbleService:
    """
    Servicio para consumir la API REST de Jibble.
    
    Flujo OAuth2 Client Credentials:
    1. POST a /connect/token con client_id + client_secret
    2. Obtener access_token (Bearer)
    3. Usar token en headers de cada request
    """

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
                    f"Error de autenticación Jibble (HTTP {response.status_code}). "
                    f"Verifica tu Client ID y Client Secret."
                )
            data = response.json()
            self.access_token = data.get('access_token')
            if not self.access_token:
                raise JibbleAPIError("Jibble respondió OK pero sin access_token.")
            self._session.headers.update({'Authorization': f'Bearer {self.access_token}'})
            logger.info("Jibble autenticación exitosa.")
            return True
        except requests.exceptions.RequestException as e:
            raise JibbleAPIError(f"Error de conexión con Jibble: {e}")

    def obtener_personas(self):
        self._verificar_token()
        try:
            response = self._session.get(JIBBLE_PEOPLE_URL, timeout=15)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and 'value' in data:
                return data['value']
            return data if isinstance(data, list) else [data]
        except requests.exceptions.RequestException as e:
            raise JibbleAPIError(f"Error al obtener personas: {e}")

    def obtener_timesheets_semana(self, fecha_inicio, fecha_fin, person_ids=None):
        self._verificar_token()
        try:
            resultado = self._obtener_daily_summary(fecha_inicio, fecha_fin, person_ids)
            if resultado:
                return {'personas': resultado, 'fuente': 'DailyTimesheetsSummary'}
        except Exception as e:
            logger.warning(f"DailyTimesheetsSummary falló, intentando TrackedTimeReport: {e}")
        try:
            resultado = self._obtener_tracked_time(fecha_inicio, fecha_fin, person_ids)
            return {'personas': resultado, 'fuente': 'TrackedTimeReport'}
        except Exception as e:
            raise JibbleAPIError(f"No se pudieron obtener timesheets de Jibble: {e}")

    def _obtener_daily_summary(self, fecha_inicio, fecha_fin, person_ids=None):
        personas = {}
        dt_inicio = datetime.strptime(fecha_inicio, '%Y-%m-%d').date()
        dt_fin = datetime.strptime(fecha_fin, '%Y-%m-%d').date()
        current = dt_inicio
        while current <= dt_fin:
            params = {'date': current.strftime('%Y-%m-%d')}
            if person_ids:
                params['personIds'] = ','.join(person_ids)
            response = self._session.get(JIBBLE_DAILY_SUMMARY_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            items = data if isinstance(data, list) else data.get('value', [])
            for item in items:
                nombre = item.get('memberName') or item.get('personName') or item.get('name', 'Desconocido')
                nombre_upper = nombre.upper().strip()
                person_id = item.get('personId') or item.get('memberId', '')
                if nombre_upper not in personas:
                    personas[nombre_upper] = {'person_id': str(person_id), 'dias': []}
                duracion = self._parsear_duracion_jibble(item)
                if duracion > 0:
                    entrada = item.get('firstIn') or item.get('clockIn') or ''
                    salida = item.get('lastOut') or item.get('clockOut') or ''
                    personas[nombre_upper]['dias'].append({
                        'fecha': current.strftime('%Y-%m-%d'),
                        'duracion_segundos': duracion,
                        'entrada': self._formatear_hora(entrada),
                        'salida': self._formatear_hora(salida),
                    })
            current += timedelta(days=1)
        return personas if personas else None

    def _obtener_tracked_time(self, fecha_inicio, fecha_fin, person_ids=None):
        params = {'from': f'{fecha_inicio}T00:00:00.000Z', 'to': f'{fecha_fin}T23:59:59.000Z'}
        if person_ids:
            params['personIds'] = ','.join(person_ids)
        response = self._session.get(JIBBLE_TRACKED_TIME_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        items = data if isinstance(data, list) else data.get('value', [])
        personas = {}
        for item in items:
            nombre = item.get('memberName') or item.get('personName') or item.get('name', 'Desconocido')
            nombre_upper = nombre.upper().strip()
            person_id = item.get('personId') or item.get('memberId', '')
            if nombre_upper not in personas:
                personas[nombre_upper] = {'person_id': str(person_id), 'dias': []}
            daily_data = item.get('dailyData') or item.get('days') or item.get('entries') or []
            if isinstance(daily_data, list):
                for day in daily_data:
                    fecha = day.get('date', '')
                    if isinstance(fecha, str) and len(fecha) >= 10:
                        fecha = fecha[:10]
                    duracion = self._parsear_duracion_jibble(day)
                    if duracion > 0:
                        personas[nombre_upper]['dias'].append({
                            'fecha': fecha,
                            'duracion_segundos': duracion,
                            'entrada': self._formatear_hora(day.get('firstIn', '')),
                            'salida': self._formatear_hora(day.get('lastOut', '')),
                        })
            else:
                duracion = self._parsear_duracion_jibble(item)
                fecha = item.get('date', fecha_inicio)
                if isinstance(fecha, str) and len(fecha) >= 10:
                    fecha = fecha[:10]
                if duracion > 0:
                    personas[nombre_upper]['dias'].append({
                        'fecha': fecha,
                        'duracion_segundos': duracion,
                        'entrada': self._formatear_hora(item.get('firstIn', '')),
                        'salida': self._formatear_hora(item.get('lastOut', '')),
                    })
        return personas

    def _verificar_token(self):
        if not self.access_token:
            self.autenticar()

    @staticmethod
    def _parsear_duracion_jibble(item):
        for key in ('durationInSeconds', 'totalSeconds', 'payrollSeconds', 'regularSeconds'):
            val = item.get(key)
            if val is not None:
                try:
                    return int(val)
                except (ValueError, TypeError):
                    pass
        for key in ('durationInMinutes', 'totalMinutes'):
            val = item.get(key)
            if val is not None:
                try:
                    return int(float(val) * 60)
                except (ValueError, TypeError):
                    pass
        for key in ('payrollHours', 'totalHours', 'duration', 'regularHours', 'total'):
            val = item.get(key)
            if val and isinstance(val, str) and ':' in val:
                try:
                    parts = val.split(':')
                    h = int(parts[0])
                    m = int(parts[1])
                    s = int(parts[2]) if len(parts) > 2 else 0
                    return h * 3600 + m * 60 + s
                except (ValueError, IndexError):
                    pass
        for key in ('hours', 'totalHoursDecimal'):
            val = item.get(key)
            if val is not None:
                try:
                    return int(float(val) * 3600)
                except (ValueError, TypeError):
                    pass
        return 0

    @staticmethod
    def _formatear_hora(valor):
        if not valor:
            return '-'
        s = str(valor).strip()
        if 'T' in s:
            try:
                dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
                return dt.strftime('%H:%M')
            except ValueError:
                pass
            try:
                parte_hora = s.split('T')[1][:8]
                dt = datetime.strptime(parte_hora, '%H:%M:%S')
                return dt.strftime('%H:%M')
            except (ValueError, IndexError):
                pass
        if ':' in s:
            parts = s.split(':')
            try:
                return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            except (ValueError, IndexError):
                pass
        return '-'

    def diagnostico(self):
        resultado = {
            'configurado': self.esta_configurado(),
            'autenticado': False,
            'personas_count': 0,
            'personas_muestra': [],
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
            resultado['personas_count'] = len(personas)
            for p in personas[:3]:
                nombre = p.get('fullName') or p.get('name') or p.get('displayName', '???')
                pid = p.get('id') or p.get('personId', '???')
                resultado['personas_muestra'].append({'nombre': nombre, 'id': pid})
        except JibbleAPIError as e:
            resultado['errores'].append(f'People: {e}')
        return resultado