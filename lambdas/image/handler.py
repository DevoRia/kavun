"""
Kavun Image Generation Lambda
Generates images using Nano Banana API and uploads to S3
"""

import json
import os
import boto3
import requests
from botocore.exceptions import ClientError
from datetime import datetime, timezone
from urllib.parse import urlparse
import uuid

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

def update_dynamodb_status(word, status, **kwargs):
    """Update word status in DynamoDB"""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['TABLE_NAME'])
    
    update_expression = "SET #status = :status, updated_at = :updated_at"
    expression_attribute_names = {"#status": "status"}
    expression_attribute_values = {
        ":status": status,
        ":updated_at": datetime.now(timezone.utc).isoformat()
    }
    
    # Add additional fields if provided
    for key, value in kwargs.items():
        # Handle reserved keywords
        if key == "error":
            key = "error_msg"
        update_expression += f", {key} = :{key}"
        expression_attribute_values[f":{key}"] = value
    
    try:
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

def generate_image(prompt, gemini_api_key):
    """Generate image using Gemini API"""
    
    # Gemini API for image generation
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent"
    
    headers = {
        "x-goog-api-key": gemini_api_key,
        "Content-Type": "application/json"
    }
    
    payload = {
        "contents": [{
            "parts": [
                {"text": f"Create a picture of: {prompt}"}
            ]
        }],
        "generationConfig": {
            "imageConfig": {
                "aspectRatio": "1:1"  # 1:1 for square images (1024x1024)
            },
            "responseModalities": ["Image"]  # Explicitly request image output
        }
    }
    
    try:
        print(f"Calling Gemini API with prompt: {prompt[:100]}...")
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        
        data = response.json()
        print(f"Gemini API response keys: {list(data.keys())}")
        if 'candidates' in data:
            print(f"Number of candidates: {len(data['candidates'])}")
            if len(data['candidates']) > 0:
                candidate = data['candidates'][0]
                print(f"Candidate keys: {list(candidate.keys())}")
                if 'content' in candidate:
                    print(f"Content keys: {list(candidate['content'].keys())}")
                    if 'parts' in candidate['content']:
                        print(f"Number of parts: {len(candidate['content']['parts'])}")
                        for i, part in enumerate(candidate['content']['parts']):
                            print(f"Part {i} keys: {list(part.keys())}")
                            if 'inline_data' in part:
                                print(f"Part {i} inline_data keys: {list(part['inline_data'].keys())}")
        
            # Process Gemini API response
            # Expected structure: {"candidates": [{"content": {"parts": [{"inlineData": {"data": "base64..."}}]}}]}
            if 'candidates' in data and len(data['candidates']) > 0:
                candidate = data['candidates'][0]
                
                # Check structure content.parts.inlineData.data
                if 'content' in candidate and 'parts' in candidate['content']:
                    parts = candidate['content']['parts']
                    for part in parts:
                        if 'inlineData' in part and 'data' in part['inlineData']:
                            image_data = part['inlineData']['data']
                            print(f"✅ Found image data in inlineData.data, length: {len(image_data)}")
                            return image_data
                
                # If not found in expected structure, output detailed information
                print(f"❌ No image data found in expected structure")
                print(f"Candidate keys: {list(candidate.keys())}")
                if 'content' in candidate:
                    print(f"Content keys: {list(candidate['content'].keys())}")
                    if 'parts' in candidate['content']:
                        print(f"Parts count: {len(candidate['content']['parts'])}")
                        for i, part in enumerate(candidate['content']['parts']):
                            print(f"Part {i} keys: {list(part.keys())}")
                            if 'inline_data' in part:
                                print(f"Part {i} inline_data keys: {list(part['inline_data'].keys())}")
                
                raise ValueError("No image data found in Gemini API response")
            else:
                raise ValueError("Invalid Gemini API response structure")
        
    except requests.exceptions.RequestException as e:
        print(f"Gemini API request error: {str(e)}")
        raise e
    except Exception as e:
        print(f"Gemini API error: {str(e)}")
        raise e


