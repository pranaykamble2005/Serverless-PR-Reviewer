# 🤖 Serverless GitHub PR Reviewer

An event-driven, fully serverless AI code reviewer that automatically analyzes GitHub Pull Requests using **Google Gemini** and posts structured, contextual feedback as PR comments — powered entirely by **AWS Lambda** and **Amazon SQS**.


## 🌐 Overview

When a developer opens a Pull Request, this system automatically:

1. **Receives** the GitHub webhook event via an AWS Lambda function exposed through a Function URL.
2. **Posts an instant placeholder comment** on the PR so developers know a review is in progress.
3. **Queues a review job** into Amazon SQS for decoupled, asynchronous processing.
4. **Consumes** the queued job via a second Lambda (the Reviewer), fetches the PR diff from GitHub, and sends it to **Google Gemini** for analysis.
5. **Updates the placeholder comment** on GitHub with the full, structured AI review — covering bugs, security issues, performance, and code quality.

The two-Lambda architecture ensures the webhook HTTP response is **near-instant** (GitHub has a short timeout), while heavy AI processing happens asynchronously in the background.

---

## 🏗 System Architecture

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              GitHub                                        │
│                                                                            │
│   Developer opens a PR  ──►  Webhook fires  ──►  PR Comment (updated)     │
│                                    │                        ▲              │
└────────────────────────────────────┼────────────────────────┼──────────────┘
                                     │ HTTP POST              │ PATCH /comments
                                     ▼                        │
┌────────────────────────────────────────────────────────────────────────────┐
│                           AWS Cloud                                        │
│                                                                            │
│  ┌─────────────────────────────┐       ┌──────────────────────────────┐   │
│  │   Receiver Lambda           │       │   Reviewer Lambda            │   │
│  │   (Function URL / API GW)   │       │   (SQS Event Source Mapping) │   │
│  │                             │       │                              │   │
│  │  1. Verify HMAC signature   │       │  1. Fetch PR files from GH   │   │
│  │  2. Parse PR event          │──SQS──►  2. Build diff context       │   │
│  │  3. Post placeholder cmt    │  msg  │  3. Call Google Gemini       │   │
│  │  4. Enqueue SQS message     │       │  4. PATCH placeholder cmt    │   │
│  │  5. Return 200 immediately  │       │  5. Delete SQS message       │   │
│  └─────────────────────────────┘       └──────────────────────────────┘   │
│                                                    │                       │
│                         ┌──────────────────────────┘                      │
│                         ▼                                                  │
│              ┌──────────────────────┐                                      │
│              │   Amazon SQS Queue   │                                      │
│              │   (Standard Queue)   │                                      │
│              │                      │                                      │
│              │  Decouples webhook   │                                      │
│              │  receipt from AI     │                                      │
│              │  processing          │                                      │
│              └──────────────────────┘                                      │
└────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     │ generate_content()
                                     ▼
                          ┌──────────────────────┐
                          │   Google Gemini API   │
                          │   (gemini-2.5-flash)  │
                          └──────────────────────┘
```

---

## 📋 Table of Contents

- [Overview](#-overview)
- [System Architecture](#-system-architecture)
- [Data Flow](#-data-flow)
- [Project Structure](#-project-structure)
- [AWS Infrastructure](#-aws-infrastructure)
- [Component Deep Dive](#-component-deep-dive)
  - [Receiver Lambda](#receiver-lambda-receiverhandlerpy)
  - [Reviewer Lambda](#reviewer-lambda-reviewerhandlerpy)
  - [Local Development Harness](#local-development-harness-local)
- [Security Design](#-security-design)
- [Prerequisites](#-prerequisites)
- [Setup & Configuration](#-setup--configuration)
  - [1. AWS SQS Setup](#1-aws-sqs-setup)
  - [2. GitHub Setup](#2-github-setup)
  - [3. Google Gemini Setup](#3-google-gemini-setup)
  - [4. Configure Environment Variables](#4-configure-environment-variables)
  - [5. Deploy Lambda Functions](#5-deploy-lambda-functions)
- [Local Development & Testing](#-local-development--testing)
- [Retry & Resilience Strategy](#-retry--resilience-strategy)
- [Future Improvements](#-future-improvements)

---

## 🔄 Data Flow

The full end-to-end lifecycle of a single PR review is documented below.

### Step 1 — GitHub fires the Webhook

When a developer opens a Pull Request, GitHub sends a `POST` request to the **Receiver Lambda's Function URL**. The request includes:
- `x-github-event: pull_request` header
- `x-hub-signature-256` HMAC header (for authenticity verification)
- A JSON body containing the full PR event payload

### Step 2 — Receiver Lambda validates and processes the event

```
GitHub Webhook POST
       │
       ▼
