import cv2
import boto3
import threading
import time
from datetime import datetime, UTC, timezone, timedelta
import logging

# ===============================
# CONFIGURATION
# ===============================
BUCKET_NAME = "ppe-detection-image"
REGION = "us-east-1"
SNS_TOPIC_ARN = "arn:aws:sns:us-east-1:422132663185:PPE-Alerts"
STATE_BUCKET = "ppe-detection-state"

LAST_ALERT_FILE = "last_alert.txt"
VIOLATIONS_FILE = "violations.txt"

COOLDOWN = 300             # seconds before sending next SNS alert batch
NO_PERSON_ALERT = 3       # seconds without person triggers alert
VIOLATION_DURATION = 3     # seconds before confirming violation
PROCESS_INTERVAL = 0.5     # seconds between Rekognition calls
UPLOAD_INTERVAL = 3        # seconds between S3 uploads
CONFIDENCE = 80

MAX_IMAGES_PER_VIOLATION = 3

tz_utc3 = timezone(timedelta(hours=3))
# ===============================
# GLOBAL STATE
# ===============================
violation_start_time = None
persistent_missing_items = None
last_alert = 0
last_no_person_alert = 0

latest_response = None
lock = threading.Lock()

batch_violations = {}       # current batch of frames to send
violation_image_count = 0
batch_sent = False
active_violation = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)

# ===============================
# AWS CLIENTS
# ===============================
rekognition = boto3.client('rekognition', region_name=REGION)
sns = boto3.client('sns', region_name=REGION)
s3 = boto3.client('s3', region_name=REGION)

# ===============================
# HELPER FUNCTIONS
# ===============================
def async_write_s3(bucket, key, content):
    threading.Thread(
        target=lambda: s3.put_object(Bucket=bucket, Key=key, Body=content),
        daemon=True
    ).start()

def handle_violation_async(violations_snapshot):
    try:
        # Save violations snapshot to S3
        async_write_s3(
            STATE_BUCKET,
            VIOLATIONS_FILE,
            "\n".join([f"{k}: {', '.join(v)}" for k, v in violations_snapshot.items()])
        )

        message_lines = []
        for frame_name, info in violations_snapshot.items():
            missing_items = info["missing"]
            timestamp = info["time"]
            try:
                url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': BUCKET_NAME, 'Key': frame_name},
                    ExpiresIn=3600
                )
                message_lines.append(f"{frame_name} | {timestamp} | Missing: {', '.join(missing_items)}\nImage: {url}")
            except Exception as e:
                logging.error(f"Presigned URL error: {e}")

        if message_lines:
            message = "🚨 PPE ALERT!\n\nViolations:\n\n" + "\n\n".join(message_lines)
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject="🚨 PPE ALERT",
                Message=message
            )

            num_frames = len(violations_snapshot)
            logging.info(f"SNS alert sent with {num_frames} violation frame(s)!")
            print(f"📢 SNS alert sent with {num_frames} violation frame(s) included!")

    except Exception as e:
        logging.error(f"Async alert error: {e}")

def handle_no_person_alert_async():
    now = datetime.now(tz_utc3).strftime("%H:%M:%S")
    logging.info(f"{now} | INFO | No person detected in frame")

# ===============================
# CAMERA SETUP
# ===============================
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 420)

if not cap.isOpened():
    print("Camera not accessible")
    exit()

print("Starting LIVE PPE system with ALERTS... Press Q to quit.")

last_upload_time = 0
last_process_time = 0

