import cv2
import boto3
import threading
import time
from datetime import datetime

# ===============================
# CONFIGURATION
# ===============================
BUCKET_NAME = "ppe-detection-image"
PROCESS_INTERVAL = 1
UPLOAD_INTERVAL = 5
CONFIDENCE = 80
VIOLATION_DURATION = 3  # seconds before confirming violation

rekognition = boto3.client('rekognition')
s3 = boto3.client('s3')

# ===============================
# CAMERA SETUP
# ===============================
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print("Camera not accessible")
    exit()

cv2.namedWindow("LIVE PPE SYSTEM - ALERTS", cv2.WINDOW_NORMAL)
cv2.setWindowProperty("LIVE PPE SYSTEM - ALERTS", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

print("Starting LIVE PPE system with ALERTS... Press Q to quit.")

# ===============================
# STATE VARIABLES
# ===============================
last_upload_time = 0
last_process_time = 0
latest_response = None
lock = threading.Lock()
violation_start_time = None
persistent_missing_items = None

# ===============================
# BACKGROUND REKOGNITION THREAD
# ===============================
def detect_ppe(image_bytes):
    global latest_response
    try:
        response = rekognition.detect_protective_equipment(
            Image={'Bytes': image_bytes},
            SummarizationAttributes={
                'MinConfidence': CONFIDENCE,
                'RequiredEquipmentTypes': [
                    'HEAD_COVER',
                    'FACE_COVER',
                    'HAND_COVER'
                ]
            }
        )
        with lock:
            latest_response = response
    except Exception as e:
        print("Rekognition error:", e)

# ===============================
# MAIN LOOP
# ===============================
while True:
    ret, frame = cap.read()
    if not ret:
        break

    height, width, _ = frame.shape
    current_time = time.time()

    # ---- Trigger Rekognition asynchronously ----
    if current_time - last_process_time > PROCESS_INTERVAL and not lock.locked():
        _, buffer = cv2.imencode('.jpg', frame)
        image_bytes = buffer.tobytes()
        threading.Thread(target=detect_ppe, args=(image_bytes,), daemon=True).start()
        last_process_time = current_time

    # ---- Draw latest detection results ----
    helmet_status = "Unknown"
    mask_status = "Unknown"
    gloves_status = "Unknown"
    violation_detected = False
    missing_items = []

    with lock:
        response = latest_response

    if response:
        persons = response.get('Persons', [])
        if not persons:
            # No person detected logic could go here if needed
            pass

        for person in persons:
            has_helmet = has_mask = has_gloves = False

            # Draw person box
            box = person['BoundingBox']
            left = int(box['Left'] * width)
            top = int(box['Top'] * height)
            w = int(box['Width'] * width)
            h = int(box['Height'] * height)
            cv2.rectangle(frame, (left, top), (left + w, top + h), (255, 0, 0), 2)
            cv2.putText(frame, "Person", (left, top - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)

            # Check PPE and draw boxes
            for body_part in person.get('BodyParts', []):
                for eq in body_part.get('EquipmentDetections', []):
                    eq_box = eq.get('BoundingBox')
                    if not eq_box:
                        continue
                    e_left = int(eq_box['Left'] * width)
                    e_top = int(eq_box['Top'] * height)
                    e_w = int(eq_box['Width'] * width)
                    e_h = int(eq_box['Height'] * height)

                    # Determine missing PPE
                    is_missing = False
                    if eq['Type'] == 'HEAD_COVER' and eq['Confidence'] < CONFIDENCE:
                        is_missing = True
                        has_helmet = False
                    elif eq['Type'] == 'HEAD_COVER':
                        has_helmet = True

                    if eq['Type'] == 'FACE_COVER' and eq['Confidence'] < CONFIDENCE:
                        is_missing = True
                        has_mask = False
                    elif eq['Type'] == 'FACE_COVER':
                        has_mask = True

                    if eq['Type'] == 'HAND_COVER' and eq['Confidence'] < CONFIDENCE:
                        is_missing = True
                        has_gloves = False
                    elif eq['Type'] == 'HAND_COVER':
                        has_gloves = True

                    box_color = (0, 0, 255) if is_missing else (0, 255, 0)
                    cv2.rectangle(frame, (e_left, e_top), (e_left + e_w, e_top + e_h), box_color, 2)
                    label = f"{eq['Type']} ({int(eq['Confidence'])}%)"
                    cv2.putText(frame, label, (e_left, e_top - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)

            # ---- Prepare missing PPE list per person ----
            missing_items = []
            if not has_helmet:
                missing_items.append("Helmet")
            if not has_mask:
                missing_items.append("Mask")
            if not has_gloves:
                missing_items.append("Gloves")

            # ---- Check 3-second violation ----
            if missing_items:
                if violation_start_time is None:
                    violation_start_time = current_time
                elif current_time - violation_start_time >= VIOLATION_DURATION:
                    violation_detected = True
                    persistent_missing_items = missing_items.copy()
            else:
                violation_start_time = None
                persistent_missing_items = None

            # ---- Update top-left text ----
            helmet_status = "Helmet: OK" if has_helmet else "Helmet: MISSING"
            mask_status = "Mask: OK" if has_mask else "Mask: MISSING"
            gloves_status = "Gloves: OK" if has_gloves else "Gloves: MISSING"

    # ---- Draw top-left status ----
    color_ok = (0, 255, 0)
    color_missing = (0, 0, 255)
    cv2.putText(frame, helmet_status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_ok if "OK" in helmet_status else color_missing, 2)
    cv2.putText(frame, mask_status, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_ok if "OK" in mask_status else color_missing, 2)
    cv2.putText(frame, gloves_status, (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color_ok if "OK" in gloves_status else color_missing, 2)

    # ---- Upload frame to S3 asynchronously ----
    if current_time - last_upload_time > UPLOAD_INTERVAL:
        _, buffer = cv2.imencode('.jpg', frame)
        filename = datetime.utcnow().strftime("frame_%Y%m%d_%H%M%S.jpg")
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

        # Optional: record violations per frame (persistent_missing_items)
        if violation_detected and persistent_missing_items:
            print(f"Violation detected in frame {filename}: Missing {persistent_missing_items}")

    # ---- Show frame ----
    cv2.imshow("LIVE PPE SYSTEM - ALERTS", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()