[1] Extract headers & body
       │
       ▼
[2] Verify HMAC-SHA256 signature
    (GITHUB_SECRET used as key)
    → 401 if signature mismatch
       │
       ▼
[3] Check event type == 'pull_request'
    Check action == 'opened'
    → 400 otherwise
       │
       ▼
[4] Extract PR metadata:
    - author, head_sha, branches,
      pull_request_url, issue_url
       │
       ▼
[5] POST placeholder comment to GitHub PR
    (tells developer that review is starting)
    → Returns comment_url for later update
       │
       ▼
[6] Enqueue SQS message:
    {
      "pull_request_url": "...",
      "pull_request_issue_url": "...",
      "comment_url": "..."
    }
       │
       ▼
[7] Return HTTP 200 immediately
```

### Step 3 — SQS buffers the job

The SQS Standard Queue acts as a durable buffer. The message persists until the Reviewer Lambda successfully processes it. If the reviewer fails, SQS applies the **visibility timeout**, hides the message temporarily, and makes it available again for a retry — up to the **maximum receive count**, after which it can be routed to a Dead Letter Queue (DLQ).

**SQS Message Schema:**
```json
{
  "pull_request_url": "https://api.github.com/repos/owner/repo/pulls/42",
  "pull_request_issue_url": "https://api.github.com/repos/owner/repo/issues/42",
  "comment_url": "https://api.github.com/repos/owner/repo/issues/comments/123456"
}
```

### Step 4 — Reviewer Lambda consumes the SQS message

The Reviewer Lambda is triggered automatically via an **SQS Event Source Mapping** (batch size configurable). For each record:

```
SQS Record
      │
      ▼
[1] Parse JSON body from SQS record
    → pull_request_url, issue_url, comment_url
      │
      ▼
[2] Fetch changed files:
    GET {pull_request_url}/files
    → list of file diffs with patches
      │
      ▼
[3] Filter and budget diffs:
    - Skip files with > 400 changed lines (per file)
    - Stop when total diff exceeds 2000 lines
    - Track skipped files for the review summary
      │
      ▼
[4] Build structured LLM prompt:
    - Inject diffs inside UNTRUSTED CONTENT delimiters
    - Request structured output: Summary, Bugs,
      Security, Performance, Code Quality, Verdict
      │
      ▼
[5] Call Google Gemini API (with exponential backoff)
    → Returns review text (Markdown)
      │
      ▼
[6] PATCH the placeholder GitHub comment
    (or POST new comment if no placeholder_url)
      │
      ▼
[7] Return success → Lambda SQS Event Source
    deletes the message automatically
    (batchItemFailures reported for failed ones)
