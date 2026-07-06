import json
from fastapi import FastAPI, Request
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../receiver"))
from handler import lambda_handler

app = FastAPI()

@app.post("/webhook")
async def github_webhook(request: Request):
    headers = dict(request.headers)
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")

    event_payload = {
        "headers": headers,
        "body": body_str,
    }

    result = lambda_handler(event_payload, None)
    return result