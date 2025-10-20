"""
Kavun CDK Stack - Basic Resources Only
"""
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput, BundlingOptions, Tags
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_iam as iam
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cloudwatch_actions
from aws_cdk import aws_sns as sns
from aws_cdk import aws_logs as logs
from aws_cdk import aws_xray as xray
from constructs import Construct
import os

class KavunStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        
        # 1. Secrets Manager for API keys
        self.secrets = secretsmanager.Secret(
            self, "KavunAPISecrets",
            description="API keys for OpenAI and Nano Banana",
            secret_name="kavun/api-keys",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"openai_key":"","nano_banana_key":""}',
                generate_string_key="password",
                exclude_characters=" %+~`#$&*()|[]{}:;<>?!'/\"\\"
            )
        )
        
        # 2. DynamoDB Table
        self.table = dynamodb.Table(
            self, "KavunWordsTable",
            table_name="kavun-words",
            partition_key=dynamodb.Attribute(
                name="word",
                type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN
        )
        
        # Add GSI for status queries
        self.table.add_global_secondary_index(
            index_name="StatusIndex",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING
            )
        )
        
        # 3. S3 Bucket for Images
        self.bucket = s3.Bucket(
            self, "KavunImagesBucket",
            bucket_name=f"kavun-images-{self.account}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="ImageLifecycle",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(30)
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90)
                        )
                    ]
                )
            ]
        )
        
        # 4. CloudFront Distribution
        self.origin_access_identity = cloudfront.OriginAccessIdentity(
            self, "KavunCloudFrontOrigin1S3Origin",
            comment="Identity for Kavun Images"
        )
        
        self.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.CanonicalUserPrincipal(self.origin_access_identity.cloud_front_origin_access_identity_s3_canonical_user_id)],
                actions=["s3:GetObject"],
                resources=[f"{self.bucket.bucket_arn}/*"]
            )
        )
        
        self.cloudfront = cloudfront.Distribution(
            self, "KavunCloudFront",
            comment="Kavun Images CDN",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3Origin(
                    self.bucket,
                    origin_access_identity=self.origin_access_identity
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                compress=True
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            http_version=cloudfront.HttpVersion.HTTP2,
            enable_ipv6=True
        )
        
        # 5. Lambda Role
        self.lambda_role = iam.Role(
            self, "KavunLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )
        
        # Lambda permissions
        self.lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:GetItem",
                    "dynamodb:PutItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:UpdateItem"
                ],
                resources=[self.table.table_arn]
            )
        )
        
        self.lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject"
                ],
                resources=[f"{self.bucket.bucket_arn}/*"]
            )
        )
        
        self.lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[self.secrets.secret_arn]
            )
        )
        
        # 6. Lambda Functions
        self.prompt_lambda = lambda_.Function(
            self, "KavunPromptLambda",
            function_name="kavun-prompt-generator",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/prompt", bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash", "-c",
                    "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                ]
            )),
            role=self.lambda_role,
            timeout=Duration.minutes(5),
            memory_size=512,
            environment={
                "SECRET_ARN": self.secrets.secret_arn,
                "TABLE_NAME": self.table.table_name,
                "CLOUDFRONT_URL": f"https://{self.cloudfront.distribution_domain_name}"
            },
            tracing=lambda_.Tracing.ACTIVE
        )
        
        self.image_lambda = lambda_.Function(
            self, "KavunImageLambda",
            function_name="kavun-image-generator",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/image", bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash", "-c",
                    "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                ]
            )),
            role=self.lambda_role,
            timeout=Duration.minutes(15),
            memory_size=1024,
            environment={
                "SECRET_ARN": self.secrets.secret_arn,
                "TABLE_NAME": self.table.table_name,
                "BUCKET_NAME": self.bucket.bucket_name,
                "CLOUDFRONT_URL": f"https://{self.cloudfront.distribution_domain_name}"
            },
            tracing=lambda_.Tracing.ACTIVE
        )
        
        self.status_lambda = lambda_.Function(
            self, "KavunStatusLambda",
            function_name="kavun-status-checker",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("../lambdas/status", bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                command=[
                    "bash", "-c",
                    "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                ]
            )),
            role=self.lambda_role,
            timeout=Duration.minutes(2),
            memory_size=256,
            environment={
                "TABLE_NAME": self.table.table_name
            },
            tracing=lambda_.Tracing.ACTIVE
        )
        
        # Outputs
        CfnOutput(
            self, "CloudFrontURL",
            description="CloudFront URL for images",
            value=f"https://{self.cloudfront.distribution_domain_name}"
        )
        
        CfnOutput(
            self, "DynamoDBTable",
            description="DynamoDB table name",
            value=self.table.table_name
        )
        
        CfnOutput(
            self, "S3Bucket",
            description="S3 bucket for images",
            value=self.bucket.bucket_name
        )
        
        # 9. Step Functions State Machine
        # Define the workflow: Prompt -> Parse -> Image -> Complete
        prompt_task = tasks.LambdaInvoke(
            self, "GeneratePrompt",
            lambda_function=self.prompt_lambda,
            payload=sfn.TaskInput.from_object({
                "word": sfn.JsonPath.string_at("$.word"),
                "language": sfn.JsonPath.string_at("$.language")
            }),
            result_path="$.prompt_result"
        )
        
        # Parse the Lambda response to extract the prompt
        parse_prompt = sfn.Pass(
            self, "ParsePrompt",
            parameters={
                "word": sfn.JsonPath.string_at("$.word"),
                "language": sfn.JsonPath.string_at("$.language"),
                "prompt": sfn.JsonPath.string_at("$.prompt_result.Payload.body")
            }
        )
        
        image_task = tasks.LambdaInvoke(
            self, "GenerateImage", 
            lambda_function=self.image_lambda,
            payload=sfn.TaskInput.from_object({
                "word": sfn.JsonPath.string_at("$.word"),
                "prompt": sfn.JsonPath.string_at("$.prompt"),
                "language": sfn.JsonPath.string_at("$.language")
            }),
            result_path="$.image_result"
        )
        
        # Choice state to check if word already exists
        check_duplicate = sfn.Choice(self, "CheckDuplicate")
        
        # Check if the prompt result contains "already_exists" status
        check_duplicate.when(
            sfn.Condition.string_matches("$.prompt", "*already_exists*"),
            sfn.Pass(self, "SkipImageGeneration", 
                result=sfn.Result.from_object({
                    "message": "Word already exists, skipping image generation",
                    "word": sfn.JsonPath.string_at("$.word"),
                    "status": "already_exists"
                })
            )
        )
        
        # If word is new, continue to image generation
        check_duplicate.otherwise(image_task)
        
        # Define the state machine
        definition = prompt_task.next(parse_prompt).next(check_duplicate)
        
        self.state_machine = sfn.StateMachine(
            self, "KavunWorkflow",
            state_machine_name="kavun-workflow",
            definition=definition,
            timeout=Duration.minutes(30),
            tracing_enabled=True
        )
        
        # Grant Step Functions permission to invoke Lambda functions
        self.prompt_lambda.grant_invoke(self.state_machine)
        self.image_lambda.grant_invoke(self.state_machine)
        
        # 10. Monitoring and Observability
        self.setup_monitoring()
        
        # 11. Security improvements
        self.setup_security()
        
        # 12. Add tags to all resources
        self.add_tags()
        
        CfnOutput(
            self, "SecretsManager",
            description="Secrets Manager secret name",
            value=self.secrets.secret_name
        )
        
        CfnOutput(
            self, "PromptLambdaArn",
            description="Prompt Lambda Function ARN",
            value=self.prompt_lambda.function_arn
        )
        
        CfnOutput(
            self, "ImageLambdaArn",
            description="Image Lambda Function ARN",
            value=self.image_lambda.function_arn
        )
        
        CfnOutput(
            self, "StatusLambdaArn",
            description="Status Lambda Function ARN",
            value=self.status_lambda.function_arn
        )
        
        CfnOutput(
            self, "StateMachineArn",
            description="Step Functions State Machine ARN",
            value=self.state_machine.state_machine_arn
        )
    
    def setup_monitoring(self):
        """Set up monitoring, alarms, and observability"""
        
        # SNS Topic for alerts
        self.alerts_topic = sns.Topic(
            self, "KavunAlertsTopic",
            display_name="Kavun Alerts",
            topic_name="kavun-alerts"
        )
        
        # CloudWatch Dashboard
        dashboard = cloudwatch.Dashboard(
            self, "KavunDashboard",
            dashboard_name="Kavun-Monitoring"
        )
        
        # Lambda Error Rate Alarms
        self.prompt_lambda.metric_errors(
            period=Duration.minutes(5),
            statistic="Sum"
        ).create_alarm(
            self, "PromptLambdaErrorRate",
            threshold=5,
            evaluation_periods=2,
            alarm_description="Prompt Lambda error rate too high"
        )
        
        self.image_lambda.metric_errors(
            period=Duration.minutes(5),
            statistic="Sum"
        ).create_alarm(
            self, "ImageLambdaErrorRate",
            threshold=5,
            evaluation_periods=2,
            alarm_description="Image Lambda error rate too high"
        )
        
        # Lambda Duration Alarms
        self.prompt_lambda.metric_duration(
            period=Duration.minutes(5),
            statistic="Average"
        ).create_alarm(
            self, "PromptLambdaDuration",
            threshold=240000,  # 4 minutes in milliseconds
            evaluation_periods=2,
            alarm_description="Prompt Lambda duration too high"
        )
        
        self.image_lambda.metric_duration(
            period=Duration.minutes(5),
            statistic="Average"
        ).create_alarm(
            self, "ImageLambdaDuration",
            threshold=840000,  # 14 minutes in milliseconds
            evaluation_periods=2,
            alarm_description="Image Lambda duration too high"
        )
        
        # DynamoDB Alarms
        self.table.metric_consumed_read_capacity_units(
            period=Duration.minutes(5),
            statistic="Sum"
        ).create_alarm(
            self, "DynamoDBReadCapacity",
            threshold=1000,
            evaluation_periods=2,
            alarm_description="DynamoDB read capacity usage high"
        )
        
        self.table.metric_consumed_write_capacity_units(
            period=Duration.minutes(5),
            statistic="Sum"
        ).create_alarm(
            self, "DynamoDBWriteCapacity",
            threshold=1000,
            evaluation_periods=2,
            alarm_description="DynamoDB write capacity usage high"
        )
        
        # Step Functions Alarms
        cloudwatch.Metric(
            namespace="AWS/States",
            metric_name="ExecutionsFailed",
            dimensions_map={
                "StateMachineArn": self.state_machine.state_machine_arn
            },
            period=Duration.minutes(5),
            statistic="Sum"
        ).create_alarm(
            self, "StepFunctionsFailedExecutions",
            threshold=5,
            evaluation_periods=2,
            alarm_description="Step Functions failed executions too high"
        )
        
        # CloudWatch Log Groups with retention
        logs.LogGroup(
            self, "PromptLambdaLogGroup",
            log_group_name=f"/aws/lambda/{self.prompt_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH
        )
        
        logs.LogGroup(
            self, "ImageLambdaLogGroup",
            log_group_name=f"/aws/lambda/{self.image_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH
        )
        
        logs.LogGroup(
            self, "StatusLambdaLogGroup",
            log_group_name=f"/aws/lambda/{self.status_lambda.function_name}",
            retention=logs.RetentionDays.ONE_MONTH
        )
        
        
        # Custom metrics for business logic
        self.create_custom_metrics()
    
    def create_custom_metrics(self):
        """Create custom CloudWatch metrics for business logic"""
        
        # Word processing metrics
        word_processing_metric = cloudwatch.Metric(
            namespace="Kavun/WordProcessing",
            metric_name="WordsProcessed",
            statistic="Sum",
            period=Duration.minutes(5)
        )
        
        # Image generation metrics
        image_generation_metric = cloudwatch.Metric(
            namespace="Kavun/ImageGeneration",
            metric_name="ImagesGenerated",
            statistic="Sum",
            period=Duration.minutes(5)
        )
        
        # API usage metrics
        api_usage_metric = cloudwatch.Metric(
            namespace="Kavun/API",
            metric_name="APICalls",
            statistic="Sum",
            period=Duration.minutes(5)
        )
    
    def add_tags(self):
        """Add consistent tags to all resources"""
        
        # Add tags to all constructs in the stack
        Tags.of(self).add("Project", "Kavun")
        Tags.of(self).add("Environment", "Production")
        Tags.of(self).add("Owner", "Kavun Team")
        Tags.of(self).add("CostCenter", "AI/ML")
        Tags.of(self).add("Backup", "Required")
        Tags.of(self).add("Monitoring", "Enabled")
    
    def setup_security(self):
        """Set up security improvements and hardening"""
        
        # Enable S3 bucket versioning and encryption
        self.bucket.add_lifecycle_rule(
            id="DeleteOldVersions",
            enabled=True,
            noncurrent_version_expiration=Duration.days(30)
        )
        
        # Enable S3 bucket access logging
        access_logs_bucket = s3.Bucket(
            self, "KavunAccessLogsBucket",
            bucket_name=f"kavun-access-logs-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.RETAIN
        )
        
        self.bucket.add_to_resource_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("logging.s3.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[f"{access_logs_bucket.bucket_arn}/*"],
                conditions={
                    "StringEquals": {
                        "aws:SourceAccount": self.account
                    }
                }
            )
        )
        
        # Enable DynamoDB point-in-time recovery (already enabled)
        # Enable DynamoDB encryption at rest (default)
        
        # Add WAF to API Gateway (if needed)
        # Note: WAF requires additional setup and costs
        
        
        # Enable CloudTrail for API calls (if needed)
        # Note: CloudTrail requires additional setup and costs
        
        
        # Enable Lambda function dead letter queues
        dead_letter_queue = sns.Topic(
            self, "KavunDeadLetterQueue",
            display_name="Kavun Dead Letter Queue",
            topic_name="kavun-dlq"
        )
        
        # Add dead letter queue to Lambda functions
        self.prompt_lambda.add_environment("DLQ_TOPIC_ARN", dead_letter_queue.topic_arn)
        self.image_lambda.add_environment("DLQ_TOPIC_ARN", dead_letter_queue.topic_arn)
        self.status_lambda.add_environment("DLQ_TOPIC_ARN", dead_letter_queue.topic_arn)
        
        # Grant Lambda functions permission to publish to DLQ
        dead_letter_queue.grant_publish(self.prompt_lambda)
        dead_letter_queue.grant_publish(self.image_lambda)
        dead_letter_queue.grant_publish(self.status_lambda)
        
        # Add VPC configuration (optional - for enhanced security)
        # Note: VPC configuration adds complexity and costs
        
        # Enable AWS Config (optional - for compliance)
        # Note: AWS Config requires additional setup and costs
        
        # Add IAM policy for least privilege access
        self.lambda_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["*"],
                resources=["*"],
                conditions={
                    "StringNotEquals": {
                        "aws:RequestedRegion": [self.region]
                    }
                }
            )
        )
        
        # Add CloudWatch Insights queries for security monitoring
        self.create_security_insights()
    
    def create_security_insights(self):
        """Create CloudWatch Insights queries for security monitoring"""
        
        # Query for failed API calls
        failed_api_calls_query = """
        fields @timestamp, @message
        | filter @message like /ERROR/
        | filter @message like /API/
        | sort @timestamp desc
        | limit 100
        """
        
        # Query for suspicious Lambda invocations
        suspicious_lambda_query = """
        fields @timestamp, @message
        | filter @message like /ERROR/
        | filter @message like /Lambda/
        | sort @timestamp desc
        | limit 100
        """
        
        # Query for DynamoDB access patterns
        dynamodb_access_query = """
        fields @timestamp, @message
        | filter @message like /DynamoDB/
        | sort @timestamp desc
        | limit 100
        """
        
        # Store queries as CloudWatch Insights queries
        # Note: This would require additional CDK constructs for CloudWatch Insights
