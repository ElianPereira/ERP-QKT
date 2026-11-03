"""System checks propios (Issue #460)."""
from django.conf import settings
from django.core.checks import Tags, register
from django.core.checks import Warning as CheckWarning

from core_erp.storages_qkt import _hay_bucket_privado


@register(Tags.security, deploy=True)
def bucket_privado_configurado(app_configs, **kwargs):
    if _hay_bucket_privado():
        return []
    return [CheckWarning(
        'Falta CLOUDFLARE_R2_PRIVADO_BUCKET: INE, ARCO, nómina y estados de cuenta '
        'se guardarían en el bucket público.',
        hint='Define la variable (y ERP_R2_PRIVADO_OBLIGATORIO=True) en Railway.',
        id='core_erp.W001',
    )] if not getattr(settings, 'DEBUG', False) else []