```

### Step 5 — GitHub comment is updated

The placeholder comment, originally reading *"Review in progress..."*, is replaced with the full Gemini-generated review containing:

| Section | Content |
|---|---|
| **Summary** | Brief overall assessment of the PR |
| **🐛 Bugs** | Logic errors and incorrect behavior |
| **🔒 Security** | Vulnerabilities and attack vectors |
| **⚡ Performance** | Bottlenecks and inefficient patterns |
| **📖 Code Quality** | Readability, naming, best practices |
| **Suggestions** | Actionable improvement steps |
| **Verdict** | ✅ Approved / ⚠️ Approved with suggestions / ❌ Changes requested |

---

## 📁 Project Structure

```
Serverless GitHub PR Reviewer/
│
├── receiver/                   # Receiver Lambda function
│   ├── handler.py              # Lambda entry point: validates webhook, queues to SQS
│   ├── requirements.txt        # Python dependencies
│   ├── .env                    # Local environment variables (not committed)
│   ├── package/                # Vendored dependencies for Lambda ZIP deployment
│   └── my_deployment_package.zip  # Deployable Lambda ZIP archive
│
├── reviewer/                   # Reviewer Lambda function
│   ├── handler.py              # Lambda entry point: fetches PR diff, calls Gemini, posts review
│   ├── requirements.txt        # Python dependencies
│   ├── .env                    # Local environment variables (not committed)
│   ├── package/                # Vendored dependencies for Lambda ZIP deployment
│   └── reviewer_lambda.zip     # Deployable Lambda ZIP archive
│
├── local/                      # Local development and testing harness
│   ├── server.py               # FastAPI wrapper: exposes receiver/handler.py as /webhook
│   ├── test_webhook.py         # Script: sends a mock signed PR webhook event to local server
│   └── poll_reviewer.py        # Script: polls SQS and invokes reviewer/handler.py locally
│
└── README.md
```

---

## ☁️ AWS Infrastructure

This project is built entirely on **AWS Serverless** services — zero server management required.

### Amazon SQS (Simple Queue Service)

| Property | Value |
|---|---|
| **Queue Type** | Standard Queue |
| **Message Retention** | 4 days (default) |
| **Visibility Timeout** | Must exceed Reviewer Lambda timeout (e.g., 5–10 minutes) |
| **Delivery** | At-least-once (idempotent review posting is recommended) |
| **Trigger** | Event Source Mapping attached to Reviewer Lambda |

> [!IMPORTANT]
> The SQS queue and both Lambda functions **must be in the same AWS region**. The Receiver Lambda's IAM role must have `sqs:SendMessage` permission, and the Reviewer Lambda's role must have `sqs:ReceiveMessage`, `sqs:DeleteMessage`, and `sqs:GetQueueAttributes`.

### AWS Lambda — Receiver

| Property | Value |
|---|---|
| **Runtime** | Python 3.12+ |
| **Trigger** | Lambda Function URL (or API Gateway HTTP API) |
| **Timeout** | 10–15 seconds (webhook response must be fast) |
| **IAM Permissions** | `sqs:SendMessage` on the PR review queue |
| **Handler** | `handler.lambda_handler` |

### AWS Lambda — Reviewer

| Property | Value |
|---|---|
| **Runtime** | Python 3.12+ |
| **Trigger** | SQS Event Source Mapping |
| **Timeout** | 3–5 minutes (AI generation + GitHub API calls) |
| **Batch Size** | 1 (recommended, to avoid partial batch failures) |
| **IAM Permissions** | `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes` |
| **Handler** | `handler.lambda_handler` |

### IAM Policy — Receiver Lambda Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sqs:SendMessage",
      "Resource": "arn:aws:sqs:<REGION>:<ACCOUNT_ID>:<QUEUE_NAME>"
    }
  ]
}
```

