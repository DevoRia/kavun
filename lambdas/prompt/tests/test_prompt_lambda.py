"""
Unit tests for Prompt Lambda handler
"""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime


# Mock environment variables
@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Mock environment variables for all tests"""
    monkeypatch.setenv('TABLE_NAME', 'test-table')
    monkeypatch.setenv('SECRET_ARN', 'arn:aws:secretsmanager:us-east-1:123456789012:secret:test')
    monkeypatch.setenv('CLOUDFRONT_URL', 'https://test.cloudfront.net')
    monkeypatch.setenv('AWS_REGION', 'us-east-1')


@pytest.fixture
def mock_context():
    """Mock Lambda context"""
    context = Mock()
    context.function_name = "test-function"
    context.function_version = "1"
    context.invoked_function_arn = "arn:aws:lambda:test"
    context.memory_limit_in_mb = "512"
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
def mock_requests():
    """Mock requests library"""
    with patch('handler.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{
                'message': {
                    'content': 'A bright yellow sun shining in a clear blue sky with rays extending outward.'
                }
            }],
            'usage': {
                'total_tokens': 50
            }
        }
        mock_post.return_value = mock_response
        yield mock_post


def test_successful_prompt_generation(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test successful prompt generation"""
    from handler import handler
    
    # Mock DynamoDB get_item to return no existing word
    mock_dynamodb.get_item.return_value = {}
    
    event = {
        'word': 'sun',
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['word'] == 'sun'
    assert body['status'] == 'prompt_generated'
    assert 'prompt' in body
    assert 'tokens_used' in body
    
    # Verify OpenAI API was called
    mock_requests.assert_called_once()
    call_args = mock_requests.call_args
    assert call_args[0][0] == 'https://api.openai.com/v1/chat/completions'
    
    # Verify DynamoDB was updated twice (generating_prompt and prompt_generated)
    assert mock_dynamodb.update_item.call_count == 2


def test_word_already_exists(mock_context, mock_secrets_manager, mock_dynamodb):
    """Test when word already exists with completed status"""
    from handler import handler
    
    # Mock DynamoDB get_item to return existing completed word
    mock_dynamodb.get_item.return_value = {
        'Item': {
            'word': 'sun',
            'status': 'completed',
            'image_url': 'https://test.cloudfront.net/images/sun.png',
            'created_at': '2024-01-01T00:00:00'
        }
    }
    
    event = {
        'word': 'sun',
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['status'] == 'already_exists'
    assert body['word'] == 'sun'
    assert 'image_url' in body


def test_missing_word_parameter(mock_context):
    """Test handler with missing word parameter"""
    from handler import handler
    
    event = {
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 400
    body = json.loads(response['body'])
    assert 'error' in body


def test_openai_api_error(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test OpenAI API error handling"""
    from handler import handler
    
    # Mock DynamoDB get_item to return no existing word
    mock_dynamodb.get_item.return_value = {}
    
    # Mock requests to raise exception
    mock_requests.side_effect = Exception("OpenAI API error")
    
    event = {
        'word': 'sun',
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500
    body = json.loads(response['body'])
    assert 'error' in body
    
    # Verify DynamoDB was updated with error status
    update_calls = [call for call in mock_dynamodb.update_item.call_args_list]
    assert len(update_calls) >= 1  # At least one update (failed status)


def test_secrets_manager_error(mock_context, mock_secrets_manager, mock_dynamodb):
    """Test Secrets Manager error handling"""
    from handler import handler
    
    # Mock DynamoDB get_item to return no existing word
    mock_dynamodb.get_item.return_value = {}
    
    # Mock secrets manager to raise exception
    mock_secrets_manager.get_secret_value.side_effect = Exception("Secrets Manager error")
    
    event = {
        'word': 'sun',
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500
    body = json.loads(response['body'])
    assert 'error' in body


def test_different_language(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test prompt generation for different language"""
    from handler import handler
    
    # Mock DynamoDB get_item to return no existing word
    mock_dynamodb.get_item.return_value = {}
    
    event = {
        'word': 'sonne',
        'language': 'de'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['word'] == 'sonne'
    assert body['language'] == 'de'


def test_dynamodb_update_error(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test DynamoDB update error handling"""
    from handler import handler
    
    # Mock DynamoDB get_item to return no existing word
    mock_dynamodb.get_item.return_value = {}
    
    # Mock DynamoDB update_item to raise exception on first call only
    # (so error status update can succeed)
    mock_dynamodb.update_item.side_effect = [Exception("DynamoDB error"), None]
    
    event = {
        'word': 'sun',
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500
    body = json.loads(response['body'])
    assert 'error' in body


def test_openai_non_200_response(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test OpenAI API non-200 response"""
    from handler import handler
    
    # Mock DynamoDB get_item to return no existing word
    mock_dynamodb.get_item.return_value = {}
    
    # Mock requests to return error status
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.text = "Rate limit exceeded"
    mock_requests.return_value = mock_response
    
    event = {
        'word': 'sun',
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert response['statusCode'] == 500
    body = json.loads(response['body'])
    assert 'error' in body


def test_cors_headers(mock_context, mock_secrets_manager, mock_dynamodb, mock_requests):
    """Test that CORS headers are present in response"""
    from handler import handler
    
    # Mock DynamoDB get_item to return no existing word
    mock_dynamodb.get_item.return_value = {}
    
    event = {
        'word': 'sun',
        'language': 'en'
    }
    
    response = handler(event, mock_context)
    
    assert 'headers' in response
    assert 'Access-Control-Allow-Origin' in response['headers']
    assert response['headers']['Access-Control-Allow-Origin'] == '*'

