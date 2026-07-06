import hmac
import hashlib
import json
import os
import sys
import urllib.request
from dotenv import load_dotenv

# Load environment variables from receiver/.env
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../receiver/.env"))
load_dotenv(dotenv_path)

GITHUB_SECRET = os.environ.get('GITHUB_SECRET', 'this_is_a_very_bad_webhook_secret')

# Mock GitHub Pull Request Webhook Event Payload
# Can specify action via CLI arg (e.g., opened, reopened, synchronize)
action = "opened"
if len(sys.argv) > 1:
    action = sys.argv[1]
    print(f"Using CLI argument action: '{action}'")

payload = {
    "action": action,
    "pull_request": {
        "url": "https://api.github.com/repos/octocat/Hello-World/pulls/1347",
        "number": 1347,
        "issue_url": "https://api.github.com/repos/octocat/Hello-World/issues/1347",
        "user": {
            "login": "octocat"
        },
        "head": {
            "sha": "6dcb09b5b57875f334f61aebed695e2e4193db5e",
            "ref": "new-feature"
        },
        "base": {
            "ref": "main"
        }
    }
}

payload_bytes = json.dumps(payload).encode('utf-8')

# Calculate GitHub Webhook HMAC signature
hash_key = GITHUB_SECRET.encode('utf-8')
signature = "sha256=" + hmac.new(hash_key, msg=payload_bytes, digestmod=hashlib.sha256).hexdigest()

# Send request to local FastAPI server
url = "http://127.0.0.1:8000/webhook"
req = urllib.request.Request(url, data=payload_bytes, method="POST")
req.add_header("Content-Type", "application/json")
req.add_header("X-GitHub-Event", "pull_request")
req.add_header("X-Hub-Signature-256", signature)

print(f"Sending webhook test request to {url}...")
try:
    with urllib.request.urlopen(req) as response:
        status_code = response.getcode()
        response_body = response.read().decode('utf-8')
        print(f"\nSUCCESS!")
        print(f"Status Code: {status_code}")
        print(f"Response: {response_body}")
except urllib.error.HTTPError as e:
    error_body = e.read().decode('utf-8')
    print(f"\nHTTP ERROR: {e.code} - {e.reason}")
    print(f"Response: {error_body}")
except urllib.error.URLError as e:
    print(f"\nCONNECTION FAILED: {e.reason}")
    print("Please make sure your FastAPI local server is running with 'uvicorn server:app' in the 'local' directory.")