### IAM Policy — Reviewer Lambda Role

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes"
      ],
      "Resource": "arn:aws:sqs:<REGION>:<ACCOUNT_ID>:<QUEUE_NAME>"
    }
  ]
}
```

---

## 🔍 Component Deep Dive

### Receiver Lambda (`receiver/handler.py`)

**Responsibilities:**
- Validate HMAC-SHA256 webhook signature from GitHub using a shared secret
- Filter to only process `pull_request` → `opened` events
- Post an immediate "review in progress" placeholder comment to the PR
- Serialize the PR context (`pull_request_url`, `issue_url`, `comment_url`) and push to SQS
- Return an HTTP 200 within GitHub's webhook timeout window

**Key design decisions:**
- **Fail-safe placeholder posting**: If the GitHub comment POST fails, the SQS message is still enqueued. The Reviewer falls back to creating a new comment.
- **Signature verification first**: HMAC check happens before any business logic to reject unauthenticated requests early.

---

### Reviewer Lambda (`reviewer/handler.py`)

**Responsibilities:**
- Process SQS records in batches (returns `batchItemFailures` for partial-batch error handling)
- Fetch PR file diffs from GitHub's REST API
- Apply diff size budgeting (per-file: 400 lines, total: 2000 lines) to avoid LLM context overflow
- Build a prompt with **prompt injection mitigation** — diffs are wrapped in `UNTRUSTED CONTENT` delimiters
- Call Google Gemini with exponential backoff for transient errors
- PATCH the placeholder comment or POST a new one with the review result
- Post a failure notice if all retries are exhausted

**Retry strategy:**

| Operation | Max Retries | Backoff Base | Retried Errors |
|---|---|---|---|
| Gemini API | 5 | 2.0s | `ResourceExhausted`, `ServiceUnavailable`, `DeadlineExceeded`, `InternalServerError` |
| GitHub PATCH/POST | 5 | 2.0s | HTTP 500/502/503/504, network errors |
| GitHub GET (PR files) | 5 | N/A (no sleep) | Any `RequestException` |

**Batch item failure reporting:**  
The `lambda_handler` returns `{"batchItemFailures": [...]}`, which tells SQS to **not delete** failed messages and retry them according to the queue's visibility timeout — preventing data loss without blocking successful messages in the same batch.

---

### Local Development Harness (`local/`)

Three helper scripts allow you to run and test the full pipeline on your machine without deploying to AWS Lambda.

| Script | Purpose |
|---|---|
| `server.py` | FastAPI wrapper that imports `receiver/handler.py` and exposes it at `POST /webhook` |
| `test_webhook.py` | Sends a mock, HMAC-signed `pull_request` event to the local FastAPI server |
| `poll_reviewer.py` | Polls the real SQS queue and invokes `reviewer/handler.py` locally for each message |

---

## 🔒 Security Design

| Threat | Mitigation |
|---|---|
| **Spoofed webhook requests** | HMAC-SHA256 signature verification using `GITHUB_SECRET`. `hmac.compare_digest` prevents timing attacks. |
| **Prompt injection via PR content** | Each file diff is wrapped inside explicit `--- BEGIN/END UNTRUSTED USER-SUBMITTED CODE ---` delimiters with a standing instruction to ignore embedded directives. |
| **Credential exposure** | Secrets are loaded from `.env` files (never committed). Lambda uses environment variables injected at deploy time. |
| **Over-processing large PRs** | Hard diff budget (400 lines/file, 2000 lines total). Large files are listed in the review as skipped with a note for manual review. |

---

## ✅ Prerequisites

- **AWS Account** with permissions to create Lambda functions, SQS queues, and IAM roles
- **Python 3.12+** installed locally
- **GitHub Account** with admin access to a repository (to configure webhooks)
- **Google Cloud / AI Studio Account** with a Gemini API key
- `pip`, `boto3`, `uvicorn`, `fastapi`, `python-dotenv` installed in your local environment

---

## ⚙️ Setup & Configuration

### 1. AWS SQS Setup

**a) Create the SQS Queue**

1. Log in to the [AWS Console](https://console.aws.amazon.com/sqs).
2. Navigate to **SQS → Create Queue**.
3. Choose **Standard Queue** (not FIFO).
4. Set a **Queue Name** (e.g., `github-pr-review-queue`). Note it exactly — it will be used in `.env`.
5. Set **Visibility Timeout** to at least `300` seconds (5 minutes) to cover Reviewer Lambda's execution time.
6. Optionally configure a **Dead Letter Queue (DLQ)** for messages that exceed the maximum receive count (recommended: 3–5 retries).
7. Click **Create Queue** and copy the **Queue URL** and **ARN**.

**b) Create an IAM User for Local Development**

1. Navigate to **IAM → Users → Create User**.
2. Attach the following inline policy (replace placeholders):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage",
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:GetQueueAttributes",
        "sqs:GetQueueUrl"
      ],
      "Resource": "arn:aws:sqs:<REGION>:<ACCOUNT_ID>:<QUEUE_NAME>"
    }
  ]
}
```

