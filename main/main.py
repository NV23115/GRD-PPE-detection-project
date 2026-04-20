import cv2
import boto3
import threading
import time
import zmq
import os
from dotenv import load_dotenv
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# ===============================
# CONFIG (ENV SUPPORT)
# ===============================
load_dotenv()

PORT = "9090"
LOG_PORT = "9091"
REGION = os.getenv('AWS_REGION', 'us-east-1')
IMAGE_BUCKET = os.getenv('S3_BUCKET', 'ppe-detection-images')

CONFIDENCE_LEVEL = int(os.getenv('CONFIDENCE_LEVEL', '80'))
print(f"Startup threshold: {CONFIDENCE_LEVEL}%")
AI_SCAN_INTERVAL = float(os.getenv('AI_SCAN_INTERVAL', '2'))
LOG_COOLDOWN = float(os.getenv('LOG_COOLDOWN', '1.0'))
UPLOAD_COOLDOWN = float(os.getenv('UPLOAD_COOLDOWN', '5'))
SNS_TOPIC_ARN = os.getenv('SNS_TOPIC_ARN', '')

# ===============================
# ZMQ
# ===============================
context = zmq.Context()

footage_socket = context.socket(zmq.PUB)
footage_socket.bind(f"tcp://*:{PORT}")

log_socket = context.socket(zmq.PUB)
log_socket.bind(f"tcp://*:{LOG_PORT}")

# ===============================
# AWS
# ===============================
s3_client = boto3.client('s3', region_name=REGION)
rekognition = boto3.client('rekognition', region_name=REGION)
sns = boto3.client('sns', region_name=REGION)

executor = ThreadPoolExecutor(max_workers=2)

# ===============================
# STATE
# ===============================
class State:
    def __init__(self):
        self.latest_res = None
        self.lock = threading.Lock()
        self.last_log_time = 0
        self.last_upload_time = 0
        self.violation_count = 0
        self.violation_details = []
        self.last_email_time = 0

state = State()

# ===============================
# LOGGING
# ===============================
last_log_msg = None
last_log_time_global = 0

def log(level, message):
    global last_log_msg, last_log_time_global
    now = datetime.now().strftime("%H:%M:%S")
    msg = f"{now} || {level.center(7)} || {message}"
    
    # Deduplicate & cooldown
    current_time = time.time()
    if msg == last_log_msg and current_time - last_log_time_global < 0.1:
        return
    last_log_msg = msg
    last_log_time_global = current_time
    
    print(msg)

    try:
        log_socket.send_string(msg, zmq.NOBLOCK)
    except:
        pass

# ===============================
# THROTTLED PPE LOGGING
# ===============================
def log_missing(missing):
    if not missing:
        return

    now = time.time()
    if now - state.last_log_time < LOG_COOLDOWN:
        return

    log("VIOLATION", f"PPE MISSING: {', '.join(missing)}")
    state.last_log_time = now
    state.violation_count += 1


# ===============================
# CAMERA HEALTH CHECK
# ===============================
cap = None
def init_camera():
    global cap
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap.isOpened()

def check_camera():
    global cap
    if cap is None or not cap.isOpened():
        log("WARN", "Camera reconnecting...")
        cap.release()
        return init_camera()
    return True

# ===============================
# AWS SCAN
# ===============================
def perform_ai_scan(image_bytes):
    try:
        response = rekognition.detect_protective_equipment(
            Image={'Bytes': image_bytes},
            SummarizationAttributes={
                'MinConfidence': CONFIDENCE_LEVEL,
                'RequiredEquipmentTypes': [
                    'HEAD_COVER',
                    'FACE_COVER',
                    'HAND_COVER'
                ]
            }
        )

        summary = response.get("Summary", {})

        # S3 UPLOAD VIOLATIONS (idempotent)
        if summary.get("PersonsWithoutRequiredEquipment"):
            now = time.time()
            if now - state.last_upload_time > UPLOAD_COOLDOWN:
                file_name = f"violation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                try:
                    # Check if exists
                    try:
                        s3_client.head_object(Bucket=IMAGE_BUCKET, Key=file_name)
                        log("INFO", f"S3 exists: {file_name}")
                    except s3_client.exceptions.ClientError:
                        # Upload if not exists
                        s3_client.put_object(
                            Bucket=IMAGE_BUCKET,
                            Key=file_name,
                            Body=image_bytes,
                            ContentType='image/jpeg'
                        )
                        log("CLOUD", f"UPLOADED {file_name}")
                    state.last_upload_time = now
                except Exception as e:
                    log("ERROR", f"S3 failed: {str(e)[:60]}")

        with state.lock:
            state.latest_res = response

    except Exception as e:
        log("ERROR", f"AWS Rekognition failed: {str(e)[:60]}")