cv2.namedWindow("LIVE PPE SYSTEM - ALERTS", cv2.WINDOW_NORMAL)
cv2.setWindowProperty("LIVE PPE SYSTEM - ALERTS", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

# ===============================
# REKOGNITION THREAD
# ===============================
def detect_ppe(image_bytes):
    global latest_response
    try:
        response = rekognition.detect_protective_equipment(
            Image={'Bytes': image_bytes},
            SummarizationAttributes={
                'MinConfidence': CONFIDENCE,
                'RequiredEquipmentTypes': ['HEAD_COVER', 'FACE_COVER', 'HAND_COVER']
            }
        )
        with lock:
            latest_response = response
    except Exception as e:
        logging.error(f"Rekognition error: {e}")

# ===============================
# MAIN LOOP
# ===============================
while True:
    ret, frame = cap.read()
    if not ret:
        break

    height, width, _ = frame.shape
    current_time = time.time()

    # ---- Rekognition async ----
    if current_time - last_process_time > PROCESS_INTERVAL and not lock.locked():
        _, buffer = cv2.imencode('.jpg', frame)
        image_bytes = buffer.tobytes()
        threading.Thread(target=detect_ppe, args=(image_bytes,), daemon=True).start()
        last_process_time = current_time

    # ---- Draw latest detection ----
    helmet_status = "Unknown"
    mask_status = "Unknown"
    gloves_status = "Unknown"
    active_violation = False

    with lock:
        response = latest_response

    if response:
        persons = response.get('Persons', [])
        if not persons:
            if time.time() - last_no_person_alert > NO_PERSON_ALERT:
                threading.Thread(target=handle_no_person_alert_async, daemon=True).start()
                last_no_person_alert = time.time()


        for person in persons:
            has_helmet = has_mask = has_gloves = False

            # Person box
            box = person['BoundingBox']
            left = int(box['Left'] * width)
            top = int(box['Top'] * height)
            w = int(box['Width'] * width)
            h = int(box['Height'] * height)
            cv2.rectangle(frame, (left, top), (left + w, top + h), (255, 0, 0), 2)
            cv2.putText(frame, "Person", (left, top - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

            # PPE boxes
            for body_part in person.get('BodyParts', []):
                for eq in body_part.get('EquipmentDetections', []):
                    eq_box = eq.get('BoundingBox')
                    if not eq_box:
                        continue
                    e_left = int(eq_box['Left'] * width)
                    e_top = int(eq_box['Top'] * height)
                    e_w = int(eq_box['Width'] * width)
                    e_h = int(eq_box['Height'] * height)

                    if eq['Type'] == 'HEAD_COVER' and eq['Confidence'] >= CONFIDENCE:
                        has_helmet = True
                    if eq['Type'] == 'FACE_COVER' and eq['Confidence'] >= CONFIDENCE:
                        has_mask = True
                    if eq['Type'] == 'HAND_COVER' and eq['Confidence'] >= CONFIDENCE:
                        has_gloves = True

                    is_missing = eq['Confidence'] < CONFIDENCE
                    color = (0, 0, 255) if is_missing else (0, 255, 0)
                    cv2.rectangle(frame, (e_left, e_top), (e_left + e_w, e_top + e_h), color, 2)
                    cv2.putText(frame, f"{eq['Type']} ({int(eq['Confidence'])}%)", (e_left, e_top - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

            helmet_status = "Helmet: OK" if has_helmet else "Helmet: MISSING"
            mask_status = "Mask: OK" if has_mask else "Mask: MISSING"
            gloves_status = "Gloves: OK" if has_gloves else "Gloves: MISSING"

            # Violation check
            missing_items = []
            if not has_helmet: missing_items.append("Helmet")
            if not has_mask: missing_items.append("Mask")
            if not has_gloves: missing_items.append("Gloves")

            if missing_items:
                active_violation = True
                if violation_start_time is None:
                    violation_start_time = current_time
                elif current_time - violation_start_time >= VIOLATION_DURATION:
                    persistent_missing_items = missing_items.copy()
            else:
                violation_start_time = None
                persistent_missing_items = None
                active_violation = False
                violation_image_count = 0
                batch_sent = False
                batch_violations.clear()

    # ---- Draw status text ----
    cv2.putText(frame, helmet_status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if "OK" in helmet_status else (0, 0, 255), 2)
    cv2.putText(frame, mask_status, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if "OK" in mask_status else (0, 0, 255), 2)
    cv2.putText(frame, gloves_status, (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if "OK" in gloves_status else (0, 0, 255), 2)

    # ---- Upload frame every UPLOAD_INTERVAL ----
    if current_time - last_upload_time > UPLOAD_INTERVAL:
        _, buffer = cv2.imencode('.jpg', frame)
        filename = datetime.now(tz_utc3).strftime("frame_%Y%m%d_%H%M%S.jpg")

        # Upload S3 async
        threading.Thread(
            target=lambda b=buffer, n=filename: s3.put_object(
                Bucket=BUCKET_NAME,
                Key=n,
                Body=b.tobytes(),
                ContentType="image/jpeg"
            ),
            daemon=True
        ).start()
        last_upload_time = current_time

        # Add frame to batch if active violation
        if active_violation and persistent_missing_items:
            logging.info(f"Frame uploaded: {filename} | Missing: {', '.join(persistent_missing_items)}")
            
            # Add frame only once
            if filename not in batch_violations:
                batch_violations[filename] = {
                    "missing": persistent_missing_items.copy(),
                    "time": datetime.now(tz_utc3).strftime("%H:%M:%S")
                }
            
            if batch_violations and (time.time() - last_alert > COOLDOWN):
                snapshot = batch_violations.copy()
                handle_violation_async(snapshot)  # send all frames in one email
                batch_violations.clear()
                last_alert = time.time()


    # ---- Show frame ----
    cv2.imshow("LIVE PPE SYSTEM - ALERTS", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        print("Shutting down... sending final alert if needed")
        if batch_violations:
            snapshot = batch_violations.copy()
            try:
                # Call SNS handling synchronously
                # Do NOT use threads here
                handle_violation_async(snapshot)
                print("✅ Final SNS alert sent successfully!")
            except Exception as e:
                print(f"❌ Failed to send final SNS alert: {e}")
        break

cap.release()
cv2.destroyAllWindows()