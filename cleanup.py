import boto3

BUCKET_NAME = "ppe-detection-image"
MAX_IMAGES = int(input("How many images do you want to keep? "))

s3 = boto3.client('s3')

# List all objects
response = s3.list_objects_v2(Bucket=BUCKET_NAME)
objects = response.get('Contents', [])

if len(objects) > MAX_IMAGES:
    # Sort by oldest first
    objects_sorted = sorted(objects, key=lambda x: x['LastModified'])
    delete_count = len(objects) - MAX_IMAGES
    for obj in objects_sorted[:delete_count]:
        s3.delete_object(Bucket=BUCKET_NAME, Key=obj['Key'])
        print(f"Deleted old S3 object: {obj['Key']}")

print("Old images cleaned up! ✅")