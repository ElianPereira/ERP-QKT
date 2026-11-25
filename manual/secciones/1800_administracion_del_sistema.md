---
titulo: Administrar el sistema: usuarios, responsables y procesos automáticos
capitulo: Administración del sistema
orden: 1800
roles: [DIRECCION]
---
### Usuarios y permisos

1. **Nuevo usuario**: en **Autenticación y Usuarios → Usuarios**, crea el usuario con contraseña y marca **Acceso al sitio de administración**. Agrégalo al **grupo** que corresponda; el grupo decide qué ve.

| Grupo | Qué recibe |
|---|---|
| Vendedor | Ventas: cotizar, cobrar, visitas. Sin costos ni cancelaciones |
| Ventas | El encargado: operación diaria |
| Contabilidad | El contador |
| Nómina | Nómina |

2. **Baja de alguien**: desactiva su usuario; **no lo borres**.
3. El usuario de **Dirección** es superusuario y pide código de verificación al entrar.
4. Si cambias los permisos de los grupos, el equipo técnico corre `python manage.py crear_grupos_permisos`; **no corre solo con el deploy.**

### A quién le llegan los avisos

En **Comunicación → Responsables** se define quién recibe los avisos de cada rol (Encargado, Dirección, Contador, Compras, Ventas):

1. Captura su teléfono de WhatsApp.
2. Al guardar un número nuevo, queda **pendiente** y el sistema le manda un mensaje: debe contestar **ACEPTO** desde ese teléfono. Hasta entonces, el número anterior sigue activo.
3. Puedes definir **suplentes** con rango de fechas.
4. Dirección puede recibir copia en vivo de los avisos del encargado.

**Equipo de apoyo temporal** (por ejemplo, mientras Dirección viaja): en el mismo apartado elige el rol **Equipo de apoyo** para cada persona. Siempre lleva fechas **desde** y **hasta**; al terminar el rango deja de recibir avisos sin que tengas que hacer nada. Marca qué recibe cada quien:

- **Parte del día**: a las 8:05 a.m., los servicios confirmados de hoy y mañana (folio, horario y personas).
- **Copia de pendientes del encargado**: los avisos urgentes del encargado, solo para enterarse.
- **Llegadas**: cliente o visita que llegó, con botón para marcarlo recibido. El parte del día le muestra además el nombre y teléfono del cliente y las visitas de hoy.
- **Pagos**: pagos recibidos y, en el parte del día, el saldo por cobrar de cada servicio.
- **Compras**: la lista de compras de barra, con botón «Ya compré».

El equipo de apoyo **no decide sobre dinero** ni sustituye al encargado: para que alguien atienda todo lo del encargado, dalo de alta como **suplente** del rol Encargado con su rango de fechas. Igual que cualquier responsable, cada número debe contestar **ACEPTO** antes de recibir avisos.

### Procesos automáticos (el Programador)

Un solo servicio de Railway corre `programador` cada 5 minutos y ejecuta las tareas del día: cierre de cotizaciones, recordatorios, guías, seguimientos, descarga del SAT, avisos de vencimientos, resúmenes y más. Cada corrida queda en **Operaciones → Ejecuciones**.

- Si la última corrida tiene más de 30 minutos, aparece un aviso rojo arriba en todas las páginas.
- Una tarea en error o que no corrió abre un pendiente urgente para la Dirección.
- Una ranura que se pasa de su tolerancia **no se recupera sola**: se avisa.

### Variables de entorno

Las llaves y configuraciones viven solo en Railway; el catálogo con el nombre, para qué sirve y dónde se rota cada una está en `docs/variables_entorno.md`. Nunca se pegan valores en chats ni en Issues. Las variables de los demás servicios apuntan a las de `web`, no llevan valores copiados.

### Las tres rutinas automáticas

Tres rutinas programadas revisan el repositorio y entregan reportes por Pull Request: **Técnica**, **Operativa/Contable** y **Seguridad**. Nunca hacen merge ni migraciones. Sus reportes quedan en `docs/`.

### Lo que no se hace

:::nohagas
Correr migraciones o escribir directo en la base de producción a mano. Los cambios de esquema salen solo al unir cambios revisados: el despliegue corre las migraciones pendientes.
:::