3. Go to **Security credentials → Create access key** (use *Local code* as the use case).
4. Save the **Access Key ID** and **Secret Access Key** — these go into your `.env` files.

**c) Connect the Reviewer Lambda to SQS (Event Source Mapping)**

1. Navigate to your Reviewer Lambda in the [Lambda Console](https://console.aws.amazon.com/lambda).
2. Under **Configuration → Triggers**, click **Add Trigger**.
3. Select **SQS** and choose your queue.
4. Set **Batch size** to `1`.
5. Enable the trigger.

---

### 2. GitHub Setup

**a) Create a Personal Access Token (PAT)**

1. Go to **GitHub → Settings → Developer settings → Personal Access Tokens → Fine-grained tokens**.
2. Click **Generate new token**.
3. Select **Repository access** → choose the specific repository.
4. Under **Permissions**, grant:
   - `Pull requests` → **Read and write** (to post/patch comments)
   - `Contents` → **Read-only** (to fetch file diffs via the API)
5. Click **Generate token** and copy it immediately — this is your `GITHUB_TOKEN`.

**b) Configure the Webhook**

1. In your repository, go to **Settings → Webhooks → Add webhook**.
2. Set:
   - **Payload URL**: Your Receiver Lambda's Function URL (e.g., `https://xxxxxxxx.lambda-url.us-east-1.on.aws/`)
   - **Content type**: `application/json`
   - **Secret**: A strong random string (e.g., `openssl rand -hex 32`) — this is your `GITHUB_SECRET`
   - **Which events?**: Select *Let me select individual events* → check only **Pull requests**
3. Click **Add webhook**.

> [!TIP]
> For local development, use [ngrok](https://ngrok.com/) or [smee.io](https://smee.io/) to expose your local FastAPI server to GitHub's webhook delivery. Set the tunnel URL as the Payload URL.

---

### 3. Google Gemini Setup

1. Visit [Google AI Studio](https://aistudio.google.com/app/apikey).
2. Click **Create API Key** and choose or create a Google Cloud project.
3. Copy the **API Key** — this is your `GEMINI_API_KEY`.
4. The `google-genai` SDK will automatically read it from the `GEMINI_API_KEY` environment variable (or `GOOGLE_API_KEY`).

> [!NOTE]
> The default model is configured as `gemini-2.5-flash` in `reviewer/handler.py`. You can override this via the `GEMINI_MODEL` environment variable. Check [Google AI Studio](https://aistudio.google.com) for the latest available model names and their context window limits.

---

### 4. Configure Environment Variables

Create a `.env` file in **both** the `receiver/` and `reviewer/` directories.

**`receiver/.env`**
```env
# GitHub Webhook secret — must match what you set in the GitHub webhook settings
GITHUB_SECRET=your_github_webhook_secret_here

# GitHub PAT — needs pull_request write permissions to post comments
GITHUB_TOKEN=ghp_your_github_personal_access_token_here

# SQS Queue name (NOT the full URL — just the name)
SQS_QUEUE_NAME=github-pr-review-queue

# AWS credentials for local development (not needed if running on Lambda with an IAM role)
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_DEFAULT_REGION=us-east-1
```

**`reviewer/.env`**
```env
# GitHub PAT — needs pull_request read permissions to fetch PR file diffs
GITHUB_TOKEN=ghp_your_github_personal_access_token_here

# Google Gemini API Key
GEMINI_API_KEY=your_gemini_api_key_here

# Gemini model to use (optional, defaults to gemini-2.5-flash)
GEMINI_MODEL=gemini-2.5-flash

# SQS Queue name
SQS_QUEUE_NAME=github-pr-review-queue

# AWS credentials for local development
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_DEFAULT_REGION=us-east-1
```

> [!CAUTION]
> **Never commit `.env` files to version control.** They are already listed in `.gitignore`. For Lambda deployments, set these as **Lambda Environment Variables** in the AWS Console or via your IaC tool (CDK, Terraform, SAM). Do not bundle them into your ZIP package.

---

### 5. Deploy Lambda Functions

**a) Package dependencies for Lambda**

Both Lambda functions require dependencies vendored alongside the handler. Run the following from the project root:

```bash
# Package Receiver Lambda
cd receiver
pip install -r requirements.txt -t package/
cd package && zip -r ../my_deployment_package.zip . && cd ..
zip my_deployment_package.zip handler.py
cd ..

# Package Reviewer Lambda
cd reviewer
pip install -r requirements.txt -t package/
cd package && zip -r ../reviewer_lambda.zip . && cd ..
zip reviewer_lambda.zip handler.py
cd ..
```

**b) Deploy to AWS Lambda**

```bash
# Deploy Receiver Lambda (update the function name to match yours)
aws lambda update-function-code \
  --function-name github-pr-receiver \
  --zip-file fileb://receiver/my_deployment_package.zip \
  --region us-east-1

# Deploy Reviewer Lambda
aws lambda update-function-code \
  --function-name github-pr-reviewer \
  --zip-file fileb://reviewer/reviewer_lambda.zip \
  --region us-east-1
```

**c) Set Lambda Environment Variables**

