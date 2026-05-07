from fastapi import FastAPI


app = FastAPI(title="noteapp-server")


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