# ===============================
# PPE ANALYSIS
# ===============================
def analyze_ppe(person):
    results = {
        "HEAD_COVER": 0,
        "FACE_COVER": 0,
        "HAND_COVER": 0
    }

    for bp in person.get("BodyParts", []):
        name = bp.get("Name")

        if name == "HEAD":
            etype = "HEAD_COVER"
        elif name == "FACE":
            etype = "FACE_COVER"
        elif name in ["LEFT_HAND", "RIGHT_HAND"]:
            etype = "HAND_COVER"
        else:
            continue

        best = 0
        for eq in bp.get("EquipmentDetections", []):
            if eq.get("Type") == etype:
                best = max(best, eq.get("Confidence", 0))

        results[etype] = best

    return results

# ===============================
# MAIN LOOP
# ===============================
if not init_camera():
    log("ERROR", "Failed to initialize camera")
    exit(1)

last_ai = 0
log("START", "PPE System Online - PRODUCTION MODE")

try:
    while True:
        if not check_camera():
            time.sleep(1)
            continue

        ret, frame = cap.read()
        if not ret:
            log("ERROR", "Failed to read frame")
            time.sleep(1)
            continue

        h, w, _ = frame.shape
        now = time.time()

        # ENCODE & STREAM FRAME
        _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        image_bytes = buf.tobytes()

        try:
            footage_socket.send(image_bytes, zmq.NOBLOCK)
        except:
            pass

        # AI SCAN (THROTTLED)
        if now - last_ai > AI_SCAN_INTERVAL:
            # Reload threshold before scan
            try:
                import pandas as pd
                threshold_df = pd.read_csv('threshold.csv')
                CONFIDENCE_LEVEL = int(threshold_df['confidence'].iloc[0])
                print(f"Live threshold update: {CONFIDENCE_LEVEL}%")
            except:
                pass
            
            # SNS email every 10min if violations
            if SNS_TOPIC_ARN and state.violation_count > 0 and now - state.last_email_time > 600:  # 10min
                summary = f"PPE Violations: {state.violation_count}\\nDetails:\\n" + '\\n'.join(state.violation_details[-10:])
                try:
                    sns.publish(
                        TopicArn=SNS_TOPIC_ARN,
                        Subject=f"PPE Alert - {state.violation_count} violations",
                        Message=summary
                    )
                    log("ALERT", f"SNS email sent: {state.violation_count} violations")
                except Exception as e:
                    log("ERROR", f"SNS failed: {str(e)}")
                state.violation_count = 0
                state.violation_details = []
                state.last_email_time = now
            
            executor.submit(perform_ai_scan, image_bytes)
            last_ai = now

        # RENDER PPE STATUS
        with state.lock:
            res = state.latest_res

        if res:
            persons = res.get("Persons", [])
            for p in persons:
                conf = analyze_ppe(p)

                head = conf["HEAD_COVER"]
                face = conf["FACE_COVER"]
                hand = conf["HAND_COVER"]

                missing = []
                if head < CONFIDENCE_LEVEL: missing.append("HEAD")
                if face < CONFIDENCE_LEVEL: missing.append("FACE")
                if hand < CONFIDENCE_LEVEL: missing.append("HAND")

                log_missing(missing)

                # PERSON BOX
                box = p['BoundingBox']
                x1 = int(box['Left'] * w)
                y1 = int(box['Top'] * h)
                x2 = int((box['Left'] + box['Width']) * w)
                y2 = int((box['Top'] + box['Height']) * h)

                cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2)
                cv2.putText(frame, "PERSON", (x1, y1 - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)

                # DASHBOARD
                cv2.putText(frame, f"HEAD: {int(head)}%", (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                           (0,255,0) if head >= CONFIDENCE_LEVEL else (0,0,255), 2)
                cv2.putText(frame, f"FACE: {int(face)}%", (10, 60),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                           (0,255,0) if face >= CONFIDENCE_LEVEL else (0,0,255), 2)
                cv2.putText(frame, f"HAND: {int(hand)}%", (10, 90),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                           (0,255,0) if hand >= CONFIDENCE_LEVEL else (0,0,255), 2)

        cv2.imshow("PPE System - PRODUCTION", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    if cap:
        cap.release()
    cv2.destroyAllWindows()
    executor.shutdown(wait=True)
    footage_socket.close()
    log_socket.close()
    context.term()
    log("STOP", "System shutdown complete")