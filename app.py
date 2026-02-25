from flask import Flask, render_template, Response
import cv2
import threading

app = Flask(__name__)
cap = cv2.VideoCapture(0)  # Open webcam

def gen_frames():
    while True:
        success, frame = cap.read()
        if not success:
            break
        else:
            # Here you can add your PPE detection logic
            # draw bounding boxes, overlay text, etc.

            _, buffer = cv2.imencode('.jpg', frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/video_feed')
def video_feed():
    return Response(gen_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)