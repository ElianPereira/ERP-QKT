#!/usr/bin/env python3
"""Redimensiona una imagen antes de pasarla a Read, para no gastar tokens
en resolución que la tarea no necesita (ej. confirmar layout, no leer
texto fino). Usa Pillow, ya es dependencia directa del proyecto."""
import sys

MAX_LADO = 1568  # suficiente para que Claude lea texto de UI normal


def main() -> int:
    if len(sys.argv) != 3:
        print(f"Uso: {sys.argv[0]} <entrada> <salida>", file=sys.stderr)
        return 1

    try:
        from PIL import Image
    except ImportError:
        print("Falta Pillow: activa el venv del proyecto.", file=sys.stderr)
        return 1

    entrada, salida = sys.argv[1], sys.argv[2]
    with Image.open(entrada) as img:
        img.thumbnail((MAX_LADO, MAX_LADO))
        img.save(salida)
    print(salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
