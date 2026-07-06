import boto3
import os
import hmac
import hashlib
import json
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone

_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(_env_path)

GITHUB_SECRET = os.environ.get('GITHUB_SECRET', '')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
SQS_QUEUE_NAME = os.environ.get('SQS_QUEUE_NAME', '')
AWS_REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')

def push_message_to_sqs(queue_name, body):
    """
    Push a JSON-serialisable message body to the named SQS queue.
    Returns the SQS SendMessage response.
    """
    if not queue_name:
        raise ValueError("SQS_QUEUE_NAME environment variable is not set. Cannot push message.")

    sqs = boto3.resource('sqs', region_name=AWS_REGION)
    queue = sqs.get_queue_by_name(QueueName=queue_name)
    response = queue.send_message(
        MessageBody=json.dumps(body)
    )
    return response

def build_placeholder_comment(pr_details):
    """
    Builds a placeholder comment to be posted on the GitHub pull request.
    """
    head_sha_short = pr_details["head_sha"][:7]
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    return f"""## 🔍 AI Code Review

> ⏳ **Review in progress** — Analyzing your changes. This usually takes under a minute. Check back shortly.

---

### What's being analyzed

- 🐛 Bugs and logic errors
- 🔒 Security vulnerabilities  
- ⚡ Performance concerns
- 📖 Code readability and maintainability
- ✅ Best practices

---

### PR Details

| Field | Value |
|-------|-------|
| **Author** | {pr_details["author"]} |
| **Branch** | `{pr_details["head_branch"]}` → `{pr_details["base_branch"]}` |
| **Commit** | `{head_sha_short}` |
| **Triggered at** | {timestamp} UTC |

---

<sub>🤖 Powered by Amazon Bedrock · Results will replace this message automatically</sub>"""

def post_github_comment(issue_url, comment_body):
    """
    Post a comment to the GitHub pull request via the Issues API.
    """
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN environment variable is not set. Cannot post comment.")
    
    url = f"{issue_url}/comments"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    payload = {"body": comment_body}

    response = requests.post(url, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()

def github_verify(received_signature, body):
    """
    Verify if the request is coming from GitHub.
    """
    if not GITHUB_SECRET:
        raise ValueError("GITHUB_SECRET environment variable is not set. Cannot verify webhook signature.")
        
    hash_key = GITHUB_SECRET.encode('utf-8')
    calculated_signature = f"sha256={hmac.new(hash_key, msg=body.encode('utf-8'), digestmod=hashlib.sha256).hexdigest()}"

    if not hmac.compare_digest(calculated_signature, received_signature):
        return {
            "status": 401,
            "message": "Invalid Signature"
        }

    return None


def lambda_handler(event, context):
    body = event.get('body', None)

    if body is None:
        return {
            "status": 400,
            "message": "No Body received"
        }

    received_signature = event['headers']['x-hub-signature-256']

    verify_result = github_verify(received_signature, body)
    if verify_result is not None:
        return verify_result

    body = json.loads(body)

    event_type = event['headers']['x-github-event']

    if event_type == 'ping':
        return {
            "status": 200,
            "message": "Pong, Request Successful"
        }

    if event_type != 'pull_request':
        return {
            "status": 400,
            "message": f"Invalid Event Type : {event_type}"
        }

    if body['action'] not in ('opened', 'reopened', 'synchronize'):
        return {
            "status": 400,
            "message": f"Invalid Action : {body['action']}"
        }

    pull_request = body['pull_request']
    pull_request_issue_url = pull_request['issue_url']

    pr_details = {
        "author": pull_request['user']['login'],
        "head_sha": pull_request['head']['sha'],
        "head_branch": pull_request['head']['ref'],
        "base_branch": pull_request['base']['ref']
    }

    comment = build_placeholder_comment(pr_details)

    try:
        comment_response = post_github_comment(pull_request_issue_url, comment)
    except:
        comment_response = None

    comment_url = None if comment_response is None else comment_response.get('url', '')
    pull_request_url = pull_request.get('url', '')

    sqs_message = {
        "pull_request_url": pull_request_url,
        "pull_request_issue_url": pull_request_issue_url,
        "comment_url": comment_url,
    }
    push_message_to_sqs(SQS_QUEUE_NAME, sqs_message)

    return {
        "status": 200,
        "message": "Placeholder comment posted and review job queued successfully"
    }
