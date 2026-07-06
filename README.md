# Serverless GitHub PR Reviewer

An AI-powered Pull Request reviewer built using AWS serverless components and the Gemini API.

## Features
- **Webhook Receiver**: A lightweight lambda function that listens to GitHub PR webhooks (`opened`, `reopened`, `synchronize`), posts an initial placeholder comment, and queues the job.
- **Reviewer Engine**: A background consumer that processes PR diffs and generates structured code reviews using Gemini.

## Local Testing
To test the webhook handler locally:
1. Start the local server:
   ```bash
   cd local
   python server.py
   ```
2. In another terminal, trigger a mock webhook:
   ```bash
   cd local
   python test_webhook.py [opened|reopened|synchronize]
   ```
