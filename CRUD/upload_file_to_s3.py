from typing import Dict, Any, Tuple
from dotenv import load_dotenv
import logging
import aioboto3
from botocore.exceptions import NoCredentialsError, PartialCredentialsError
import os

logger = logging.getLogger(__name__)

# Load environment variables from .env
load_dotenv()

# AWS S3 Configuration
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

def validate_json_structure(json_data: Dict[Any, Any]) -> Tuple[bool, str]:
    """
    Validate that the JSON contains required columns: 'Filename' and 'Inference'
    
    Args:
        json_data: The parsed JSON data to validate
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        # Check if it's a list of records or a single record
        if isinstance(json_data, list):
            if not json_data:
                return False, "JSON file is empty"
            # Check the first record for required columns
            sample_record = json_data[0]
        elif isinstance(json_data, dict):
            sample_record = json_data
        else:
            return False, "JSON must be either a dictionary or a list of dictionaries"
        
        # Check for required columns
        required_columns = ['filename', 'prediction']
        missing_columns = []
        
        for column in required_columns:
            if column not in sample_record:
                missing_columns.append(column)
        
        if missing_columns:
            return False, f"Missing required columns: {', '.join(missing_columns)}"
        
        # If it's a list, validate all records have the required columns
        if isinstance(json_data, list):
            for i, record in enumerate(json_data):
                for column in required_columns:
                    if column not in record:
                        return False, f"Record {i+1} is missing required column: {column}"
        
        return True, "Validation successful"
        
    except Exception as e:
        logger.error(f"Error during JSON validation: {str(e)}")
        return False, f"Validation error: {str(e)}"

async def simple_upload_to_s3(file: Any, filename: str, folder_path: str = "evalai/uploads") -> Tuple[bool, str, str]:
    """
    Simple S3 upload without any validation - used for testing and general file uploads.
    
    Args:
        file: File object to upload
        filename: Name of the file
        folder_path: S3 folder path (default: evalai/uploads)
        
    Returns:
        Tuple of (success, message, s3_url)
    """
    session = aioboto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_REGION")
    )

    async with session.client('s3') as s3_client:
        try:
            # Upload file to S3 without validation
            s3_key = f"{folder_path}/{filename}"
            
            # Set proper Content-Type based on file extension
            content_type = "application/octet-stream"  # Default
            if filename.lower().endswith('.json'):
                content_type = "application/json"
            elif filename.lower().endswith(('.jpg', '.jpeg')):
                content_type = "image/jpeg"
            elif filename.lower().endswith('.png'):
                content_type = "image/png"
            elif filename.lower().endswith('.txt'):
                content_type = "text/plain"
            
            extra_args = {
                'ContentType': content_type,
                'ContentDisposition': 'inline'
            }
            
            await s3_client.upload_fileobj(
                file,
                os.getenv("S3_BUCKET_NAME"),
                s3_key,
                ExtraArgs=extra_args
            )
            
            # Construct the S3 URL - use utility function for better URL generation
            from .s3_utils import generate_public_s3_url
            try:
                file_url = generate_public_s3_url(s3_key)
            except ValueError:
                # Fallback to direct URL construction if utils fail
                file_url = f"https://{os.getenv('S3_BUCKET_NAME')}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{s3_key}"
            
            return True, "File uploaded successfully", file_url
            
        except NoCredentialsError:
            return False, "AWS credentials not found", ""
        except PartialCredentialsError:
            return False, "Incomplete AWS credentials", ""
        except Exception as e:
            return False, f"Failed to upload file: {str(e)}", ""
