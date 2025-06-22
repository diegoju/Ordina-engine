from fastapi import FastAPI
import json
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Permitir cualquier origen (útil para desarrollo/testing)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

with open("IdLegislaciones.json", encoding="utf-8") as f:
    leyes = json.load(f)

@app.get("/")
def read_root():
    return {"mensaje": "API de Legislaciones funcionando en Vercel"}

@app.get("/ley")
def buscar_ley(id: int = None, categoria: int = None, nombre: str = None):
    resultados = leyes
    if id is not None:
        resultados = [l for l in resultados if l["id"] == id]
    if categoria is not None:
        resultados = [l for l in resultados if l["categoria"] == categoria]
    if nombre is not None:
        resultados = [l for l in resultados if nombre.lower() in l["nombre"].lower()]
    return JSONResponse(content=resultados)