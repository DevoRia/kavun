"""
Kavun Status Check Lambda
Checks word status and provides status information
"""

import json
import os
import boto3
from botocore.exceptions import ClientError
from datetime import datetime

def get_word_from_dynamodb(word):
    """Get word information from DynamoDB"""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['TABLE_NAME'])
    
    try:
        response = table.get_item(Key={'word': word})
        return response.get('Item')
    except Exception as e:
        print(f"Error getting word from DynamoDB: {str(e)}")
        raise e

def get_all_words(status=None, limit=100):
    """Get all words with optional status filter"""
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(os.environ['TABLE_NAME'])
    
    try:
        if status:
            # Query by status using GSI
            response = table.query(
                IndexName='StatusIndex',
                KeyConditionExpression='#status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={':status': status},
                Limit=limit,
                ScanIndexForward=False  # Sort by created_at descending
            )
        else:
            # Scan all items
            response = table.scan(Limit=limit)
        
        return response.get('Items', [])
    except Exception as e:
        print(f"Error getting words from DynamoDB: {str(e)}")
        raise e

def check_word_exists(word):
    """Check if word exists and return its status"""
    try:
        item = get_word_from_dynamodb(word)
        
        if not item:
            return {
                "exists": False,
                "word": word,
                "status": None
            }
        
        return {
            "exists": True,
            "word": word,
            "status": item.get('status'),
            "image_url": item.get('image_url'),
            "created_at": item.get('created_at'),
            "updated_at": item.get('updated_at')
        }
        
    except Exception as e:
        print(f"Error checking word existence: {str(e)}")
        raise e

def get_word_status(word):
    """Get detailed status for a specific word"""
    try:
        item = get_word_from_dynamodb(word)
        
        if not item:
            return {
                "word": word,
                "status": "not_found",
                "message": "Word not found in database"
            }
        
        status = item.get('status', 'unknown')
        
        # Determine status message
        status_messages = {
            'pending': 'Word is waiting to be processed',
            'processing_prompt': 'Generating prompt using OpenAI',
            'processing_image': 'Generating image using Nano Banana',
            'generating_image': 'Creating image and uploading to S3',
            'completed': 'Image generation completed successfully',
            'failed': 'Image generation failed',
            'unknown': 'Status unknown'
        }
        
        result = {
            "word": word,
            "status": status,
            "message": status_messages.get(status, 'Unknown status'),
            "created_at": item.get('created_at'),
            "updated_at": item.get('updated_at')
        }
        
        # Add additional fields based on status
        if status == 'completed' and item.get('image_url'):
            result['image_url'] = item['image_url']
            result['s3_key'] = item.get('s3_key')
        
        if status == 'failed' and item.get('error'):
            result['error'] = item['error']
        
        if item.get('prompt'):
            result['prompt'] = item['prompt']
        
        return result
        
    except Exception as e:
        print(f"Error getting word status: {str(e)}")
        return {
            "word": word,
            "status": "error",
            "message": f"Error retrieving status: {str(e)}"
        }

def list_images(status=None, limit=100):
    """List all images with optional status filter"""
    try:
        words = get_all_words(status=status, limit=limit)
        
        # Format response
        images = []
        for word in words:
            image_info = {
                "word": word['word'],
                "status": word.get('status'),
                "created_at": word.get('created_at'),
                "updated_at": word.get('updated_at')
            }
            
            if word.get('image_url'):
                image_info['image_url'] = word['image_url']
            
            if word.get('prompt'):
                image_info['prompt'] = word['prompt']
            
            images.append(image_info)
        
        return {
            "images": images,
            "count": len(images),
            "status_filter": status,
            "limit": limit
        }
        
    except Exception as e:
        print(f"Error listing images: {str(e)}")
        raise e

def handler(event, context):
    """Lambda handler for status operations"""
    print(f"Status Lambda invoked with event: {json.dumps(event)}")
    
    try:
        # Determine operation based on event
        http_method = event.get('httpMethod', 'GET')
        path_parameters = event.get('pathParameters') or {}
        query_parameters = event.get('queryStringParameters') or {}
        
        # Determine endpoint
        if 'word' in path_parameters:
            word = path_parameters['word']
            
            if 'check' in event.get('path', ''):
                # GET /check/{word}
                result = check_word_exists(word)
            else:
                # GET /status/{word}
                result = get_word_status(word)
        
        elif 'images' in event.get('path', ''):
            # GET /images
            status_filter = query_parameters.get('status')
            limit = int(query_parameters.get('limit', 100))
            result = list_images(status=status_filter, limit=limit)
        
        else:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Invalid endpoint"})
            }
        
        return {
            "statusCode": 200,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps(result, default=str)
        }
        
    except Exception as e:
        error_msg = f"Error in status handler: {str(e)}"
        print(error_msg)
        
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*"
            },
            "body": json.dumps({"error": error_msg})
        }
