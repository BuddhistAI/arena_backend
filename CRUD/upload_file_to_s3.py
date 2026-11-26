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
