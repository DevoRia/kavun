"""
Snapshot tests for Kavun CDK Stack
Tests that the synthesized CloudFormation template matches the expected snapshot
"""
import json
import os
from pathlib import Path
import pytest
from aws_cdk import App
from kavun_stack import KavunStack


def get_snapshot_path():
    """Get path to snapshot file"""
    return Path(__file__).parent / "snapshots" / "kavun_stack_snapshot.json"


def synthesize_stack():
    """Synthesize the stack and return the CloudFormation template"""
    app = App()
    
    # Create stack with test configuration
    stack = KavunStack(
        app,
        "kavun-test",
        description="Kavun - Anki Image Generator (Test)",
        env={
            "account": "123456789012",  # Dummy account for testing
            "region": "us-east-1"
        }
    )
    
    # Synthesize the stack
    cloud_assembly = app.synth()
    
    # Get the template
    stack_artifact = cloud_assembly.get_stack_by_name("kavun-test")
    template = stack_artifact.template
    
    return template


def normalize_template(template):
    """
    Normalize template by removing dynamic values that change between runs
    but don't affect the actual infrastructure
    """
    # Remove CDK metadata that changes between runs
    if "CDKMetadata" in template.get("Resources", {}):
        del template["Resources"]["CDKMetadata"]
    
    # Remove bootstrap version parameter (changes with CDK updates)
    if "Parameters" in template and "BootstrapVersion" in template["Parameters"]:
        del template["Parameters"]["BootstrapVersion"]
    
    # Sort keys for consistent comparison
    def sort_dict(obj):
        if isinstance(obj, dict):
            return {k: sort_dict(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, list):
            return [sort_dict(item) for item in obj]
        return obj
    
    return sort_dict(template)


def test_stack_snapshot():
    """
    Test that the synthesized CloudFormation template matches the snapshot.
    
    If the snapshot doesn't exist, it will be created.
    If the template differs from the snapshot, the test will fail.
    """
    # Synthesize the stack
    template = synthesize_stack()
    
    # Normalize the template
    normalized_template = normalize_template(template)
    
    # Get snapshot path
    snapshot_path = get_snapshot_path()
    
    # If snapshot doesn't exist, create it
    if not snapshot_path.exists():
        print(f"\n📸 Creating initial snapshot at {snapshot_path}")
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with open(snapshot_path, 'w') as f:
            json.dump(normalized_template, f, indent=2, sort_keys=True)
        pytest.skip("Initial snapshot created. Run test again to validate.")
    
    # Load the snapshot
    with open(snapshot_path, 'r') as f:
        snapshot = json.load(f)
    
    # Compare the template with the snapshot
    if normalized_template != snapshot:
        # Save the actual template for comparison
        actual_path = snapshot_path.parent / "kavun_stack_actual.json"
        with open(actual_path, 'w') as f:
            json.dump(normalized_template, f, indent=2, sort_keys=True)
        
        # Generate a diff
        diff_path = snapshot_path.parent / "kavun_stack_diff.txt"
        try:
            import difflib
            snapshot_str = json.dumps(snapshot, indent=2, sort_keys=True).splitlines()
            actual_str = json.dumps(normalized_template, indent=2, sort_keys=True).splitlines()
            
            diff = difflib.unified_diff(
                snapshot_str,
                actual_str,
                fromfile='snapshot',
                tofile='actual',
                lineterm=''
            )
            
            with open(diff_path, 'w') as f:
                f.write('\n'.join(diff))
            
            pytest.fail(
                f"❌ CloudFormation template differs from snapshot!\n"
                f"Snapshot: {snapshot_path}\n"
                f"Actual: {actual_path}\n"
                f"Diff: {diff_path}\n\n"
                f"If the changes are intentional, update the snapshot by running:\n"
                f"  rm {snapshot_path} && pytest {__file__}"
            )
        except Exception as e:
            pytest.fail(
                f"❌ CloudFormation template differs from snapshot!\n"
                f"Snapshot: {snapshot_path}\n"
                f"Actual: {actual_path}\n"
                f"Error generating diff: {str(e)}"
            )
    
    print(f"\n✅ CloudFormation template matches snapshot!")


def test_stack_has_required_resources():
    """Test that the stack contains all required resources"""
    template = synthesize_stack()
    resources = template.get("Resources", {})
    
    # Check for required resources
    required_resource_types = [
        "AWS::SecretsManager::Secret",
        "AWS::DynamoDB::Table",
        "AWS::S3::Bucket",
        "AWS::Lambda::Function",
        "AWS::StepFunctions::StateMachine",
        "AWS::CloudFront::Distribution",
        "AWS::IAM::Role",
        "AWS::Logs::LogGroup",
        "AWS::SNS::Topic",
        "AWS::CloudWatch::Alarm"
    ]
    
    found_types = set()
    for resource_id, resource in resources.items():
        resource_type = resource.get("Type")
        found_types.add(resource_type)
    
    missing_types = [rt for rt in required_resource_types if rt not in found_types]
    
    assert not missing_types, f"Missing required resource types: {missing_types}"
    print(f"\n✅ All required resource types present: {len(required_resource_types)}")


def test_lambda_functions_count():
    """Test that we have exactly 4 Lambda functions"""
    template = synthesize_stack()
    resources = template.get("Resources", {})
    
    lambda_functions = [
        r for r in resources.values()
        if r.get("Type") == "AWS::Lambda::Function"
    ]
    
    assert len(lambda_functions) == 4, f"Expected 4 Lambda functions, found {len(lambda_functions)}"
    print(f"\n✅ Found {len(lambda_functions)} Lambda functions")


def test_dynamodb_has_gsi():
    """Test that DynamoDB table has the StatusIndex GSI"""
    template = synthesize_stack()
    resources = template.get("Resources", {})
    
    # Find DynamoDB table
    dynamodb_tables = [
        r for r in resources.values()
        if r.get("Type") == "AWS::DynamoDB::Table"
    ]
    
    assert len(dynamodb_tables) == 1, "Expected exactly 1 DynamoDB table"
    
    table = dynamodb_tables[0]
    gsi = table.get("Properties", {}).get("GlobalSecondaryIndexes", [])
    
    assert len(gsi) > 0, "DynamoDB table should have at least one GSI"
    
    # Check for StatusIndex
    index_names = [idx.get("IndexName") for idx in gsi]
    assert "StatusIndex" in index_names, "DynamoDB table should have StatusIndex GSI"
    
    print(f"\n✅ DynamoDB table has StatusIndex GSI")


if __name__ == "__main__":
    # Allow running the test directly
    pytest.main([__file__, "-v"])

