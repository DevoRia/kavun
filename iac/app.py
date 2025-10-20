#!/usr/bin/env python3
"""
Kavun Generator - CDK Main Application
Generates images for Anki cards using OpenAI + Nano Banana APIs
"""

import os
from aws_cdk import App, Environment
from kavun_stack import KavunStack

# CDK App
app = App()

# Environment configuration
env = Environment(
    account=os.environ.get('CDK_DEFAULT_ACCOUNT'),
    region=os.environ.get('CDK_DEFAULT_REGION', 'us-east-1')
)

# Stack configuration
stack_config = {
    'description': 'Kavun - Anki Image Generator',
    'tags': {
        'Project': 'Kavun',
        'Environment': 'Production',
        'Owner': 'Kavun Team'
    }
}

# Create Kavun Stack
KavunStack(
    app, 
    "kavun",
    env=env,
    **stack_config
)

# Synthesize CloudFormation
app.synth()
