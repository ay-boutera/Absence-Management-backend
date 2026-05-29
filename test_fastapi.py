from fastapi import FastAPI, Depends
from fastapi.security import HTTPBearer

security = HTTPBearer()
app = FastAPI()

@app.get("/")
def get_root(auth = Depends(security)):
    return {"hello": "world"}
