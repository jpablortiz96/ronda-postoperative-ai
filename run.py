"""Arranque de RONDA:  python run.py  →  http://localhost:8000

Se enlaza en modo DUAL-STACK (IPv6 + IPv4 en el mismo socket).

Motivo: `localhost` resuelve a ::1 antes que a 127.0.0.1 en Windows y macOS.
Enlazando solo 0.0.0.0 (IPv4), el navegador carga la página porque para HTTP
reintenta con IPv4, pero el WebSocket falla contra ::1 y la llamada de voz
queda muerta con un error de conexión. Con un socket dual-stack funcionan
ambas familias, se escriba localhost, 127.0.0.1 o [::1].
"""
import socket

import uvicorn

HOST_V6 = "::"
HOST_V4 = "0.0.0.0"
PUERTO = 8000


def crear_socket():
    """Socket dual-stack; si el sistema no ofrece IPv6, cae a IPv4."""
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        # En Windows IPV6_V6ONLY viene activado por defecto y dejaría fuera a
        # IPv4; en Linux ya viene desactivado. Se fuerza en ambos.
        s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST_V6, PUERTO))
        s.listen(128)
        s.set_inheritable(True)
        return s, f"[::]:{PUERTO} (IPv6 + IPv4)"
    except OSError:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((HOST_V4, PUERTO))
        s.listen(128)
        s.set_inheritable(True)
        return s, f"{HOST_V4}:{PUERTO} (solo IPv4)"


if __name__ == "__main__":
    sock, descripcion = crear_socket()
    print(f"RONDA escuchando en {descripcion}  ->  http://localhost:{PUERTO}")
    servidor = uvicorn.Server(uvicorn.Config("app.main:app", reload=False))
    servidor.run(sockets=[sock])
