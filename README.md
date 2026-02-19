# 🦺 AWS Serverless PPE Detection System

A fully serverless, event-driven Personal Protective Equipment (PPE) detection system built on AWS.

This project uses a Raspberry Pi to capture images and upload them to Amazon S3. Each upload triggers an AWS Lambda function that analyzes the image using Amazon Rekognition’s built-in PPE detection API. If a safety violation is detected (e.g., missing helmet or vest), the system sends a real-time alert via Amazon SNS and logs the violation in a secondary S3 bucket.

---

# 🚀 Architecture Overview

## System Flow

1. Raspberry Pi captures image
2. Image uploaded to S3 (Images Bucket)
3. S3 event triggers Lambda
4. Lambda:
   - Calls Rekognition `detect_protective_equipment`
   - Evaluates PPE compliance
   - Sends SNS alert if violation detected
   - Logs violation details as text file in second S3 bucket
5. Local cleanup script removes outdated objects from S3

---

# 🏗 Architecture Diagram

```
Raspberry Pi
     ↓
Amazon S3 (Images Bucket)
     ↓ (Event Trigger)
AWS Lambda
     ↓
Amazon Rekognition (PPE Detection)
     ↓
Amazon SNS (Alert Notification)
     ↓
Amazon S3 (Violations Bucket - Text Logs)
```

---

# 🧰 AWS Services Used

- Amazon S3
- AWS Lambda
- Amazon Rekognition (Built-in PPE Detection)
- Amazon SNS

---

# 📁 Repository Structure

```
ppe-detection-aws/
│
├── raspberry_pi/
│   └── image_uploader.py
│
├── lambda/
│   ├── handler.py
│   ├── rekognition_service.py
│   ├── sns_service.py
│   ├── s3_service.py
│   ├── violation_logger.py
│   └── requirements.txt
│
├── cleanup/
│   └── delete_old_s3_objects.py
│
├── docs/
│   └── architecture.md
│
├── .env.example
├── .gitignore
└── README.md
```

---

# ⚙️ How It Works

## 1️⃣ Raspberry Pi Upload

The Raspberry Pi captures images and uploads them directly to the Images S3 bucket.

Example upload flow:

```
capture image → upload to S3 → S3 event fires
```

---

## 2️⃣ Lambda Processing

When a new image is uploaded:

- Lambda extracts the S3 object key
- Calls Rekognition PPE detection API
- Checks for missing required PPE
- Publishes SNS alert if violation detected
- Appends violation details to a text file in the violations bucket

---

## 3️⃣ Violation Logging

Violations are stored as structured text logs in S3.

Example log entry:

```
Timestamp: 2026-02-19T14:32:10Z
Image: site_camera_01_1234.jpg
Violation: Missing helmet
Confidence: 98.4%
---------------------------------------
```

Note:
Since S3 does not support native append operations, the Lambda function:
1. Downloads the existing log file
2. Appends new violation entry
3. Uploads the updated file back to S3

---

# 🧹 Cleanup Utility

The `cleanup` folder contains a local script to remove old objects from S3 buckets.

Example usage:

```bash
python delete_old_s3_objects.py --bucket images-bucket --days 7
```

This helps manage storage and maintain cost efficiency.

---

# 🔐 Security Considerations

- IAM roles follow least privilege principle
- No hardcoded credentials
- Environment variables used for configuration
- S3 buckets configured as private
- SNS topics restricted via IAM policies
- Lambda execution role limited to required services only

---

# 🧠 Design Decisions

- Event-driven architecture for scalability
- Fully serverless implementation
- Built-in Rekognition PPE detection (no custom ML training required)
- Lightweight S3-based logging for low-frequency violations
- Modular Lambda structure for maintainability
- Separation between edge device logic and cloud processing

---

# 📈 Scalability & Improvements

Future improvements may include:

- Replacing text-based logging with DynamoDB
- Adding CloudWatch structured logging
- Implementing Infrastructure as Code (AWS SAM / Terraform)
- Adding CI/CD pipeline for automated Lambda deployment
- Adding lifecycle policies to S3 buckets
- Implementing authentication for Raspberry Pi uploads

---

# 🛠 Deployment Notes

Required AWS resources:

- Two S3 buckets:
  - Images bucket (triggers Lambda)
  - Violations bucket (stores text logs)
- Lambda function (Python runtime)
- SNS topic (with email/SMS subscription)
- IAM role with:
  - S3 read/write access
  - Rekognition access
  - SNS publish permissions

---

# 📜 License

MIT License

---

# 👤 Author

Your Name  
Cloud & AI Engineering Project  
Serverless Computer Vision Implementation
