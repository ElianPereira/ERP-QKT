from django.db import models


class PosicionLanding(models.TextChoices):
    TOP = 'top', 'Arriba'
    ARRIBA_CENTRO = '20%', 'Arriba-centro'
    CENTER = 'center', 'Centro'
    ABAJO_CENTRO = '80%', 'Abajo-centro'
    BOTTOM = 'bottom', 'Abajo'


class ModoDescuento(models.TextChoices):
    MANUAL = 'MANUAL', 'Manual'
    AUTOMATICO = 'AUTOMATICO', 'Automático'