```bash
aws lambda update-function-configuration \
  --function-name github-pr-receiver \
  --environment "Variables={GITHUB_SECRET=...,GITHUB_TOKEN=...,SQS_QUEUE_NAME=...,AWS_DEFAULT_REGION=us-east-1}"

aws lambda update-function-configuration \
  --function-name github-pr-reviewer \
  --environment "Variables={GITHUB_TOKEN=...,GEMINI_API_KEY=...,SQS_QUEUE_NAME=...,GEMINI_MODEL=gemini-2.5-flash}"
```

**d) Configure the Receiver Lambda Function URL**

```bash
aws lambda create-function-url-config \
  --function-name github-pr-receiver \
  --auth-type NONE \
  --cors '{"AllowOrigins":["*"],"AllowMethods":["POST"]}'
```

> [!NOTE]
> Using `NONE` auth type is acceptable here because the webhook is protected by HMAC-SHA256 signature verification in application code. For stricter setups, place an API Gateway with a WAF in front.

---

## 🧪 Local Development & Testing

You can run the entire pipeline locally without deploying to Lambda.

### Step 1: Start the Local Receiver Server

```bash
cd local
uvicorn server:app --reload --port 8000
```

This starts a FastAPI server at `http://127.0.0.1:8000` that wraps `receiver/handler.py`.

### Step 2: Send a Mock Webhook Event

In a new terminal:

```bash
cd local
python test_webhook.py
```

This script:
1. Constructs a mock `pull_request` opened event payload.
2. Signs it with your `GITHUB_SECRET` from `receiver/.env`.
3. POSTs it to `http://127.0.0.1:8000/webhook`.
4. Prints the server's response.

Expected output:
```
Sending webhook test request to http://127.0.0.1:8000/webhook...

SUCCESS!
Status Code: 200
Response: {"status": 200, "message": "Placeholder comment posted and review job queued successfully"}
```

### Step 3: Run the Local Reviewer Poller

```bash
cd local
python poll_reviewer.py
```

This script connects to your real SQS queue, polls for messages (long polling, 10s), and invokes `reviewer/handler.py` for each message — exactly as the Lambda SQS trigger would.

### Step 4: Verify the GitHub PR Comment

Check the PR on GitHub (the `issue_url` in your mock payload). You should see:
1. The placeholder *"Review in progress"* comment (posted by the Receiver).
2. Updated to the full Gemini review (patched by the Reviewer).

---

