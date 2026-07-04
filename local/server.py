from fastapi import FastAPI, Request
from handler import lambda_handler

app = FastAPI()

@app.post("/webhook")
async def github_webhook(request: Request):
    headers = dict(request.headers)
    body = await request.json()

    event_payload = {headers: headers, body: body}
    lambda_handler(event, None)