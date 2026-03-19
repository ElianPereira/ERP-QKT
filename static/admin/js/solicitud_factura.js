// Función para marcar solicitud como enviada después de abrir WhatsApp
function marcarEnviada(solicitudId, metodo) {
    fetch('/admin/facturacion/solicitudfactura/' + solicitudId + '/marcar_enviada/?metodo=' + metodo)
        .then(function(response) { return response.json(); })
        .then(function(data) {
            if (data.status === 'ok') {
                setTimeout(function() { location.reload(); }, 2000);
            }
        })
        .catch(function(err) { console.error('Error:', err); });
}