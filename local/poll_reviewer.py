import os
import sys
import time
import json
import boto3
from dotenv import load_dotenv

reviewer_env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../reviewer/.env"))
load_dotenv(reviewer_env_path)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../reviewer")))
from receiver.handler import lambda_handler

SQS_QUEUE_NAME = os.environ.get('SQS_QUEUE_NAME')
AWS_REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')

def poll_messages():
    if not SQS_QUEUE_NAME:
        print("Error: SQS_QUEUE_NAME is not set in reviewer/.env")
        return

    print(f"Initialising SQS client for queue: '{SQS_QUEUE_NAME}' in region: '{AWS_REGION}'...")
    try:
        sqs = boto3.resource('sqs', region_name=AWS_REGION)
        queue = sqs.get_queue_by_name(QueueName=SQS_QUEUE_NAME)
    except Exception as e:
        print(f"Failed to connect to SQS Queue: {e}")
        return

    print(f"Started polling SQS queue: {queue.url}")

    while True:
        try:
            messages = queue.receive_messages(
                MaxNumberOfMessages=1,
                WaitTimeSeconds=10,
                VisibilityTimeout=30
            )

            for message in messages:
                print(f"\nReceived message ID: {message.message_id}")
                
                event = {
                    "records": [
                        {
                            "body": message.body,
                            "messageId": message.message_id,
                            "receiptHandle": message.receipt_handle
                        }
                    ]
                }
                
                print("🧠 Invoking Reviewer lambda handler locally...")
                try:
                    lambda_handler(event, None)
                    print("Successfully processed message. Deleting from queue")
                    message.delete()
                except Exception as ex:
                    print(f"Error during message processing: {ex}")
                    print("Message remains in the queue for retry.")
                    
        except KeyboardInterrupt:
            print("\nStopping poll script.")
            break
        except Exception as e:
            print(f"Error while receiving messages: {e}")
            time.sleep(5)

if __name__ == "__main__":
    poll_messages()