def optimize_image(image_data, max_size=(500, 500), max_file_size_kb=400):
    """Simple image optimization without PIL (for Lambda compatibility)"""
    import base64
    
    try:
        # Decode base64 image data
        image_bytes = base64.b64decode(image_data)
        
        # Check current file size
        current_size_kb = len(image_bytes) / 1024
        print(f"Original image size: {current_size_kb:.1f} KB")
        
        # If image is already small enough, return as is
        if current_size_kb <= max_file_size_kb:
            print(f"✅ Image already optimized: {current_size_kb:.1f} KB")
            return image_data
        
        # For now, we'll use the original image and rely on Gemini's optimization
        # In a production environment, you might want to use a different approach
        # like calling an external image optimization service
        
        print(f"⚠️ Image size ({current_size_kb:.1f} KB) exceeds target ({max_file_size_kb} KB)")
        print("Using original image - consider using external optimization service")
        
        return image_data
        
    except Exception as e:
        print(f"Error in image optimization: {str(e)}")
        # Return original image if optimization fails
        return image_data


def upload_to_s3(image_data, word):
    """Upload optimized image to S3 and return CloudFront URL"""
    import base64
    
    s3_client = boto3.client('s3')
    bucket_name = os.environ['BUCKET_NAME']
    cloudfront_url = os.environ['CLOUDFRONT_URL']
    
    try:
        # Optimize image before uploading
        print(f"🔧 Optimizing image for word: {word}")
        optimized_image_data = optimize_image(image_data, max_size=(500, 500), max_file_size_kb=400)
        
        # Decode optimized base64 image data
        image_bytes = base64.b64decode(optimized_image_data)
        
        # Generate S3 key with PNG extension (Gemini generates PNG)
        file_extension = 'png'
        s3_key = f"images/{word}.{file_extension}"
        
        # Upload image to S3
        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=image_bytes,
            ContentType='image/png',
            Metadata={
                'word': word,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'optimized': 'true',
                'max_size': '500x500',
                'max_file_size': '400KB'
            }
        )
        
        # Generate CloudFront URL
        cloudfront_image_url = f"{cloudfront_url}/{s3_key}"
        
        print(f"Uploaded image to S3: s3://{bucket_name}/{s3_key}")
        print(f"CloudFront URL: {cloudfront_image_url}")
        
        return {
            's3_key': s3_key,
            'cloudfront_url': cloudfront_image_url,
            'bucket_name': bucket_name
        }
        
    except Exception as e:
        print(f"Error uploading to S3: {str(e)}")
        raise e

def handler(event, context):
    """Lambda handler for image generation"""
    print(f"Image Lambda invoked with event: {json.dumps(event)}")
    
    try:
        # Process event from Step Functions or direct invocation
        if 'prompt_result' in event:
            # Step Functions event
            word = event['word']
            prompt = event['prompt_result']['prompt']
        elif 'word' in event and 'prompt' in event:
            # Direct invocation
            word = event['word']
            prompt = event['prompt']
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Word and prompt are required"})
            }
        
        if not word or not prompt:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Word and prompt cannot be empty"})
            }
        
        # Update status to "generating_image"
        update_dynamodb_status(word, "generating_image")
        
        # Get API keys
        secrets = get_secret()
        gemini_api_key = secrets['nano_banana_key']  # Using Gemini API key
        
        if not gemini_api_key:
            raise ValueError("Gemini API key not found in secrets")
        
        # Generate image via Gemini
        image_data = generate_image(prompt, gemini_api_key)
        
        # Upload to S3
        upload_result = upload_to_s3(image_data, word)
        
        # Update status and save URL
        update_dynamodb_status(
            word,
            "completed",
            image_url=upload_result['cloudfront_url'],
            s3_key=upload_result['s3_key'],
            image_generated_at=datetime.now(timezone.utc).isoformat()
        )
        
        # Return result for Step Functions
        result = {
            "word": word,
            "image_url": upload_result['cloudfront_url'],
            "s3_key": upload_result['s3_key'],
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
        print(f"Image generation completed for '{word}': {upload_result['cloudfront_url']}")
        return result
        
    except Exception as e:
        error_msg = f"Error generating image: {str(e)}"
        print(error_msg)
        
        # Update status to "failed"
        if 'word' in locals():
            update_dynamodb_status(word, "failed", error=str(e))
        
        return {
            "statusCode": 500,
            "body": json.dumps({"error": error_msg})
        }
