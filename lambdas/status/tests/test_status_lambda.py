"""
Unit tests for Status Check Lambda - FULLY MOCKED
"""

import pytest
import json
import os
from unittest.mock import patch, MagicMock
import sys

# Add the parent directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Import from the correct handler module
import handler as status_handler

class TestStatusLambda:
    """Unit tests for Status Lambda - NO AWS CONNECTION"""
    
    @patch('handler.boto3.resource')
    def test_get_word_from_dynamodb_success(self, mock_boto3):
        """Test successful word retrieval from DynamoDB - MOCKED"""
        os.environ['TABLE_NAME'] = 'kavun-words'
        
        # Mock DynamoDB
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            'Item': {
                'word': 'apple',
                'status': 'completed',
                'image_url': 'https://test.com/apple.png',
                'created_at': '2023-01-01T00:00:00Z'
            }
        }
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_boto3.return_value = mock_dynamodb
        
        result = status_handler.get_word_from_dynamodb('apple')
        
        assert result['word'] == 'apple'
        assert result['status'] == 'completed'
        assert 'image_url' in result
    
    @patch('handler.boto3.resource')
    def test_get_word_from_dynamodb_not_found(self, mock_boto3):
        """Test word not found in DynamoDB - MOCKED"""
        os.environ['TABLE_NAME'] = 'kavun-words'
        
        # Mock DynamoDB
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        mock_dynamodb = MagicMock()
        mock_dynamodb.Table.return_value = mock_table
        mock_boto3.return_value = mock_dynamodb
        
        result = status_handler.get_word_from_dynamodb('nonexistent')
        
        assert result is None
    
    @patch('handler.get_word_from_dynamodb')
    def test_check_word_exists_found(self, mock_get_word):
        """Test checking existing word - MOCKED"""
        mock_get_word.return_value = {
            'word': 'apple',
            'status': 'completed',
            'image_url': 'https://test.com/apple.png'
        }
        
        result = status_handler.check_word_exists('apple')
        
        assert result['exists'] == True
        assert result['word'] == 'apple'
        assert result['status'] == 'completed'
    
    @patch('handler.get_word_from_dynamodb')
    def test_check_word_exists_not_found(self, mock_get_word):
        """Test checking non-existing word - MOCKED"""
        mock_get_word.return_value = None
        
        result = status_handler.check_word_exists('nonexistent')
        
        assert result['exists'] == False
        assert result['word'] == 'nonexistent'
        assert result['status'] is None
    
    @patch('handler.get_word_from_dynamodb')
    def test_get_word_status_found(self, mock_get_word):
        """Test getting status for existing word - MOCKED"""
        mock_get_word.return_value = {
            'word': 'apple',
            'status': 'completed',
            'image_url': 'https://test.com/apple.png',
            'created_at': '2023-01-01T00:00:00Z',
            'updated_at': '2023-01-01T00:00:00Z'
        }
        
        result = status_handler.get_word_status('apple')
        
        assert result['word'] == 'apple'
        assert result['status'] == 'completed'
        assert 'image_url' in result
    
    @patch('handler.get_word_from_dynamodb')
    def test_get_word_status_not_found(self, mock_get_word):
        """Test getting status for non-existing word - MOCKED"""
        mock_get_word.return_value = None
        
        result = status_handler.get_word_status('nonexistent')
        
        assert result['word'] == 'nonexistent'
        assert result['status'] == 'not_found'
        assert 'message' in result
    
    def test_handler_missing_word(self):
        """Test handling missing word parameter"""
        event = {}
        context = MagicMock()
        
        result = status_handler.handler(event, context)
        
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
    
    def test_handler_empty_word(self):
        """Test handling empty word parameter"""
        event = {'word': ''}
        context = MagicMock()
        
        result = status_handler.handler(event, context)
        
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body
    
    def test_handler_none_word(self):
        """Test handling None word parameter"""
        event = {'word': None}
        context = MagicMock()
        
        result = status_handler.handler(event, context)
        
        assert result['statusCode'] == 400
        body = json.loads(result['body'])
        assert 'error' in body