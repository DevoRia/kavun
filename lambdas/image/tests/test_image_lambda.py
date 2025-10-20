"""
Unit tests for Image Lambda handler
"""
import json
import base64
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime


# Mock environment variables
@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables for all tests"""
    monkeypatch.setenv('TABLE_NAME', 'test-table')
    monkeypatch.setenv('SECRET_ARN', 'arn:aws:secretsmanager:us-east-1:123456789012:secret:test')
    monkeypatch.setenv('BUCKET_NAME', 'test-bucket')
    monkeypatch.setenv('CLOUDFRONT_URL', 'https://test.cloudfront.net')
    monkeypatch.setenv('AWS_REGION', 'us-east-1')


@pytest.fixture
def mock_context():
    """Mock Lambda context"""
    context = Mock()
    context.function_name = "test-function"
    context.function_version = "1"
    context.invoked_function_arn = "arn:aws:lambda:test"
    context.memory_limit_in_mb = "1024"
    context.aws_request_id = "test-request-id"
    return context


@pytest.fixture
def mock_secrets_manager():
    """Mock Secrets Manager"""
    with patch('handler.boto3.session.Session') as mock_session:
        mock_client = MagicMock()
        mock_session.return_value.client.return_value = mock_client
        
        # Mock get_secret_value response
        mock_client.get_secret_value.return_value = {
            'SecretString': json.dumps({
                'openai_key': 'test-openai-key',
                'nano_banana_key': 'test-gemini-key'
            })
        }
        yield mock_client


@pytest.fixture
def mock_dynamodb():
    """Mock DynamoDB resource"""
    with patch('handler.boto3.resource') as mock_resource:
        mock_table = MagicMock()
        mock_resource.return_value.Table.return_value = mock_table
        yield mock_table


@pytest.fixture
def mock_s3():
    """Mock S3 client"""
    with patch('handler.boto3.client') as mock_client:
        mock_s3_client = MagicMock()
        mock_client.return_value = mock_s3_client
        yield mock_s3_client


@pytest.fixture
def mock_requests():
    """Mock requests library for Gemini API"""
    with patch('handler.requests.post') as mock_post:
        # Create a valid base64 encoded image (1x1 transparent PNG)
        test_image_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'candidates': [{
                'content': {
                    'parts': [{
                        'inlineData': {
                            'data': test_image_data
                        }
                    }]
                }
            }]
        }
        mock_post.return_value = mock_response
        yield mock_post


def test_successful_image_generation_from_step_functions(mock_context, mock_secrets_manager, mock_dynamodb, mock_s3, mock_requests):
    """Test successful image generation from Step Functions event"""
    from handler import handler
    
    event = {
        'word': 'sun',
        'prompt_result': {
            'prompt': 'A bright yellow sun shining in a clear blue sky'
        }
    }
    
    response = handler(event, mock_context)
    
    assert response['word'] == 'sun'
    assert response['status'] == 'completed'
    assert 'image_url' in response
    assert 'https://test.cloudfront.net/images/sun.png' == response['image_url']
    
    # Verify Gemini API was called
    mock_requests.assert_called_once()
    
    # Verify S3 upload was called
    mock_s3.put_object.assert_called_once()
    
    # Verify DynamoDB was updated
    assert mock_dynamodb.update_item.call_count >= 2  # generating_image and completed


def test_successful_image_generation_direct_invocation(mock_context, mock_secrets_manager, mock_dynamodb, mock_s3, mock_requests):
    """Test successful image generation from direct invocation"""
    from handler import handler
    
    event = {
        'word': 'apple',
        'prompt': 'A red apple on a wooden table'
    }
    
    response = handler(event, mock_context)
    
    assert response['word'] == 'apple'
    assert response['status'] == 'completed'
    assert 'image_url' in response


def test_missing_word_parameter(mock_context):
    """Test handler with missing word parameter"""
    from handler import handler
    
    event = {
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 400
    body = json.loads(response['body'])
    assert 'error' in body


def test_missing_prompt_parameter(mock_context):
    """Test handler with missing prompt parameter"""
    from handler import handler
    
    event = {
        'word': 'sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 400
    body = json.loads(response['body'])
    assert 'error' in body


def test_gemini_api_error(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test Gemini API error handling"""
    from handler import handler
    
    # Mock requests to raise exception
    mock_requests.side_effect = Exception("Gemini API error")
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500
    body = json.loads(response['body'])
    assert 'error' in body
    
    # Verify DynamoDB was updated with failed status
    mock_dynamodb.update_item.assert_called()


def test_gemini_api_non_200_response(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test Gemini API non-200 response"""
    from handler import handler
    
    # Mock requests to return error status
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.raise_for_status.side_effect = Exception("Rate limit exceeded")
    mock_requests.return_value = mock_response
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500


def test_s3_upload_error(mock_context, mock_secrets_manager, mock_dynamodb, mock_s3, mock_requests):
    """Test S3 upload error handling"""
    from handler import handler
    
    # Mock S3 to raise exception
    mock_s3.put_object.side_effect = Exception("S3 error")
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500
    body = json.loads(response['body'])
    assert 'error' in body


def test_secrets_manager_error(mock_context, mock_secrets_manager, mock_dynamodb):
    """Test Secrets Manager error handling"""
    from handler import handler
    
    # Mock secrets manager to raise exception
    mock_secrets_manager.get_secret_value.side_effect = Exception("Secrets Manager error")
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500


def test_gemini_api_invalid_response_structure(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test Gemini API with invalid response structure"""
    from handler import handler
    
    # Mock requests to return invalid structure
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        'candidates': []  # Empty candidates
    }
    mock_requests.return_value = mock_response
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500


def test_dynamodb_update_error(mock_context, mock_secrets_manager, mock_dynamodb, mock_s3, mock_requests):
    """Test DynamoDB update error handling"""
    from handler import handler
    
    # Mock DynamoDB to raise exception on first update only
    # (so error status update can succeed)
    mock_dynamodb.update_item.side_effect = [Exception("DynamoDB error"), None]
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500


def test_image_optimization(mock_context, mock_secrets_manager, mock_dynamodb, mock_s3, mock_requests):
    """Test image optimization"""
    from handler import optimize_image
    
    # Create a test image (already small)
    test_image_data = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    
    optimized = optimize_image(test_image_data, max_size=(500, 500), max_file_size_kb=400)
    
    # Should return the same image as it's already small
    assert optimized == test_image_data


def test_cloudfront_url_in_response(mock_context, mock_secrets_manager, mock_dynamodb, mock_s3, mock_requests):
    """Test that CloudFront URL is properly returned"""
    from handler import handler
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert 'image_url' in response
    assert response['image_url'].startswith('https://test.cloudfront.net/')


def test_s3_metadata(mock_context, mock_secrets_manager, mock_dynamodb, mock_s3, mock_requests):
    """Test that S3 object has proper metadata"""
    from handler import handler
    
    event = {
        'word': 'sun',
        'prompt': 'A bright sun'
    }
    
    handler(event, mock_context)
    
    # Check S3 put_object was called with metadata
    call_args = mock_s3.put_object.call_args
    assert 'Metadata' in call_args.kwargs
    metadata = call_args.kwargs['Metadata']
    assert metadata['word'] == 'sun'
    assert 'generated_at' in metadata


def test_empty_word(mock_context):
    """Test handler with empty word"""
    from handler import handler
    
    event = {
        'word': '',
        'prompt': 'A bright sun'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 400


def test_empty_prompt(mock_context):
    """Test handler with empty prompt"""
    from handler import handler
    
    event = {
        'word': 'sun',
        'prompt': ''
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 400