## 🔁 Retry & Resilience Strategy

The system is designed to be resilient to transient failures at every layer:

```
Failure Scenario                  │  Recovery Mechanism
──────────────────────────────────┼───────────────────────────────────────────
GitHub webhook delivery fails     │  GitHub retries automatically for 72h
Receiver Lambda cold start/crash  │  GitHub retries; SQS not written = no ghost job
Gemini API rate limit / 503       │  Exponential backoff (up to 5 retries, ~32s max wait)
GitHub PATCH fails (5xx)          │  Exponential backoff (up to 5 retries)
Reviewer Lambda crashes mid-job   │  SQS visibility timeout expires → message retried
All retries exhausted             │  Failure notice posted to PR; message deleted cleanly
SQS message exceeds receive count │  Routes to DLQ (if configured) for manual inspection
```

---

## 🚀 Future Improvements

The following enhancements would take this project from a working prototype to a production-grade system:

### Infrastructure & Deployment
- **Infrastructure as Code (IaC)** — Define all AWS resources (Lambda, SQS, IAM roles, Function URLs) using **AWS CDK**, **Terraform**, or **AWS SAM** for reproducible, version-controlled deployments.
- **Dead Letter Queue (DLQ)** — Add a DLQ for the SQS queue and a CloudWatch alarm to alert on messages landing there.
- **API Gateway + WAF** — Replace the raw Lambda Function URL with an API Gateway HTTP API fronted by AWS WAF to enable rate limiting, IP allowlisting, and request validation.
- **CI/CD Pipeline** — Automate deployments using **GitHub Actions** (ironic but appropriate): on push to `main`, automatically bundle and deploy both Lambda ZIPs.

### Features
- **Support for `synchronize` events** — Currently only `opened` PRs trigger a review. Extend to also review `synchronize` (new commits pushed) events.
- **Inline PR Review Comments** — Use GitHub's [Pull Request Review API](https://docs.github.com/en/rest/pulls/reviews) to post comments **on specific lines of code** rather than a general PR comment, for more precise feedback.
- **Configurable `.prreviewer.yml`** — Allow repository owners to place a config file in their repo controlling review sensitivity, skipped file patterns (e.g., `*.lock`, `migrations/`), and preferred review depth.
- **Multi-model support** — Abstract the LLM layer to support switching between Gemini, Claude (via AWS Bedrock), or OpenAI GPT models via a config flag.
- **PR size labeling** — Automatically label PRs as `small`, `medium`, or `large` based on the total diff size.

### Observability
- **Structured logging** — Replace `print()` statements with structured JSON logging using Python's `logging` module, enabling easy CloudWatch Insights queries.
- **AWS X-Ray Tracing** — Enable active tracing on both Lambdas to trace the full request lifecycle from SQS message receipt to GitHub API response.
- **CloudWatch Dashboard** — Build a dashboard tracking: Lambda invocation counts, error rates, Gemini API latency, SQS queue depth, and GitHub API success rates.
- **Review Quality Metrics** — Store review metadata (PR size, model used, latency, verdict) in DynamoDB for analytics on review quality over time.

### Security Hardening
- **AWS Secrets Manager** — Move all secrets (`GITHUB_TOKEN`, `GEMINI_API_KEY`, `GITHUB_SECRET`) out of Lambda environment variables and into **AWS Secrets Manager**, with Lambda fetching them at runtime via the Secrets Manager extension.
- **VPC deployment** — Place both Lambdas inside a VPC with private subnets and a NAT Gateway, restricting outbound internet access to only required endpoints.
- **Fine-grained GitHub App** — Replace the PAT with a proper **GitHub App** installation, scoped to only the required repositories and permissions, with short-lived installation tokens.

---

## 📄 License

This project is for personal learning and experimentation with AWS serverless services, the GitHub Webhook API, and Google Gemini. Feel free to fork and adapt it.

---

<sub>Built with ❤️ using AWS Lambda · Amazon SQS · Google Gemini · GitHub Webhooks API</sub>
