from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Search Relevancy Suite")

app.mount("/static", StaticFiles(directory="portal/static"), name="static")


@app.get("/")
async def root():
    return FileResponse("portal/static/index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}
