"""
Kavun Prompt Generation Lambda - AWS version without pydantic
"""

import json
import os
import boto3
import requests
from botocore.exceptions import ClientError
from datetime import datetime, timezone

def get_secret():
    """Get API keys from Secrets Manager"""
    secret_name = os.environ['SECRET_ARN']
    region_name = os.environ.get('AWS_REGION', 'us-east-1')
    
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager',
        region_name=region_name
    )
    
    try:
        get_secret_value_response = client.get_secret_value(
            SecretId=secret_name
        )
    except ClientError as e:
        print(f"Error getting secret: {str(e)}")
        raise e
    
    secret = json.loads(get_secret_value_response['SecretString'])
    return secret

def generate_prompt_with_requests(word, language='en'):
    """Generate image prompt using OpenAI API via requests"""
    try:
        # Get API keys from Secrets Manager
        api_keys = get_secret()
        openai_key = api_keys['openai_key']
        
        headers = {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json"
        }
        
        # System prompt
        system_prompt = """You are an expert at creating detailed, vivid image prompts for Anki flashcards. 
        Your prompts should be:
        - Clear and descriptive
        - Visually specific
        - Educational and memorable
        - Suitable for language learning
        - Focused on the main concept
        
        Create a single, detailed prompt that would generate a perfect image for an Anki card."""
        
        # User prompt
        user_prompt = f"""
        Generate a short, vivid English scene description for an image that illustrates the meaning of the given word '{word}' without including the word itself or any text in the IMAGE (but you can use the word in prompt).
        The scene must clearly and intuitively convey the concept through context, emotion, or visual contrast.
        The description should:
            •	Be one or two sentences long.
            •	Be concrete and easy to visualize.
            •	Avoid any written words, letters, or symbols in the scene.
            •	Use human or natural settings where possible (for emotional or conceptual clarity).
            •	Express the essence of the word in a way suitable for Anki flashcards — the learner should guess the word based only on the image.
            •	Output only the final English scene description — no explanations, no formatting, no repetition of the word.
        """
        
        data = {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 200,
            "temperature": 0.7
        }
        
        # Call OpenAI API
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        
        if response.status_code != 200:
            raise Exception(f"OpenAI API error: {response.status_code} - {response.text}")
        
        result = response.json()
        prompt = result['choices'][0]['message']['content'].strip()
        
        return {
            "word": word,
            "language": language,
            "prompt": prompt,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "prompt_generated",
            "tokens_used": result.get('usage', {}).get('total_tokens', 0)
        }
        
    except Exception as e:
        print(f"Error generating prompt: {str(e)}")
        raise e

def check_word_exists(word):
    """Check if word already exists in DynamoDB"""
    try:
        table_name = os.environ['TABLE_NAME']
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(table_name)
        
        response = table.get_item(Key={'word': word})
        
        if 'Item' in response:
            return response['Item']
        else:
            return None
            
    except Exception as e:
        print(f"Error checking word existence: {str(e)}")
        return None

def update_dynamodb_status(word, status, prompt=None, error_message=None):
    """Update DynamoDB with word status"""
    try:
        table_name = os.environ['TABLE_NAME']
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(table_name)
        
        update_expression = "SET #status = :status, updated_at = :updated_at"
        expression_attribute_names = {"#status": "status"}
        expression_attribute_values = {
            ":status": status,
            ":updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        if prompt:
            update_expression += ", prompt = :prompt"
            expression_attribute_values[":prompt"] = prompt
        
        if error_message:
            update_expression += ", error_msg = :error_msg"
            expression_attribute_values[":error_msg"] = str(error_message)
        
        table.update_item(
            Key={'word': word},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values
        )
        
        print(f"Updated DynamoDB: {word} -> {status}")
        
    except Exception as e:
        print(f"Error updating DynamoDB: {str(e)}")
        raise e

def handler(event, context):
    """Lambda handler for prompt generation"""
    print(f"Prompt Lambda invoked with event: {json.dumps(event)}")
    
    try:
        # Get parameters from event
        word = event.get('word')
        language = event.get('language', 'en')
        
        if not word:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Word is required"})
            }
        
        # Check if word already exists
        existing_word = check_word_exists(word)
        if existing_word and existing_word.get('status') == 'completed':
            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*"
                },
                "body": json.dumps({
                    "message": "Word already exists",
                    "word": word,
                    "status": "already_exists",
                    "image_url": existing_word.get('image_url'),
                    "created_at": existing_word.get('created_at')
                })
            }
        
        # Update status in DynamoDB
        update_dynamodb_status(word, "generating_prompt")
        
        # Generate prompt
        print(f"🎨 Generating prompt for word: '{word}' in {language}")
        prompt_data = generate_prompt_with_requests(word, language)
        
        # Update status in DynamoDB
        update_dynamodb_status(word, "prompt_generated", prompt=prompt_data['prompt'])
        
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(prompt_data, default=str)
        }
        
    except Exception as e:
        print(f"Error in handler: {str(e)}")
        
        # Update error status in DynamoDB
        if 'word' in locals():
            update_dynamodb_status(word, "failed", error_message=str(e))
        
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({
                "error": str(e),
                "message": "Internal server error"
            })
        }

if __name__ == "__main__":
    # Local testing
    test_event = {
        "word": "sun",
        "language": "en"
    }
    
    class MockContext:
        def __init__(self):
            self.function_name = "test-function"
            self.function_version = "1"
            self.invoked_function_arn = "arn:aws:lambda:test"
            self.memory_limit_in_mb = "512"
            self.aws_request_id = "test-request-id"
    
    result = handler(test_event, MockContext())
    print(json.dumps(result, indent=2, ensure_ascii=False))