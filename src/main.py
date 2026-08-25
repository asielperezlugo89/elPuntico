# Punto de entrada para Wasmer Edge: expone src.main:app
from app import app  # noqa: F401

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
